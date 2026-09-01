"""Entry point for the distributed executable.

By default it opens the GUI (template management, screenshots, inputs,
verification). With --console you get the old numbered terminal menu, useful
for command-line diagnostics or if the GUI fails to start.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_gui() -> int:
    from autoswitch.gui import main as gui_main

    return gui_main()


def run_console() -> int:
    from autoswitch import wizard
    from autoswitch.main import Switcher, load_config, setup_logging
    from autoswitch.paths import base_dir, ensure_config

    base = base_dir()
    config_path = ensure_config(base)

    def status_line() -> str:
        ok, detail = wizard.check_vmix(config_path)
        vmix_line = f"vMix: {'OK' if ok else 'UNREACHABLE'} - {detail}"
        monitor = wizard.current_monitor(config_path)
        return f"{vmix_line}\nConfigured monitor: {monitor}"

    def run_switcher(dry_run: bool) -> None:
        setup_logging(base, verbose=False)
        switcher = Switcher(load_config(config_path), base, dry_run=dry_run)
        print("\nRunning. Press Ctrl+C to stop and go back to the menu.\n")
        try:
            switcher.run()
        except KeyboardInterrupt:
            switcher.stop()
            print("\nStopped.")

    while True:
        print("\n=== Verse Auto-switcher (ITA/FIL) - console mode ===\n")
        print(status_line())
        print(
            "\n  [1] TEST - see what it would do, does NOT touch vMix"
            "\n  [2] GO LIVE - commands vMix for real"
            "\n  [3] Change monitor"
            "\n  [4] Exit\n"
        )
        choice = input("Choice: ").strip()
        if choice == "1":
            run_switcher(dry_run=True)
        elif choice == "2":
            run_switcher(dry_run=False)
        elif choice == "3":
            monitor = wizard.pick_monitor(base)
            if monitor is not None:
                wizard.apply_monitor(config_path, monitor)
                print(f"Set: monitor {monitor}.")
        elif choice == "4":
            return 0
        else:
            print("Not a valid choice.")


def _fatal_error_dialog(exc: Exception) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Auto-switcher - error", f"Startup error:\n\n{exc}")
        root.destroy()
    except Exception:
        pass


def main() -> int:
    console_mode = "--console" in sys.argv[1:]
    try:
        return run_console() if console_mode else run_gui()
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        if not console_mode:
            _fatal_error_dialog(exc)
        else:
            input("\nAn unexpected error occurred. Press ENTER to close...")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
