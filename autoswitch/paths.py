"""Path resolution, correct both from source and from a packaged .exe.

PyInstaller (onefile) extracts the code into a temporary folder (_MEIPASS) on
every launch: if config.json/logs/testdata were read from there, every change
would disappear on close and every PC would restart from the factory
template. This module keeps the two apart:

  base_dir()   -> the .exe's folder (or the project's, from source). This is
                  where config.json, logs/, testdata/ live: persistent,
                  editable, what the user can copy from one PC to another.
  bundle_dir() -> the temporary folder with the files included in the exe
                  (the "factory" config.json to copy on first run). From
                  source this is the same as the project folder.
"""

from __future__ import annotations

import os
import sys


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_dir() -> str:
    return getattr(sys, "_MEIPASS", base_dir())


def ensure_config(base: str | None = None) -> str:
    """If config.json is missing next to the exe, creates it by copying the
    template included in the bundle. Returns the (persistent) path to use."""
    base = base or base_dir()
    path = os.path.join(base, "config.json")
    if not os.path.exists(path):
        import shutil

        template = os.path.join(bundle_dir(), "config.json")
        shutil.copy(template, path)
    return path
