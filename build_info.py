"""Generates version_info.json (git commit + build time) for the exe build,
and prints the exe name build.bat should use for it. Centralized here in
Python rather than in batch, because git-output parsing and timestamp
formatting are both fragile in cmd.exe.

version_info.json gets bundled into the exe (--add-data) and read by
autoswitch/version.py directly from the exe's own embedded data - never
copied to the user's base_dir like config.json is, because it must always
reflect the exe that's actually running, not go stale across upgrades.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess


def git(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        ).strip()
    except Exception:
        return None


def main() -> None:
    commit = git("rev-parse", "--short", "HEAD") or "unknown"
    dirty = bool(git("status", "--porcelain"))
    branch = git("rev-parse", "--abbrev-ref", "HEAD") or "?"
    now = datetime.datetime.now()

    info = {
        "commit": commit,
        "dirty": dirty,
        "branch": branch,
        "built_at": now.isoformat(timespec="seconds"),
    }
    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, "version_info.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)
        fh.write("\n")

    suffix = "-dirty" if dirty else ""
    exe_name = f"AutoSwitcher_{now:%Y%m%d_%H%M}_{commit}{suffix}"
    print(exe_name)


if __name__ == "__main__":
    main()
