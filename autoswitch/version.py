"""Reads the running build's version info: which git commit it was built
from, and when. This is what answers "which version is this .exe?" months
after it was sent to someone.

From a packaged exe: reads version_info.json baked in at build time by
build_info.py (always from bundle_dir(), never copied to the user's
base_dir - it must reflect THIS exe, not go stale across upgrades).

From source (dev, not frozen): queries git directly, live, so it's always
accurate without needing a build step.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from .paths import base_dir, bundle_dir


def _git(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args], text=True, cwd=base_dir(), stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def load_version_info() -> dict:
    baked = os.path.join(bundle_dir(), "version_info.json")
    if os.path.exists(baked):
        with open(baked, encoding="utf-8") as fh:
            return json.load(fh)

    if not getattr(sys, "frozen", False):
        commit = _git("rev-parse", "--short", "HEAD")
        if commit:
            return {
                "commit": commit,
                "dirty": bool(_git("status", "--porcelain")),
                "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "?",
                "built_at": "running from source",
            }

    return {"commit": "unknown", "dirty": False, "branch": "?", "built_at": "?"}


def version_label() -> str:
    info = load_version_info()
    mark = "*" if info.get("dirty") else ""
    return f"{info['commit']}{mark}  ({info['built_at']})"
