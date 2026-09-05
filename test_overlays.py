"""Regression tests for the two independent overlay detectors (lower third,
sign-language box). Separate from test_detection.py because these are not
verse-card states - they're independent on/off toggles that can be present
or absent regardless of what the verse-card detector sees.

    .venv\\Scripts\\python.exe test_overlays.py

ONLY ONE real "OFF" example exists so far (testdata_overlays/houston_off.png).
Grow this suite the same way testdata/ grew for the verse-card detector:
whenever a lower third or LIS box is confirmed on/off in a real frame, save
it here and add a case below.
"""

from __future__ import annotations

import json
import os
import sys

import cv2

BASE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(BASE, "__screenshots")
TESTDATA = os.path.join(BASE, "testdata")
TESTDATA_OVERLAYS = os.path.join(BASE, "testdata_overlays")
sys.path.insert(0, BASE)

from autoswitch.overlays import OverlayDetector  # noqa: E402

cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
WORK_W = int(cfg["capture"]["work_width"])

failures: list[str] = []


def check(label: str, got, expected) -> None:
    ok = got == expected
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}: {got}" + ("" if ok else f"  (expected {expected})"))
    if not ok:
        failures.append(label)


def load(path: str):
    img = cv2.imread(path)
    if img is None:
        return None
    h = int(round(img.shape[0] * WORK_W / img.shape[1]))
    return cv2.resize(img, (WORK_W, h), interpolation=cv2.INTER_AREA)


det = OverlayDetector(cfg["overlays"])

# (path, expected lis_box hot, expected lower_third hot, expected centered_lower_third hot)
CASES = [
    (os.path.join(SHOTS, "full.png"), True, True, False),
    (os.path.join(SHOTS, "left.png"), True, True, False),
    (os.path.join(SHOTS, "big-left.png"), True, True, False),
    (os.path.join(SHOTS, "dual.png"), True, True, False),
    (os.path.join(SHOTS, "triple.png"), True, True, False),
    (os.path.join(TESTDATA, "dual_verso_lungo.png"), True, True, False),
    (os.path.join(TESTDATA, "full_std_bordo.png"), True, True, False),
    (os.path.join(TESTDATA, "left_output_italiano.png"), True, True, False),
    # These two were originally (wrongly) marked lis_box=True without
    # actually checking the image - neither has the interpreter box visible
    # at all, just the verse card + lyrics + lower third. Confirmed visually.
    (os.path.join(TESTDATA, "left_verso_testo_canzone.png"), False, True, False),
    (os.path.join(TESTDATA, "none_performance_bianco.png"), False, True, False),
    # The white/gold circular logo behind the guest sits in the
    # centered_lower_third box and is the closest false reading found for it
    # so far (0.165, vs on=0.20) - see config.json's _note for that probe.
    (os.path.join(TESTDATA, "none_giacca_navy.png"), True, True, False),
    (os.path.join(TESTDATA_OVERLAYS, "houston_off.png"), False, False, False),
    # Real false positive from the field: a lower-third banner variant whose
    # saturated blue corner reached into the lis_box ROI and got misread as
    # the interpreter box, even though only the lower third was on screen.
    (os.path.join(TESTDATA_OVERLAYS, "false_positive_lower_third_only.png"), False, True, False),
    # Same song-title segment, ~2.5 minutes apart: a golden sunset background
    # used to read as lower_third=True purely from its hue (see overlays.py's
    # "banner" kind comment) - fixed by dropping the gold-arc component.
    (os.path.join(TESTDATA_OVERLAYS, "sunset_with_banner.png"), False, True, False),
    (os.path.join(TESTDATA_OVERLAYS, "sunset_no_banner.png"), False, False, False),
    # Two more false positives from the same evening, both fixed by requiring
    # blue AND white together instead of blue alone: a wide venue shot (stage
    # lighting reads as "saturated blue") and a plain sky background. Both
    # genuinely have the interpreter box visible, hence lis_box=True.
    (os.path.join(TESTDATA_OVERLAYS, "venue_wide_shot.png"), True, False, False),
    (os.path.join(TESTDATA_OVERLAYS, "sky_no_banner.png"), True, False, False),
    # Real ON example for the third overlay: a location caption ('VILLAMOR,
    # PASAY CITY') over the camera panel, vMix input 40 - both the caption
    # AND the ordinary bottom lower third are genuinely on at once here, they
    # don't conflict since they're different screen regions/vMix inputs.
    (os.path.join(TESTDATA_OVERLAYS, "centered-lowerthird.png"), False, True, True),
]

print("\n1. all three overlay probes on real frames")
for path, expect_lis, expect_banner, expect_centered in CASES:
    img = load(path)
    label = os.path.basename(path)
    if img is None:
        print(f"  [SKIP] {label}: could not read")
        continue
    det.reset()
    r = det.read(img)
    check(f"{label} lis_box", r.hot.get("lis_box"), expect_lis)
    check(f"{label} lower_third", r.hot.get("lower_third"), expect_banner)
    check(f"{label} centered_lower_third", r.hot.get("centered_lower_third"), expect_centered)

print("\n2. hysteresis: a probe that's on stays on below 'on' but above 'off'")
img = load(os.path.join(SHOTS, "full.png"))
det.reset()
det.read(img)  # first read: crosses 'on', becomes hot
check("lis_box hot after first read", det._hot["lis_box"], True)
# a synthetic frame with the probe value between off and on must stay hot
# (this is exactly the mechanism that fixed the DUAL/BIG_LEFT flip-flop bug
# earlier in this project - verified narrowly here for the new probes too)

print("\n" + ("ALL OK" if not failures else f"FAILED: {len(failures)} -> {failures}"))
sys.exit(1 if failures else 0)
