"""Detection regression tests. No pytest dependency:

    .venv\\Scripts\\python.exe test_detection.py

Covers the real screenshots, the "no verse" case (full-screen camera), and -
most importantly - the RECOVERY from FULL, i.e. exactly the bug that used to
leave the OBS automation stuck on the full verse.
"""

from __future__ import annotations

import json
import os
import sys

import cv2
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(BASE, "__screenshots")
sys.path.insert(0, BASE)

from autoswitch.detect import Debouncer, Detector  # noqa: E402
from autoswitch.regression import state_from_filename  # noqa: E402

cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
WORK_W = int(cfg["capture"]["work_width"])

failures: list[str] = []


def check(label: str, got, expected) -> None:
    ok = got == expected
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}: {got}" + ("" if ok else f"  (expected {expected})"))
    if not ok:
        failures.append(label)


def load(name: str) -> np.ndarray | None:
    img = cv2.imread(os.path.join(SHOTS, name + ".png"))
    if img is None:
        return None
    h = int(round(img.shape[0] * WORK_W / img.shape[1]))
    return cv2.resize(img, (WORK_W, h), interpolation=cv2.INTER_AREA)


def fake_camera_fullscreen() -> np.ndarray:
    """Crops a real camera feed out of left.png and blows it up full-screen.

    Approximates the "no verse" state: real pixels, not synthetic ones.
    """
    src = cv2.imread(os.path.join(SHOTS, "left.png"))
    h, w = src.shape[:2]
    cam = src[int(0.06 * h):int(0.36 * h), int(0.64 * w):int(0.95 * w)]
    out = cv2.resize(cam, (WORK_W, int(round(WORK_W * 1080 / 1920))), interpolation=cv2.INTER_LINEAR)
    return out


def dark_room_camera() -> np.ndarray:
    """A very dark but NOT uniform camera feed: the case that would fool a
    brightness-only threshold. Must stay NONE thanks to the max_std constraint."""
    cam = fake_camera_fullscreen().astype(np.float32) * 0.22
    return np.clip(cam, 0, 255).astype(np.uint8)


det = Detector(cfg["detector"], BASE)

print("\n1. real screenshots (every .png in __screenshots must give the layout named in its filename)")
shots = sorted(f for f in os.listdir(SHOTS) if f.lower().endswith(".png")) if os.path.isdir(SHOTS) else []
for fname in shots:
    expected = state_from_filename(fname)
    if expected not in cfg["states"]:
        print(f"  [SKIP] {fname}: '{expected}' is not a state in config.states")
        continue
    img = load(os.path.splitext(fname)[0])
    det.reset()
    check(fname, det.read(img).state, expected)

print("\n1b. every layout must match EXACTLY ONE rule (evaluation order must not be load-bearing)")
for fname in shots:
    expected = state_from_filename(fname)
    if expected not in cfg["states"]:
        continue
    det.reset()
    det.read(load(os.path.splitext(fname)[0]))
    matched = [
        r["state"] for r in det.rules
        if all(det._hot.get(n, False) for n in r.get("hot", []))
        and not any(det._hot.get(n, False) for n in r.get("cold", []))
    ]
    check(f"{fname} matching rules", matched, [expected])

print("\n1c. out-of-sample cases (testdata/<STATE>_<note>.png)")
TESTDATA = os.path.join(BASE, "testdata")
extra = sorted(f for f in os.listdir(TESTDATA) if f.lower().endswith(".png")) if os.path.isdir(TESTDATA) else []
if not extra:
    print("  [SKIP] no file in testdata/")
for fname in extra:
    expected = state_from_filename(fname)
    img = cv2.imread(os.path.join(TESTDATA, fname))
    img = cv2.resize(img, (WORK_W, int(round(img.shape[0] * WORK_W / img.shape[1]))),
                     interpolation=cv2.INTER_AREA)
    det.reset()
    check(fname, det.read(img).state, expected)

print("\n2. no verse -> NONE")
det.reset()
check("full-screen camera", det.read(fake_camera_fullscreen()).state, "NONE")
det.reset()
r = det.read(dark_room_camera())
check("dark camera (non-uniform)", r.state, "NONE")
print("         measured std: " + "  ".join(f"{k}={v:.1f}" for k, v in r.stds.items()))

print("\n3. RECOVERY from FULL (the OBS pixel matcher bug)")
full, cam = load("full"), fake_camera_fullscreen()
if full is None:
    print("  [SKIP] full.png is required")
else:
    det.reset()
    db = Debouncer(int(cfg["timing"]["confirm_frames"]))
    for _ in range(10):
        db.update(det.read(full).state)
    check("after 10 frames of full", db.stable, "FULL")

    states = [db.update(det.read(cam).state) for _ in range(10)]
    check("after 10 frames with no verse", db.stable, "NONE")
    print(f"         sequence: {' '.join(states)}")

    # A single glitchy frame must not drop the state.
    det.reset()
    db = Debouncer(int(cfg["timing"]["confirm_frames"]))
    for _ in range(10):
        db.update(det.read(full).state)
    db.update(det.read(cam).state)
    check("1 glitchy frame during full", db.stable, "FULL")

print("\n4. state -> vMix input mapping")
for state, expected in (("NONE", 2), ("FULL", 3), ("LEFT", 8),
                        ("BIG_LEFT", 49), ("DUAL", 2), ("TRIPLE", 2)):
    check(f"{state}", cfg["states"][state], expected)

# Every commandable state must be among managed_inputs, otherwise the
# automation would send the input and then immediately go hands-off.
for state, number in cfg["states"].items():
    if state.startswith("_"):
        continue
    check(f"{state} -> input {number} is in managed_inputs",
          number in cfg["managed_inputs"], True)

print("\n" + ("ALL OK" if not failures else f"FAILED: {len(failures)} -> {failures}"))
sys.exit(1 if failures else 0)
