"""Independent on/off overlay detectors: lower third and sign-language box.

Different problem from the verse-card state machine in detect.py: these two
graphics are NOT mutually exclusive states of "what's on screen" - production
keeps them on through many different segments, including performances that
would otherwise get misread as a verse (confirmed on real footage: both
overlays measured ON during the false-positive performance frames). So each
one gets its own independent binary detector, reconciled against its own
vMix overlay channel, with no interaction with the verse-card logic.

Both signals have far more margin than anything in detect.py, because the
question is binary presence/absence of a distinctive flat-color graphic
against a busy photographic image, not "which of several similar cards is
this" - measured on 11 ON examples vs 1 OFF example (testdata/off_houston_
no_overlays.png):

    probe        worst ON   OFF
    lis_box      0.36       0.03
    lower_third  0.83       0.11

Only one OFF example exists so far - get more real cases before trusting the
thresholds blindly the way earlier probes in this project were trusted on
too little data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .detect import _slice


@dataclass
class OverlayReading:
    hot: dict[str, bool] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)


class OverlayDetector:
    def __init__(self, cfg: dict):
        self.probes = {k: v for k, v in cfg.items() if not k.startswith("_")}
        self._hot: dict[str, bool] = {name: False for name in self.probes}

    def _measure(self, probe: dict, hsv: np.ndarray) -> float:
        patch = _slice(hsv, probe["box"])
        h, s, v = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
        kind = probe.get("kind", "lightblue")

        if kind == "lightblue":
            # LIS box: solid light-blue backdrop behind the interpreter. The
            # upper bound on saturation matters as much as the lower one: the
            # backdrop is a soft studio blue (S~128), clearly less vivid than
            # the lower third's punchy graphic blue (S~208-234) - without
            # s_max, a lower-third banner variant whose blue corner reaches
            # into this ROI's screen position gets misread as the interpreter
            # box (see testdata_overlays/false_positive_lower_third_only.png).
            mask = (
                (h >= probe.get("h_min", 95)) & (h <= probe.get("h_max", 115))
                & (s >= probe.get("s_min", 80)) & (s <= probe.get("s_max", 255))
                & (v >= probe.get("v_min", 140))
            )
        elif kind == "banner":
            # Lower third: saturated blue bar + white text/panel + gold arc.
            blue = (h >= 100) & (h <= 130) & (s >= 100)
            white = (v >= 230) & (s <= 30)
            gold = (h >= 15) & (h <= 35) & (s >= 120)
            mask = blue | white | gold
        else:
            raise ValueError(f"unknown overlay probe kind: {kind!r}")

        return float(mask.mean())

    def read(self, frame: np.ndarray) -> OverlayReading:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        metrics: dict[str, float] = {}
        for name, probe in self.probes.items():
            value = self._measure(probe, hsv)
            metrics[name] = value
            if self._hot[name]:
                self._hot[name] = value >= float(probe["off"])
            else:
                self._hot[name] = value >= float(probe["on"])
        return OverlayReading(hot=dict(self._hot), metrics=metrics)

    def reset(self) -> None:
        self._hot = {name: False for name in self.probes}
