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
    """Reproduces vMix's /api: readable state + Function=Merge that changes it."""

    def __init__(self, active: int = 2, drop_first: int = 0):
        self.active = active
        self.drop_first = drop_first  # how many commands to "drop"
        self.received: list[int] = []
        self.applied: list[int] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                q = parse_qs(urlparse(self.path).query)
                if q.get("Function", [""])[0] == "Merge":
                    number = int(q["Input"][0])
                    outer.received.append(number)
                    if outer.drop_first > 0:
                        outer.drop_first -= 1  # dropped command: vMix doesn't change
                    else:
                        outer.active = number
                        outer.applied.append(number)
                body = f"""<vmix><inputs>
                    <input key="k2" number="2" title="APALIT ZOOM FEED"/>
                    <input key="k3" number="3" title="RL VERSES"/>
                    <input key="k8" number="8" title="LEFT"/>
                    <input key="k41" number="41" title="OTHER"/>
                  </inputs><overlays/><preview>0</preview>
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


def run(frames, active_start=2, drop_first=0, managed_override=None):
    cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    mock = MockVmix(active=active_start, drop_first=drop_first)
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

print("\n" + ("ALL OK" if not failures else f"FAILED: {len(failures)} -> {failures}"))
sys.exit(1 if failures else 0)
