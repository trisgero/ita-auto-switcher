"""Guided setup, designed for someone who has never seen a JSON file.

The problem it solves: every PC can have its monitors connected in a
different order, so the right number in config.json changes from machine to
machine. Instead of asking to edit the file by hand, it saves a preview of
every monitor, opens it with Windows' default image viewer, and just asks
"which of these pictures shows the Filipino Zoom feed?".
"""

from __future__ import annotations

import json
import os

import cv2

from .capture import ScreenCapture
from .vmix import VmixClient, VmixError


def save_previews(base_dir: str) -> list[tuple[int, str, tuple[int, int]]]:
    """Saves a screenshot of every monitor in <base_dir>/monitor_previews/.

    Returns (monitor_number, file_path, (width, height))."""
    out_dir = os.path.join(base_dir, "monitor_previews")
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        try:
            os.remove(os.path.join(out_dir, old))
        except OSError:
            pass

    monitors = ScreenCapture.list_monitors()
    results = []
    for idx, mon in enumerate(monitors):
        if idx == 0:
            continue  # [0] = all monitors combined into one, not selectable
        cap = ScreenCapture(monitor=idx, work_width=mon["width"])
        frame = cap.grab()
        cap.close()
        path = os.path.join(out_dir, f"monitor_{idx}.png")
        cv2.imwrite(path, frame)
        results.append((idx, path, (mon["width"], mon["height"])))
    return results


def open_file(path: str) -> None:
    try:
        os.startfile(path)  # Windows opens it with the default image viewer
    except OSError:
        print(f"  (couldn't open it automatically: open it by hand: {path})")


def pick_monitor(base_dir: str) -> int | None:
    print("\nLooking for connected monitors and saving a preview of each...")
    previews = save_previews(base_dir)
    if not previews:
        print("No monitor found.")
        return None

    print(f"Found {len(previews)} monitor(s). Opening the previews one by one.\n")
    for idx, path, (w, h) in previews:
        print(f"  Monitor {idx}  ({w}x{h})  ->  {os.path.basename(path)}")
        open_file(path)

    print(
        "\nLook at the pictures that just opened: which one shows the "
        "Filipino Zoom stream (the one with the verses) full-screen?"
    )
    valid = {idx for idx, _, _ in previews}
    while True:
        raw = input(f"Number of the right monitor {sorted(valid)}: ").strip()
        if raw.isdigit() and int(raw) in valid:
            return int(raw)
        print("Not a valid number, try again.")


def apply_monitor(config_path: str, monitor: int) -> None:
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg["capture"]["monitor"] = monitor
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def check_vmix(config_path: str) -> tuple[bool, str]:
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    vmix_cfg = {k: v for k, v in cfg["vmix"].items() if not k.startswith("_")}
    try:
        state = VmixClient(**vmix_cfg).state()
        return True, f"OK (current program: input {state.active})"
    except VmixError as exc:
        return False, str(exc)


def current_monitor(config_path: str) -> int:
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    return int(cfg["capture"].get("monitor", 1))
