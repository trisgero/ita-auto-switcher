"""Independent on/off overlay detectors: lower thirds and sign-language box.

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

One exception to "independent": the three lower thirds (lower_third,
centered_lower_third, group_lower_third) can genuinely be pixel-on at the
same time - each is a real, separately-toggled vMix overlay, and production
sometimes leaves more than one actually on in vMix at once - but only one
may ever be REPORTED on: by production rule, exactly one lower third shows
at a time, the most specific one available (centered/group name a location
or group; lower_third is the generic fallback). A probe declares
"suppressed_by": [other probe names] in config.json to encode that priority;
it's a policy choice, not a sign the probes can't tell the graphics apart at
the pixel level. See OverlayDetector.read().
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
    stds: dict[str, float] = field(default_factory=dict)


class OverlayDetector:
    def __init__(self, cfg: dict):
        self.probes = {k: v for k, v in cfg.items() if not k.startswith("_")}
        self._hot: dict[str, bool] = {name: False for name in self.probes}

    def _measure(self, probe: dict, hsv: np.ndarray) -> tuple[float, float]:
        patch = _slice(hsv, probe["box"])
        h, s, v = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
        kind = probe.get("kind", "lightblue")
        std = float(v.std())

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
            # Lower third: requires BOTH the saturated blue bar AND the white
            # text panel present TOGETHER (the metric is min of the two
            # fractions, not an OR of masks). Blue alone wasn't enough on its
            # own: a wide venue shot with stage lighting, and a plain blue
            # sky background, both produced plenty of "saturated blue" with
            # no banner anywhere in frame (see testdata_overlays/venue_wide_
            # shot.png and sky_no_banner.png) - a photographed scene can
            # easily be mostly one glob of blue, but it essentially never has
            # a big flat white panel sitting right next to a big flat blue
            # one, which is exactly what this banner graphic looks like.
            # (An earlier version also had a "gold arc" range, dropped
            # separately for matching a sunset's hue - see git history.)
            #
            # s<=40 alone was still too loose: a pale cream/gold lyrics
            # caption (a completely different graphic, H~25-33) has low
            # enough saturation (S~21-31) to pass as "white paper panel",
            # while a dark bluish shadow nearby passed as "the blue bar"
            # (blue has no v_min) - together they crossed the threshold with
            # zero real banner on screen (testdata_overlays/shouldnt-appear-
            # lt.png). Real white text/panel pixels are much closer to true
            # white (median S 0-4 across every real ON example) than a
            # cream tint is, so s<=15 keeps the real banner's white fraction
            # almost untouched (0.16-0.21, was 0.20-0.27) while collapsing
            # the cream false positive to ~0.
            blue = ((h >= 100) & (h <= 130) & (s >= 100)).mean()
            white = ((v >= 225) & (s <= 15)).mean()
            return float(min(blue, white)), std
        elif kind == "gold":
            # Group lower third: solid gold/orange panel behind white bold
            # text (testdata_overlays/group-lowerthird.png, "ALL CELEBRANTS").
            # Gold hue alone is fooled by a sunset background - the exact
            # same lesson learned for lower_third's dropped "gold arc"
            # component (see git history): a tropical sunset b-roll measured
            # 0.22 on hue+saturation alone (testdata_overlays/sunset_no_
            # banner.png), a real but distant margin from this graphic's
            # 0.91. max_std makes that margin much wider for free: the real
            # panel is flat (V std ~7), a photographed sunset sky isn't (V
            # std 36-56) - same "graphic panel vs busy photo" signal
            # detect.py's ROIs already use, applied here as a second,
            # independent gate rather than tightening the color mask itself.
            mask = (
                (h >= probe.get("h_min", 15)) & (h <= probe.get("h_max", 30))
                & (s >= probe.get("s_min", 150)) & (v >= probe.get("v_min", 180))
            )
        else:
            raise ValueError(f"unknown overlay probe kind: {kind!r}")

        return float(mask.mean()), std

    def read(self, frame: np.ndarray) -> OverlayReading:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        metrics: dict[str, float] = {}
        stds: dict[str, float] = {}
        for name, probe in self.probes.items():
            value, std = self._measure(probe, hsv)
            metrics[name] = value
            stds[name] = std

            # A graphic panel is flat; a photographed scene isn't. Only
            # gates probes that declare max_std - existing probes are
            # untouched.
            max_std = probe.get("max_std")
            flat = max_std is None or std <= float(max_std)

            if self._hot[name]:
                self._hot[name] = value >= float(probe["off"]) and flat
            else:
                self._hot[name] = value >= float(probe["on"]) and flat

        # Some overlays take priority over others regardless of their own
        # pixel reading (config's "suppressed_by": [other probe names]) -
        # e.g. lower_third (the generic banner) is suppressed whenever a more
        # specific lower third (centered_lower_third, group_lower_third) is
        # on, by production rule: only one lower third is ever reported at a
        # time even when more than one is genuinely lit up in vMix. Applied
        # to a COPY, not self._hot: the suppressed probe's own hysteresis
        # keeps tracking its real signal underneath, so it comes back the
        # instant the suppressing overlay goes off, without having to
        # re-cross its "on" threshold.
        hot = dict(self._hot)
        for name, probe in self.probes.items():
            if any(hot.get(other) for other in probe.get("suppressed_by", [])):
                hot[name] = False

        return OverlayReading(hot=hot, metrics=metrics, stds=stds)

    def reset(self) -> None:
        self._hot = {name: False for name in self.probes}
