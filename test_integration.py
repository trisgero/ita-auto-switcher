"""End-to-end test of the reconciliation loop against a fake vMix.

    .venv\\Scripts\\python.exe test_integration.py

Verifies the three properties the OBS automation lacked:
  1. converges on the right state following the source;
  2. RECOVERS from a dropped command (this is the mechanism that makes it
     impossible to stay stuck on the full verse);
  3. doesn't fight the operator when the program is on an unmanaged input.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import cv2

BASE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(BASE, "__screenshots")
sys.path.insert(0, BASE)

from autoswitch.main import Switcher  # noqa: E402

failures: list[str] = []


def check(label: str, got, expected) -> None:
    ok = got == expected
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}: {got}" + ("" if ok else f"  (expected {expected})"))
    if not ok:
        failures.append(label)


# --------------------------------------------------------------- fake vMix

class MockVmix:
    """Reproduces vMix's /api: readable state + Function=Merge that changes
    it + Function=OverlayInputN that TOGGLES overlay channel N (matching
    real vMix semantics, per the shortcut template's existing Down/S keys)."""

    def __init__(self, active: int = 2, drop_first: int = 0,
                 overlays: dict[int, int | None] | None = None,
                 drop_overlay_first: int = 0):
        self.active = active
        self.drop_first = drop_first  # how many Merge commands to "drop"
        self.received: list[int] = []
        self.applied: list[int] = []

        self.overlays: dict[int, int | None] = dict(overlays or {})
        self.drop_overlay_first = drop_overlay_first
        self.overlay_received: list[tuple[int, int]] = []
        self.overlay_applied: list[tuple[int, int]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                q = parse_qs(urlparse(self.path).query)
                func = q.get("Function", [""])[0]
                if func == "Merge":
                    number = int(q["Input"][0])
                    outer.received.append(number)
                    if outer.drop_first > 0:
                        outer.drop_first -= 1  # dropped command: vMix doesn't change
                    else:
                        outer.active = number
                        outer.applied.append(number)
                elif func.startswith("OverlayInput"):
                    channel = int(func[len("OverlayInput"):])
                    number = int(q["Input"][0])
                    outer.overlay_received.append((channel, number))
                    if outer.drop_overlay_first > 0:
                        outer.drop_overlay_first -= 1
                    else:
                        # real vMix toggles: same input already showing -> OFF
                        current = outer.overlays.get(channel)
                        outer.overlays[channel] = None if current == number else number
                        outer.overlay_applied.append((channel, number))

                overlay_xml = "".join(
                    f'<overlay number="{ch}">k{inp}</overlay>'
                    for ch, inp in outer.overlays.items() if inp is not None
                )
                input_keys = {2, 3, 8, 41, 16, 19}
                inputs_xml = "".join(
                    f'<input key="k{n}" number="{n}" title="INPUT{n}"/>' for n in input_keys
                )
                body = f"""<vmix><inputs>{inputs_xml}
                  </inputs><overlays>{overlay_xml}</overlays><preview>0</preview>
                  <active>{outer.active}</active></vmix>""".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


# ------------------------------------------------------- fake video source

class FakeCapture:
    """Returns a preset sequence of frames, then stops the switcher."""

    def __init__(self, frames, switcher_ref):
        self.frames = frames
        self.i = 0
        self.switcher_ref = switcher_ref
        self.source_size = (1920, 1080)

    def grab(self):
        frame = self.frames[min(self.i, len(self.frames) - 1)]
        self.i += 1
        if self.i >= len(self.frames):
            self.switcher_ref[0].running = False
        return frame

    def close(self):
        pass


def load(name: str, width: int):
    img = cv2.imread(os.path.join(SHOTS, name + ".png"))
    return cv2.resize(img, (width, int(round(img.shape[0] * width / img.shape[1]))),
                      interpolation=cv2.INTER_AREA)


def camera_frame(width: int):
    src = cv2.imread(os.path.join(SHOTS, "left.png"))
    h, w = src.shape[:2]
    crop = src[int(0.06 * h):int(0.36 * h), int(0.64 * w):int(0.95 * w)]
    return cv2.resize(crop, (width, int(round(width * 1080 / 1920))), interpolation=cv2.INTER_LINEAR)


def run(frames, active_start=2, drop_first=0, managed_override=None,
        overlays_start=None, drop_overlay_first=0):
    cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    mock = MockVmix(active=active_start, drop_first=drop_first,
                     overlays=overlays_start, drop_overlay_first=drop_overlay_first)
    cfg["vmix"]["url"] = f"http://127.0.0.1:{mock.port}/api"
    cfg["capture"]["fps"] = 1000          # no waiting in tests
    cfg["timing"]["command_cooldown_s"] = 0
    cfg["logging"]["save_transition_frames"] = False
    if managed_override is not None:
        cfg["managed_inputs"] = managed_override

    ref: list = [None]
    sw = Switcher(cfg, BASE, capture=FakeCapture(frames, ref))
    ref[0] = sw
    sw.run()
    mock.stop()
    return sw, mock


logging.disable(logging.INFO)  # the loop logs a lot; here only the outcome matters
W = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))["capture"]["work_width"]
FULL, LEFT, CAM = load("full", W), load("left", W), camera_frame(W)


