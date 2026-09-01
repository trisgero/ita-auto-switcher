"""Main loop: capture -> classify -> reconcile with vMix.

The central point of the design is that this is NOT an event generator. On
every tick it compares the desired state with the ACTUAL state read from
vMix, and if they diverge, sends the correction. If a command is dropped, the
next tick resends it. By construction there is no "stuck" state.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time

import cv2

from .capture import ScreenCapture
from .detect import Debouncer, Detector
from .paths import base_dir as resolve_base_dir
from .paths import ensure_config
from .vmix import VmixClient, VmixError

log = logging.getLogger("autoswitch")


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def setup_logging(base_dir: str, verbose: bool) -> None:
    os.makedirs(os.path.join(base_dir, "logs"), exist_ok=True)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    fh = logging.FileHandler(os.path.join(base_dir, "logs", "autoswitch.log"), encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
    root.addHandler(fh)


class Switcher:
    def __init__(self, cfg: dict, base_dir: str, dry_run: bool = False, capture=None):
        self.cfg = cfg
        self.base_dir = base_dir
        self.dry_run = dry_run

        self.states = {k: int(v) for k, v in cfg["states"].items() if not k.startswith("_")}
        self.managed = set(cfg["managed_inputs"])
        for name, number in self.states.items():
            if number not in self.managed:
                log.warning("state %s -> input %s is not in managed_inputs", name, number)

        timing = cfg["timing"]
        self.cooldown = float(timing["command_cooldown_s"])
        self.warn_after = int(timing.get("resync_warn_after", 5))

        cap_cfg = cfg["capture"]
        # injectable capture: tests replace it with a fake source
        self.capture = capture or ScreenCapture(
            monitor=int(cap_cfg.get("monitor", 1)),
            region=cap_cfg.get("region"),
            work_width=int(cap_cfg.get("work_width", 480)),
        )
        self.detector = Detector(cfg["detector"], base_dir)
        self.debouncer = Debouncer(int(timing["confirm_frames"]))
        self.vmix = VmixClient(**{k: v for k, v in cfg["vmix"].items() if not k.startswith("_")})

        self.period = 1.0 / float(cfg["capture"].get("fps", 8))
        self._last_command = 0.0
        self._corrections = 0
        self._hands_off = False
        self._hands_off_state = "NONE"
        self._last_logged_state = None
        self._vmix_down_since: float | None = None

        lg = cfg.get("logging", {})
        self.save_frames = bool(lg.get("save_transition_frames", False))
        self.frames_dir = os.path.join(base_dir, lg.get("frames_dir", "logs/frames"))
        self.max_frames = int(lg.get("max_saved_frames", 500))
        if self.save_frames:
            os.makedirs(self.frames_dir, exist_ok=True)

        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    # ------------------------------------------------------------------ utility

    def _dump_frame(self, frame, state: str) -> None:
        if not self.save_frames:
            return
        name = time.strftime("%Y%m%d-%H%M%S") + f"-{state}.png"
        cv2.imwrite(os.path.join(self.frames_dir, name), frame)
        files = sorted(os.listdir(self.frames_dir))
        for stale in files[: max(0, len(files) - self.max_frames)]:
            try:
                os.remove(os.path.join(self.frames_dir, stale))
            except OSError:
                pass

    # --------------------------------------------------------------- reconcile

    def reconcile(self, desired: str) -> None:
        target = self.states.get(desired)
        if target is None:
            log.error("state %s is not mapped in config.states", desired)
            return

        try:
            vstate = self.vmix.state()
        except VmixError as exc:
            if self._vmix_down_since is None:
                self._vmix_down_since = time.time()
                log.error("vMix unreachable (%s). Retrying every tick.", exc)
            return
        if self._vmix_down_since is not None:
            log.info("vMix reachable again after %.1fs", time.time() - self._vmix_down_since)
            self._vmix_down_since = None

        active = vstate.active

        # Hands-off: if the operator moved the program to an input we don't
        # manage, don't fight it. We resume only when it returns to our set,
        # or when the source itself changes state (= a genuine new event).
        if active not in self.managed:
            if not self._hands_off:
                self._hands_off = True
                self._hands_off_state = desired
                log.info(
                    "HANDS-OFF: program on input %s (%s), not managed. Automation suspended.",
                    active,
                    vstate.inputs.get(active, "?"),
                )
            elif desired != self._hands_off_state:
                self._hands_off = False
                log.info("RESUMING: the source changed to %s.", desired)
            if self._hands_off:
                return
        elif self._hands_off:
            self._hands_off = False
            log.info("RESUMING: program back on a managed input (%s).", active)

        if active == target:
            if self._corrections:
                log.debug("aligned on %s (input %s)", desired, target)
            self._corrections = 0
            return

        now = time.time()
        if now - self._last_command < self.cooldown:
            return

        self._corrections += 1
        if self._corrections == self.warn_after:
            log.warning(
                "input %s requested %s times but vMix stays on %s. Check shortcuts/transitions.",
                target,
                self._corrections,
                active,
            )
        log.info("Merge -> input %s (%s)   [program was %s]", target, desired, active)
        if not self.dry_run:
            try:
                self.vmix.merge(target)
            except VmixError as exc:
                log.error("%s", exc)
                return
        self._last_command = now

    # -------------------------------------------------------------------- loop

    def run(self) -> None:
        log.info(
            "starting  |  capture %sx%s  |  states: %s  |  %s",
            *self.capture.source_size,
            self.states,
            "DRY-RUN (no commands to vMix)" if self.dry_run else "live",
        )
        while self.running:
            tick = time.time()
            frame = self.capture.grab()
            reading = self.detector.read(frame)
            desired = self.debouncer.update(reading.state)

            if desired != self._last_logged_state:
                metrics = "  ".join(f"{k}={v:.4f}" for k, v in reading.metrics.items())
                log.info("STATE: %s -> %s   (%s)", self._last_logged_state, desired, metrics)
                self._dump_frame(frame, desired)
                self._last_logged_state = desired

            self.reconcile(desired)

            sleep = self.period - (time.time() - tick)
            if sleep > 0:
                time.sleep(sleep)

        self.capture.close()
        log.info("stopped.")


def main(argv: list[str] | None = None) -> int:
    base_dir = resolve_base_dir()
    default_config = ensure_config(base_dir)
    parser = argparse.ArgumentParser(description="vMix verse auto-switcher")
    parser.add_argument("-c", "--config", default=default_config)
    parser.add_argument("--dry-run", action="store_true", help="detect and log without commanding vMix")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(base_dir, args.verbose)
    switcher = Switcher(load_config(args.config), base_dir, args.dry_run)
    signal.signal(signal.SIGINT, switcher.stop)
    signal.signal(signal.SIGTERM, switcher.stop)
    switcher.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
