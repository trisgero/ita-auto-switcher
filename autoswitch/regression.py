"""Verification/calibration logic shared between test_detection.py (command
line) and the GUI ("Verify all" and "Recalibrate thresholds" buttons).

A "case" is a screenshot whose name declares the expected state:
big-left.png -> BIG_LEFT, dual_verso_lungo.png -> DUAL. It's the same
convention used by hand so far in __screenshots/ and testdata/.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import cv2
import numpy as np

from .detect import Detector


def state_from_filename(fname: str) -> str:
    """big-left.png -> BIG_LEFT, dual_verso_lungo.png -> DUAL"""
    stem = os.path.splitext(fname)[0]
    return stem.split("_")[0].upper().replace("-", "_")


def state_to_primary_filename(state: str) -> str:
    """FULL -> full.png, BIG_LEFT -> big-left.png. Inverse of
    state_from_filename for the CANONICAL name (no suffix) used as the
    primary screenshot."""
    return state.lower().replace("_", "-") + ".png"


@dataclass
class Case:
    path: str
    expected: str
    label: str
    primary: bool  # "primary" screenshot (__screenshots/) or verification (testdata/)


@dataclass
class CaseResult:
    case: Case
    got: str
    metrics: dict[str, float] = field(default_factory=dict)
    hot: dict[str, bool] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.got == self.case.expected


def discover_cases(shots_dir: str, testdata_dir: str, known_states: set[str]) -> list[Case]:
    cases: list[Case] = []
    for d, primary in ((shots_dir, True), (testdata_dir, False)):
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.lower().endswith(".png"):
                continue
            expected = state_from_filename(fname)
            if expected not in known_states:
                continue
            cases.append(Case(os.path.join(d, fname), expected, fname, primary))
    return cases


def load_scaled(path: str, work_width: int) -> np.ndarray | None:
    img = cv2.imread(path)
    if img is None:
        return None
    h = int(round(img.shape[0] * work_width / img.shape[1]))
    return cv2.resize(img, (work_width, h), interpolation=cv2.INTER_AREA)


def run_cases(cfg: dict, base_dir: str, cases: list[Case]) -> list[CaseResult]:
    detector = Detector(cfg["detector"], base_dir)
    work_width = int(cfg["capture"]["work_width"])
    results = []
    for case in cases:
        img = load_scaled(case.path, work_width)
        if img is None:
            results.append(CaseResult(case, "?", error=f"could not read {case.path}"))
            continue
        detector.reset()
        reading = detector.read(img)
        results.append(CaseResult(case, reading.state, reading.metrics, reading.hot))
    return results


def rules_by_state(cfg: dict) -> dict[str, dict]:
    return {r["state"]: r for r in cfg["detector"]["rules"] if r.get("enabled", True)}


@dataclass
class ThresholdSuggestion:
    roi: str
    on: float | None
    off: float | None
    current_on: float
    current_off: float
    hot_min: float | None
    cold_max: float | None
    margin: float | None
    warning: str | None
    n_hot: int
    n_cold: int


def suggest_thresholds(cfg: dict, base_dir: str, cases: list[Case]) -> list[ThresholdSuggestion]:
    """For every ROI, measures the value on each tagged screenshot and
    recomputes on/off with a margin, using the rules to know whether that ROI
    should be hot or cold for each case's expected state. It's the same
    procedure (min of the "hot" cases vs max of the "cold" cases, threshold
    at the midpoint with margin) used by hand during the initial calibration
    - here it's automatic and repeatable every time the screenshots change.
    """
    results = run_cases(cfg, base_dir, cases)
    rules = rules_by_state(cfg)
    rois = cfg["detector"]["rois"]

    suggestions = []
    for roi_name, roi_cfg in rois.items():
        if roi_name.startswith("_"):
            continue
        hot_vals: list[float] = []
        cold_vals: list[float] = []
        for res in results:
            if res.error or roi_name not in res.metrics:
                continue
            rule = rules.get(res.case.expected)
            value = res.metrics[roi_name]
            if rule is None:
                # State with no rule (e.g. NONE): no template should light up
                # any probe.
                cold_vals.append(value)
            elif roi_name in rule.get("hot", []):
                hot_vals.append(value)
            elif roi_name in rule.get("cold", []):
                cold_vals.append(value)
            # if the ROI isn't mentioned by the rule, that case constrains nothing

        current_on = float(roi_cfg.get("on", 0))
        current_off = float(roi_cfg.get("off", 0))

        if not hot_vals or not cold_vals:
            suggestions.append(ThresholdSuggestion(
                roi_name, None, None, current_on, current_off,
                min(hot_vals) if hot_vals else None,
                max(cold_vals) if cold_vals else None,
                None,
                "insufficient data: need screenshots both for templates that turn it on "
                "and for templates that leave it off",
                len(hot_vals), len(cold_vals),
            ))
            continue

        hot_min, cold_max = min(hot_vals), max(cold_vals)
        margin = hot_min - cold_max
        if margin <= 0:
            suggestions.append(ThresholdSuggestion(
                roi_name, None, None, current_on, current_off, hot_min, cold_max, margin,
                f"this probe can NO LONGER distinguish the templates (the weakest case in "
                f"favor is {hot_min:.3f}, the strongest case against is {cold_max:.3f}): the "
                f"ROI needs to be redrawn, not just the threshold",
                len(hot_vals), len(cold_vals),
            ))
            continue

        on = cold_max + margin * 0.5
        off = cold_max + margin * 0.2
        warning = None
        if margin < 0.15:
            warning = f"tight margin ({margin:.3f}): add more screenshots if you can"
        suggestions.append(ThresholdSuggestion(
            roi_name, round(on, 3), round(off, 3), current_on, current_off,
            hot_min, cold_max, margin, warning, len(hot_vals), len(cold_vals),
        ))
    return suggestions
