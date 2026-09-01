"""Screen capture of the monitor where the Zoom feed runs full-screen."""

from __future__ import annotations

import cv2
import mss
import numpy as np


class ScreenCapture:
    """Returns BGR frames already resized to the working width.

    Working at ~480px instead of 1920 cuts CPU cost by ~16x and, more
    importantly, averages away Zoom's compression noise: the metrics become
    much more stable.
    """

    def __init__(self, monitor: int = 2, region: list[int] | None = None, work_width: int = 480):
        self._sct = mss.mss()
        mons = self._sct.monitors  # [0] = all monitors combined, then 1, 2, ...
        if monitor >= len(mons):
            raise ValueError(
                f"monitor {monitor} does not exist. Available: 1..{len(mons) - 1}. "
                f"List: {mons[1:]}"
            )
        m = mons[monitor]
        if region:
            x, y, w, h = region
            self._box = {"left": m["left"] + x, "top": m["top"] + y, "width": w, "height": h}
        else:
            self._box = {"left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}
        self.work_width = work_width

    @property
    def source_size(self) -> tuple[int, int]:
        return self._box["width"], self._box["height"]

    def grab(self) -> np.ndarray:
        raw = np.asarray(self._sct.grab(self._box))  # BGRA
        frame = raw[:, :, :3]
        h, w = frame.shape[:2]
        if w != self.work_width:
            scale = self.work_width / w
            frame = cv2.resize(
                frame, (self.work_width, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA
            )
        return np.ascontiguousarray(frame)

    def close(self) -> None:
        self._sct.close()

    @staticmethod
    def list_monitors() -> list[dict]:
        with mss.mss() as sct:
            return list(sct.monitors)
