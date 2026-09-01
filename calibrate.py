"""Calibration tool: thresholds are read from the data, not guessed.

Typical usage:

    python calibrate.py --monitors          list monitors to pick the right one
    python calibrate.py --shot full.png     save a screenshot of the configured monitor
    python calibrate.py                     live viewer: ROIs drawn + metrics + state
    python calibrate.py --rois              redraw the ROIs with the mouse and write them to config
    python calibrate.py --save-ref          save the background reference for the template gate
    python calibrate.py --image full.png    test the current ROIs on a saved screenshot

In the live viewer: make the verse appear and disappear live and watch the
numbers. The 'on' threshold goes at about 1/3 between the resting value and
the value with text; 'off' at about 1/5. With this metric the gap is wide,
precision isn't needed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from autoswitch.capture import ScreenCapture  # noqa: E402
from autoswitch.detect import Detector, _slice  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "config.json")


def load_cfg() -> dict:
    with open(CONFIG, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_cfg(cfg: dict) -> None:
    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def make_capture(cfg: dict) -> ScreenCapture:
    cap = cfg["capture"]
    return ScreenCapture(
        monitor=int(cap.get("monitor", 1)),
        region=cap.get("region"),
        work_width=int(cap.get("work_width", 480)),
    )


def annotate(frame: np.ndarray, detector: Detector, reading) -> np.ndarray:
    view = frame.copy()
    h, w = view.shape[:2]
    for name, roi in detector.rois.items():
        x, y, bw, bh = roi["box"]
        p0 = (int(x * w), int(y * h))
        p1 = (int((x + bw) * w), int((y + bh) * h))
        hot = reading.hot.get(name, False)
        color = (0, 220, 0) if hot else (60, 60, 200)
        cv2.rectangle(view, p0, p1, color, 2)
        label = f"{name} {reading.metrics.get(name, 0):.2f} on={roi['on']} off={roi['off']}"
        if roi.get("max_std") is not None:
            label += f" sd={reading.stds.get(name, 0):.0f}/{roi['max_std']}"
        cv2.putText(view, label, (p0[0] + 4, p0[1] + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.38, color, 1)

    if detector.gate_enabled and detector.gate_box:
        x, y, bw, bh = detector.gate_box
        cv2.rectangle(view, (int(x * w), int(y * h)), (int((x + bw) * w), int((y + bh) * h)),
                      (0, 200, 200) if reading.gate_open else (0, 0, 255), 1)

    banner = f"STATE: {reading.state}"
    if reading.gate_diff is not None:
        banner += f"   gate_diff={reading.gate_diff:.1f} (max {detector.gate_max_diff})"
    cv2.rectangle(view, (0, 0), (w, 22), (0, 0, 0), -1)
    cv2.putText(view, banner, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return view


def cmd_monitors() -> int:
    for i, mon in enumerate(ScreenCapture.list_monitors()):
        tag = "  <- 'all combined', don't use" if i == 0 else ""
        print(f"monitor {i}: {mon}{tag}")
    return 0


def cmd_shot(cfg: dict, path: str) -> int:
    cap = make_capture(cfg)
    cap.work_width = cap.source_size[0]  # full-resolution screenshot
    frame = cap.grab()
    cap.close()
    cv2.imwrite(path, frame)
    print(f"saved {path}  ({frame.shape[1]}x{frame.shape[0]})")
    return 0


def cmd_save_ref(cfg: dict) -> int:
    gate = cfg["detector"]["template_gate"]
    cap = make_capture(cfg)
    frame = cap.grab()
    cap.close()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    patch = _slice(gray, gate["box"])
    out = os.path.join(BASE, gate.get("reference", "ref_bg.png"))
    cv2.imwrite(out, patch)
    print(f"background reference saved to {out} ({patch.shape[1]}x{patch.shape[0]})")
    print("Remember: set \"enabled\": true in the config's template_gate.")
    return 0


def cmd_rois(cfg: dict) -> int:
    cap = make_capture(cfg)
    frame = cap.grab()
    cap.close()
    h, w = frame.shape[:2]
    rois = {k: v for k, v in cfg["detector"]["rois"].items() if not k.startswith("_")}
    for name in rois:
        print(f"Draw the ROI '{name}' with the mouse, then ENTER. ESC to skip it.")
        box = cv2.selectROI(f"ROI: {name}", frame, showCrosshair=True)
        cv2.destroyAllWindows()
        if box[2] == 0 or box[3] == 0:
            print("  skipped")
            continue
        x, y, bw, bh = box
        rois[name]["box"] = [round(x / w, 4), round(y / h, 4), round(bw / w, 4), round(bh / h, 4)]
        print(f"  {name} -> {rois[name]['box']}")
    for name, roi in rois.items():
        cfg["detector"]["rois"][name] = roi
    save_cfg(cfg)
    print(f"config updated: {CONFIG}")
    return 0


def _load_scaled(cfg: dict, path: str):
    img = cv2.imread(path)
    if img is None:
        return None
    work_width = int(cfg["capture"].get("work_width", 480))
    scale = work_width / img.shape[1]
    return cv2.resize(img, (work_width, int(round(img.shape[0] * scale))), interpolation=cv2.INTER_AREA)


def cmd_image(cfg: dict, paths: list[str], show: bool) -> int:
    detector = Detector(cfg["detector"], BASE)
    failures = 0
    for path in paths:
        img = _load_scaled(cfg, path)
        if img is None:
            print(f"could not read {path}")
            failures += 1
            continue
        detector.reset()  # each image is independent: no hysteresis across files
        reading = detector.read(img)

        # If the file is named after a state (full.png, left.png...) use it
        # as the expected one, so the command doubles as a regression test.
        expected = os.path.splitext(os.path.basename(path))[0].upper()
        verdict = ""
        if expected in cfg["states"]:
            ok = reading.state == expected
            verdict = "  OK" if ok else f"  <-- EXPECTED {expected}"
            failures += 0 if ok else 1

        print(f"\n{os.path.basename(path):12s} ->  {reading.state}{verdict}")
        for name, value in reading.metrics.items():
            roi = detector.rois[name]
            extra = ""
            if roi.get("max_std") is not None:
                extra = f"  std={reading.stds[name]:6.1f} (max {roi['max_std']})"
            mark = "HOT " if reading.hot[name] else "cold"
            print(f"   {name:11s} {value:5.3f}  on={roi['on']:<5} off={roi['off']:<5} {mark}{extra}")
        if reading.gate_diff is not None:
            print(f"   gate_diff  {reading.gate_diff:5.1f}  max={detector.gate_max_diff}  open={reading.gate_open}")

        if show:
            cv2.imshow(os.path.basename(path), annotate(img, detector, reading))
            cv2.waitKey(0)
            cv2.destroyAllWindows()
    return 1 if failures else 0


def cmd_live(cfg: dict) -> int:
    cap = make_capture(cfg)
    detector = Detector(cfg["detector"], BASE)
    print("Live viewer. 'q' to quit, 's' to save the current frame.")
    try:
        while True:
            frame = cap.grab()
            reading = detector.read(frame)
            cv2.imshow("calibrate  (q=quit, s=save frame)", annotate(frame, detector, reading))
            key = cv2.waitKey(30) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                name = f"frame-{reading.state}.png"
                cv2.imwrite(os.path.join(BASE, name), frame)
                print(f"saved {name}")
    finally:
        cap.close()
        cv2.destroyAllWindows()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ROI and threshold calibration")
    parser.add_argument("--monitors", action="store_true", help="list the monitors")
    parser.add_argument("--shot", metavar="FILE", help="save a full-resolution screenshot")
    parser.add_argument("--rois", action="store_true", help="redraw the ROIs with the mouse")
    parser.add_argument("--save-ref", action="store_true", help="save the background reference")
    parser.add_argument("--image", metavar="FILE", nargs="+", help="test the ROIs on saved screenshots")
    parser.add_argument("--show", action="store_true", help="with --image, also show the drawn ROIs")
    args = parser.parse_args()

    if args.monitors:
        return cmd_monitors()
    cfg = load_cfg()
    if args.shot:
        return cmd_shot(cfg, args.shot)
    if args.save_ref:
        return cmd_save_ref(cfg)
    if args.rois:
        return cmd_rois(cfg)
    if args.image:
        return cmd_image(cfg, args.image, args.show)
    return cmd_live(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
