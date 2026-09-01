"""Verse-layout classification from the position of the dark panels.

The reliable signal in this graphic is not the text, it's the GEOMETRY: the
verse card is an almost-uniform navy panel (V ~ 65-85) on a light blue
background (V ~ 200+). Whether that panel is present or not distinguishes the
layouts with huge margins, and doesn't depend on which verse is on screen nor
on Zoom's compression.

Measured on real screenshots (fraction of "navy" pixels: H 100-130, S>60, V<110):

    probe        full   left  big_l   dual  triple    cam
    card_left    0.69   0.84   0.83   0.83   0.52     0.02
    col_right    0.62   0.13   0.99   1.00   0.85     0.03
    far_right    0.75   0.02   0.12   0.99   0.51     0.10
    low_left     0.96   0.05   0.37   0.00   0.01     0.04
    low_right    1.00   0.09   0.05   0.00   0.00     0.00
    center_low   0.94   0.00   0.00   0.00   0.77     0.00

Across the 5 layouts every rule is mutually exclusive: each one matches only
one rule, so evaluation order is not load-bearing (verified in
test_detection.py). Values stay stable from 320 to 960px of width.

NOTE: calibration was done on screenshots of the ITALIAN output. The premise
of the project is that both sides share the same geometry, but this should be
re-verified on Filipino frames with:  python calibrate.py --image <file>

Available metrics per ROI:
  "navy"  fraction of pixels matching the card's color (H/S/V within the
          given bounds). HUE does not scale with gain, so a dim camera can't
          pass for a graphic panel - which is exactly what fools a
          brightness-only threshold. It's the right metric for this graphic.
  "dark"  brightness only (V < v_max). Simpler, less selective.
  "edges" edge density (Canny): useful if it's ever needed to distinguish
          "card with text" from "empty card".

Every ROI accepts an optional "max_std" on brightness: a graphic panel is
flat (std ~2), a camera isn't (std ~55). A second, independent gate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import cv2
import numpy as np


def _slice(frame: np.ndarray, box: list[float]) -> np.ndarray:
    h, w = frame.shape[:2]
    x, y, bw, bh = box
    x0, y0 = int(round(x * w)), int(round(y * h))
    x1, y1 = int(round((x + bw) * w)), int(round((y + bh) * h))
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, max(x0 + 1, x1)), min(h, max(y0 + 1, y1))
    return frame[y0:y1, x0:x1]


@dataclass
class Reading:
    """Result of a single frame analysis."""

    state: str
    metrics: dict[str, float] = field(default_factory=dict)
    stds: dict[str, float] = field(default_factory=dict)
    hot: dict[str, bool] = field(default_factory=dict)
    gate_diff: float | None = None
    gate_open: bool = True


class Detector:
    def __init__(self, cfg: dict, base_dir: str = "."):
        self.cfg = cfg
        self.blur = int(cfg.get("blur", 5))
        self.canny_low = int(cfg.get("canny_low", 60))
        self.canny_high = int(cfg.get("canny_high", 160))
        self.rois = {k: v for k, v in cfg["rois"].items() if not k.startswith("_")}
        self.rules = [r for r in cfg["rules"] if r.get("enabled", True)]

        # "Sticky" state per ROI: implements hysteresis. A ROI that's on
        # stays on until it drops below 'off', not below 'on'.
        self._hot: dict[str, bool] = {name: False for name in self.rois}

        gate = cfg.get("template_gate", {})
        self.gate_enabled = bool(gate.get("enabled", False))
        self.gate_box = gate.get("box")
        self.gate_max_diff = float(gate.get("max_diff", 12.0))
        self._gate_ref: np.ndarray | None = None
        if self.gate_enabled and gate.get("reference"):
            path = os.path.join(base_dir, gate["reference"])
            ref = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if ref is None:
                raise FileNotFoundError(
                    f"template_gate is enabled but the reference {path} is missing. "
                    f"Create it with: python calibrate.py --save-ref"
                )
            self._gate_ref = ref

    # ------------------------------------------------------------------ measures

    def _prepare(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Returns (HSV, gray), both blurred."""
        blurred = frame
        if self.blur >= 3 and self.blur % 2 == 1:
            blurred = cv2.GaussianBlur(frame, (self.blur, self.blur), 0)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY)
        return hsv, gray

    def _measure(self, roi: dict, hsv: np.ndarray, gray: np.ndarray) -> tuple[float, float]:
        metric = roi.get("metric", "navy")

        if metric == "edges":
            patch = _slice(gray, roi["box"])
            edges = cv2.Canny(patch, self.canny_low, self.canny_high)
            return float(np.count_nonzero(edges)) / float(edges.size), float(patch.std())

        patch = _slice(hsv, roi["box"])
        h, s, v = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
        std = float(v.std())

        if metric == "dark":
            return float((v < float(roi.get("v_max", 110))).mean()), std

        # "navy": dark AND the card's color. Hue doesn't scale with gain, so a
        # dim camera can't pass for a graphic panel the way it would with a
        # brightness-only threshold. An earlier version also counted bright,
        # desaturated pixels as "card" (to tolerate white verse text), but
        # that let a performer's white/cream outfit or a white set piece get
        # misread as a verse card (testdata/none_performance_bianco.png).
        # Pure navy alone, with today's thresholds, already covers every
        # known case including long verses - see the margins in the module
        # docstring - so that extra tolerance was dropped.
        mask = (
            (h >= float(roi.get("h_min", 100)))
            & (h <= float(roi.get("h_max", 130)))
            & (s >= float(roi.get("s_min", 60)))
            & (v <= float(roi.get("v_max", 110)))
        )

        return float(mask.mean()), std

    def _check_gate(self, gray: np.ndarray) -> tuple[float | None, bool]:
        """Is the verse graphic actually on screen (not a camera)?"""
        if not self.gate_enabled or self._gate_ref is None or self.gate_box is None:
            return None, True
        patch = _slice(gray, self.gate_box)
        ref = self._gate_ref
        if ref.shape != patch.shape:
            ref = cv2.resize(ref, (patch.shape[1], patch.shape[0]), interpolation=cv2.INTER_AREA)
        diff = float(np.mean(cv2.absdiff(patch, ref)))
        return diff, diff <= self.gate_max_diff

    # ------------------------------------------------------------- classify

    def read(self, frame: np.ndarray) -> Reading:
        hsv, gray = self._prepare(frame)
        gate_diff, gate_open = self._check_gate(gray)

        metrics: dict[str, float] = {}
        stds: dict[str, float] = {}
        for name, roi in self.rois.items():
            metric, std = self._measure(roi, hsv, gray)
            metrics[name] = metric
            stds[name] = std

            # A graphic panel is flat; a dark camera isn't. If the ROI
            # declares max_std, the surface must also be uniform.
            max_std = roi.get("max_std")
            flat = max_std is None or std <= float(max_std)

            if self._hot[name]:
                self._hot[name] = metric >= float(roi["off"]) and flat
            else:
                self._hot[name] = metric >= float(roi["on"]) and flat

        state = "NONE"
        if gate_open:
            for rule in self.rules:
                if all(self._hot.get(n, False) for n in rule.get("hot", [])) and not any(
                    self._hot.get(n, False) for n in rule.get("cold", [])
                ):
                    state = rule["state"]
                    break

        return Reading(
            state=state,
            metrics=metrics,
            stds=stds,
            hot=dict(self._hot),
            gate_diff=gate_diff,
            gate_open=gate_open,
        )

    def reset(self) -> None:
        """Resets hysteresis. Only needed when analyzing unrelated images."""
        self._hot = {name: False for name in self.rois}


class Debouncer:
    """Accepts a new state only after N consecutive agreeing readings.

    Eliminates flicker during graphic transitions/animations, which is the
    other half of the reliability problem with the pixel matcher.
    """

    def __init__(self, confirm_frames: int = 3, initial: str = "NONE"):
        self.confirm_frames = max(1, confirm_frames)
        self.stable = initial
        self._candidate = initial
        self._count = 0

    def update(self, raw: str) -> str:
        if raw == self.stable:
            self._candidate, self._count = raw, 0
            return self.stable
        if raw == self._candidate:
            self._count += 1
        else:
            self._candidate, self._count = raw, 1
        if self._count >= self.confirm_frames:
            self.stable = raw
            self._count = 0
        return self.stable