def overlay_off_frame(width: int):
    img = cv2.imread(os.path.join(BASE, "testdata_overlays", "houston_off.png"))
    return cv2.resize(img, (width, int(round(img.shape[0] * width / img.shape[1]))),
                      interpolation=cv2.INTER_AREA)


OVERLAYS_OFF = overlay_off_frame(W)

print("\n1. the source drives it: NONE -> FULL -> NONE -> LEFT -> NONE")
sw, mock = run([CAM] * 5 + [FULL] * 8 + [CAM] * 8 + [LEFT] * 8 + [CAM] * 8)
check("final input", mock.active, 2)
check("command sequence", mock.applied, [3, 2, 8, 2])

print("\n2. DROPPED command: vMix ignores the first 2 Merge calls")
sw, mock = run([FULL] * 15, drop_first=2)
check("final input despite 2 dropped commands", mock.active, 3)
check("attempts sent", len(mock.received) >= 3, True)
print(f"         sent {len(mock.received)}, applied {mock.applied}")

print("\n3. the historical bug: full verse removed -> must recover to input 2")
sw, mock = run([FULL] * 8 + [CAM] * 12, drop_first=0)
check("final input", mock.active, 2)

print("\n4. same case but the recovery command is dropped")
sw, mock = run([FULL] * 8 + [CAM] * 15, drop_first=0)
check("final input", mock.active, 2)
sw, mock = run([CAM] * 20, active_start=3, drop_first=3)
check("starts stuck on 3, 3 dropped commands -> recovers", mock.active, 2)
print(f"         sent {len(mock.received)} attempts before succeeding")

print("\n5. hands-off: program on input 41 (unmanaged), source idle")
sw, mock = run([CAM] * 15, active_start=41)
check("no command sent", mock.received, [])
check("program left where it was", mock.active, 41)

print("\n6. hands-off, then the source changes -> the automation resumes")
sw, mock = run([CAM] * 6 + [FULL] * 10, active_start=41)
check("resumes and goes to 3", mock.active, 3)

print("\n7. overlays start OFF, source shows both ON (real FULL frame) -> both get toggled on")
sw, mock = run([FULL] * 10, overlays_start={2: None, 8: None})
check("lower_third turned on (channel 2 -> input 19)", mock.overlays.get(2), 19)
check("lis_box turned on (channel 8 -> input 16)", mock.overlays.get(8), 16)

print("\n8. overlays start ON, source shows neither (real Houston frame) -> both get toggled off")
sw, mock = run([OVERLAYS_OFF] * 10, overlays_start={2: 19, 8: 16})
check("lower_third turned off", mock.overlays.get(2), None)
check("lis_box turned off", mock.overlays.get(8), None)

print("\n9. DROPPED overlay command: recovers same as the historical Merge bug")
sw, mock = run([FULL] * 15, overlays_start={2: None, 8: None}, drop_overlay_first=3)
check("lower_third eventually turned on despite 3 dropped toggles", mock.overlays.get(2), 19)
check("lis_box eventually turned on despite 3 dropped toggles", mock.overlays.get(8), 16)
print(f"         overlay toggles sent: {len(mock.overlay_received)}, applied: {mock.overlay_applied}")

print("\n10. overlays are independent of the verse-card state: FULL -> nothing (verse-only content) "
      "must NOT flip overlays that are already correctly on")
sw, mock = run([FULL] * 10, overlays_start={2: 19, 8: 16})
check("overlay toggle commands sent (should be none, already correct)", mock.overlay_received, [])

print("\n" + ("ALL OK" if not failures else f"FAILED: {len(failures)} -> {failures}"))
sys.exit(1 if failures else 0)
