"""Auto-switcher GUI: template management (screenshots + vMix input),
detection verification, threshold recalibration, start/stop.

A single Tkinter file (included with Python, no extra dependency in the
exe). Replaces the numbered console menu as the entry point for end users.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import wizard
from .main import Switcher, load_config, setup_logging
from .paths import base_dir as resolve_base_dir
from .paths import bundle_dir as resolve_bundle_dir
from .paths import ensure_config
from .regression import (
    ThresholdSuggestion,
    discover_cases,
    run_cases,
    rules_by_state,
    state_from_filename,
    state_to_primary_filename,
    suggest_thresholds,
)

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_MONO = ("Consolas", 9)


def _thumb(path: str, max_w: int = 360, max_h: int = 220) -> tk.PhotoImage | None:
    try:
        img = tk.PhotoImage(file=path)
    except tk.TclError:
        return None
    factor = max(1, img.width() // max_w, img.height() // max_h)
    if factor > 1:
        img = img.subsample(factor, factor)
    return img


class QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        self.q.put(self.format(record))


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Verse Auto-switcher")
        self.geometry("980x680")
        self.minsize(860, 600)

        self.base_dir = resolve_base_dir()
        self.config_path = ensure_config(self.base_dir)
        self.shots_dir = os.path.join(self.base_dir, "__screenshots")
        self.testdata_dir = os.path.join(self.base_dir, "testdata")
        self._seed_templates()

        self.cfg = load_config(self.config_path)
        self._thumb_ref: tk.PhotoImage | None = None
        self._selected_state: str | None = None

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.switcher: Switcher | None = None
        self.switcher_thread: threading.Thread | None = None

        self._build_ui()
        self._refresh_template_list()
        self._refresh_status()
        self.after(150, self._poll_log_queue)

    def _seed_templates(self) -> None:
        """On first launch on a new PC, copies the "factory" screenshots
        included in the exe (same mechanism as ensure_config for
        config.json). If the folder already exists (machine already used) it
        touches nothing, so as not to overwrite the user's own choices."""
        bundle = resolve_bundle_dir()
        for name in ("__screenshots", "testdata"):
            dest = os.path.join(self.base_dir, name)
            if os.path.exists(dest):
                continue
            src = os.path.join(bundle, name)
            if os.path.isdir(src):
                shutil.copytree(src, dest)
            else:
                os.makedirs(dest, exist_ok=True)

    # ------------------------------------------------------------ build UI

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=8)
        top.pack(side="top", fill="x")

        self.status_var = tk.StringVar(value="vMix: ...")
        self.monitor_var = tk.StringVar(value="Monitor: ...")
        ttk.Label(top, textvariable=self.status_var, font=FONT).pack(side="left", padx=(0, 16))
        ttk.Label(top, textvariable=self.monitor_var, font=FONT).pack(side="left", padx=(0, 16))
        ttk.Button(top, text="Change monitor...", command=self._open_monitor_picker).pack(side="left")
        ttk.Button(top, text="Refresh status", command=self._refresh_status).pack(side="left", padx=(6, 0))

        self.run_btn_test = ttk.Button(top, text="TEST (doesn't touch vMix)", command=self._start_test)
        self.run_btn_live = ttk.Button(top, text="GO LIVE", command=self._start_live)
        self.run_btn_stop = ttk.Button(top, text="STOP", command=self._stop_switcher, state="disabled")
        self.run_btn_stop.pack(side="right")
        self.run_btn_live.pack(side="right", padx=(0, 6))
        self.run_btn_test.pack(side="right", padx=(0, 6))

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(side="top", fill="both", expand=True, padx=8, pady=(0, 8))

        # ------------------------------------------------- left column: templates
        left = ttk.Frame(body, padding=(0, 4, 8, 0))
        body.add(left, weight=1)

        ttk.Label(left, text="Templates", font=FONT_BOLD).pack(anchor="w")
        self.template_list = tk.Listbox(left, font=FONT, exportselection=False, height=14)
        self.template_list.pack(fill="both", expand=True, pady=(4, 4))
        self.template_list.bind("<<ListboxSelect>>", self._on_template_selected)

        btns = ttk.Frame(left)
        btns.pack(fill="x")
        ttk.Button(btns, text="New...", command=self._new_template).pack(side="left")
        ttk.Button(btns, text="Delete", command=self._delete_template).pack(side="left", padx=(6, 0))

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8)
        ttk.Button(left, text="Verify all", command=self._run_verify_all).pack(fill="x")
        ttk.Button(left, text="Recalibrate thresholds...", command=self._open_recalibrate).pack(
            fill="x", pady=(6, 0))

        # ------------------------------------------------- right column: detail
        right = ttk.Frame(body, padding=(8, 4, 0, 0))
        body.add(right, weight=2)

        self.detail_title = ttk.Label(right, text="", font=FONT_BOLD)
        self.detail_title.pack(anchor="w")

        self.detail_note = ttk.Label(right, text="", font=FONT, foreground="#a33", wraplength=520, justify="left")
        self.detail_note.pack(anchor="w", pady=(2, 6))

        form = ttk.Frame(right)
        form.pack(anchor="w", fill="x")

        ttk.Label(form, text="vMix input:", font=FONT).grid(row=0, column=0, sticky="w")
        self.input_var = tk.StringVar()
        self.input_entry = ttk.Spinbox(form, from_=0, to=9999, textvariable=self.input_var, width=8,
                                       command=self._on_input_changed)
        self.input_entry.grid(row=0, column=1, sticky="w", padx=(6, 16))
        self.input_entry.bind("<FocusOut>", lambda _e: self._on_input_changed())
        self.input_entry.bind("<Return>", lambda _e: self._on_input_changed())

        self.enabled_var = tk.BooleanVar(value=True)
        self.enabled_check = ttk.Checkbutton(form, text="Rule active", variable=self.enabled_var,
                                             command=self._on_enabled_changed)
        self.enabled_check.grid(row=0, column=2, sticky="w")

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=8)

        ttk.Label(right, text="Primary screenshot (calibration)", font=FONT_BOLD).pack(anchor="w")
        shot_row = ttk.Frame(right)
        shot_row.pack(fill="x", pady=(4, 4))
        self.thumb_label = ttk.Label(shot_row, text="(none)", relief="groove", anchor="center")
        self.thumb_label.pack(side="left")
        shot_btns = ttk.Frame(shot_row)
        shot_btns.pack(side="left", fill="y", padx=(10, 0))
        ttk.Button(shot_btns, text="Choose screenshot...", command=self._choose_primary_shot).pack(fill="x")
        ttk.Button(shot_btns, text="Remove", command=self._remove_primary_shot).pack(fill="x", pady=(4, 0))

        ttk.Label(right, text="Additional verification screenshots", font=FONT_BOLD).pack(anchor="w", pady=(8, 0))
        verif_row = ttk.Frame(right)
        verif_row.pack(fill="both", expand=False, pady=(4, 0))
        self.verif_list = tk.Listbox(verif_row, font=FONT, height=5)
        self.verif_list.pack(side="left", fill="both", expand=True)
        verif_btns = ttk.Frame(verif_row)
        verif_btns.pack(side="left", fill="y", padx=(10, 0))
        ttk.Button(verif_btns, text="Add...", command=self._add_verif_shot).pack(fill="x")
        ttk.Button(verif_btns, text="Remove", command=self._remove_verif_shot).pack(fill="x", pady=(4, 0))

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(right, text="Log", font=FONT_BOLD).pack(anchor="w")
        log_frame = ttk.Frame(right)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, font=FONT_MONO, height=10, state="disabled", wrap="none")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="left", fill="y")

    # --------------------------------------------------------------- config utility

    def _save_config(self) -> None:
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(self.cfg, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    def _sync_managed_inputs(self) -> None:
        numbers = {int(v) for k, v in self.cfg["states"].items() if not k.startswith("_")}
        self.cfg["managed_inputs"] = sorted(numbers)

    def _states(self) -> list[str]:
        return [k for k in self.cfg["states"] if not k.startswith("_")]

    def _log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # --------------------------------------------------------------- template list

    def _refresh_template_list(self) -> None:
        self.template_list.delete(0, "end")
        for state in self._states():
            self.template_list.insert("end", state)
        if self._selected_state and self._selected_state in self._states():
            idx = self._states().index(self._selected_state)
            self.template_list.selection_set(idx)
            self._show_template(self._selected_state)
        elif self._states():
            self.template_list.selection_set(0)
            self._show_template(self._states()[0])

    def _on_template_selected(self, _evt=None) -> None:
        sel = self.template_list.curselection()
        if not sel:
            return
        state = self.template_list.get(sel[0])
        self._show_template(state)

    def _show_template(self, state: str) -> None:
        self._selected_state = state
        self.detail_title.configure(text=state)

        rule = rules_by_state(self.cfg).get(state)
        has_rule = state == "NONE" or rule is not None
        if has_rule:
            self.detail_note.configure(text="")
        else:
            self.detail_note.configure(
                text="No detection rule for this state: the automation will never pick it up "
                "on its own. A rule needs to be defined in the detection engine (config.json / "
                "ask whoever tuned the system) before the input assigned here gets used."
            )

        self.input_var.set(str(self.cfg["states"].get(state, 0)))

        if rule is not None:
            self.enabled_check.configure(state="normal")
            self.enabled_var.set(bool(rule.get("enabled", True)))
        else:
            self.enabled_check.configure(state="disabled")
            self.enabled_var.set(has_rule)

        primary_path = self._primary_shot_path(state)
        self._set_thumb(primary_path)

        self.verif_list.delete(0, "end")
        for case in self._verif_cases(state):
            self.verif_list.insert("end", os.path.basename(case))

    def _set_thumb(self, path: str | None) -> None:
        if path and os.path.exists(path):
            img = _thumb(path)
            if img is not None:
                self._thumb_ref = img
                self.thumb_label.configure(image=img, text="")
                return
        self._thumb_ref = None
        self.thumb_label.configure(image="", text="(none)")

    def _primary_shot_path(self, state: str) -> str | None:
        fname = state_to_primary_filename(state)
        path = os.path.join(self.shots_dir, fname)
        return path if os.path.exists(path) else None

    def _verif_cases(self, state: str) -> list[str]:
        if not os.path.isdir(self.testdata_dir):
            return []
        out = []
        for fname in sorted(os.listdir(self.testdata_dir)):
            if not fname.lower().endswith(".png"):
                continue
            if state_from_filename(fname) == state:
                out.append(os.path.join(self.testdata_dir, fname))
        return out

    # --------------------------------------------------------------- template actions

    def _new_template(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("New template")
        dialog.transient(self)
        dialog.grab_set()
        ttk.Label(dialog, text="Name (e.g. QUAD):", font=FONT).pack(padx=12, pady=(12, 4))
        name_var = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=name_var, font=FONT)
        entry.pack(padx=12, fill="x")
        entry.focus_set()

        def confirm() -> None:
            name = name_var.get().strip().upper().replace(" ", "_")
            if not name:
                return
            if name in self.cfg["states"]:
                messagebox.showerror("Auto-switcher", f"'{name}' already exists.", parent=dialog)
                return
            self.cfg["states"][name] = 2
            self._sync_managed_inputs()
            self._save_config()
            self._selected_state = name
            self._refresh_template_list()
            dialog.destroy()

        btns = ttk.Frame(dialog)
        btns.pack(pady=12)
        ttk.Button(btns, text="Create", command=confirm).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)
        dialog.bind("<Return>", lambda _e: confirm())

    def _delete_template(self) -> None:
        if not self._selected_state:
            return
        state = self._selected_state
        if state == "NONE":
            messagebox.showerror("Auto-switcher", "NONE can't be deleted (it's the idle state).")
            return
        if not messagebox.askyesno("Auto-switcher", f"Delete the template '{state}'?\n"
                                    "Associated screenshots are not removed from disk."):
            return
        self.cfg["states"].pop(state, None)
        for rule in self.cfg["detector"]["rules"]:
            if rule.get("state") == state:
                rule["enabled"] = False
        self._sync_managed_inputs()
        self._save_config()
        self._selected_state = None
        self._refresh_template_list()

    def _on_input_changed(self) -> None:
        if not self._selected_state:
            return
        try:
            number = int(self.input_var.get())
        except ValueError:
            return
        self.cfg["states"][self._selected_state] = number
        self._sync_managed_inputs()
        self._save_config()

    def _on_enabled_changed(self) -> None:
        if not self._selected_state:
            return
        for rule in self.cfg["detector"]["rules"]:
            if rule.get("state") == self._selected_state:
                rule["enabled"] = bool(self.enabled_var.get())
        self._save_config()

    # --------------------------------------------------------------- screenshots

    def _choose_primary_shot(self) -> None:
        if not self._selected_state:
            return
        path = filedialog.askopenfilename(title="Choose screenshot", filetypes=[("PNG images", "*.png")])
        if not path:
            return
        dest = os.path.join(self.shots_dir, state_to_primary_filename(self._selected_state))
        shutil.copy(path, dest)
        self._set_thumb(dest)

    def _remove_primary_shot(self) -> None:
        if not self._selected_state:
            return
        dest = os.path.join(self.shots_dir, state_to_primary_filename(self._selected_state))
        if os.path.exists(dest):
            os.remove(dest)
        self._set_thumb(None)

    def _add_verif_shot(self) -> None:
        if not self._selected_state:
            return
        path = filedialog.askopenfilename(title="Add verification screenshot",
                                          filetypes=[("PNG images", "*.png")])
        if not path:
            return
        note = os.path.splitext(os.path.basename(path))[0][:40]
        dest_name = f"{self._selected_state}_{note}.png"
        dest = os.path.join(self.testdata_dir, dest_name)
        i = 2
        while os.path.exists(dest):
            dest = os.path.join(self.testdata_dir, f"{self._selected_state}_{note}_{i}.png")
            i += 1
        shutil.copy(path, dest)
        self._show_template(self._selected_state)

    def _remove_verif_shot(self) -> None:
        sel = self.verif_list.curselection()
        if not sel or not self._selected_state:
            return
        fname = self.verif_list.get(sel[0])
        path = os.path.join(self.testdata_dir, fname)
        if os.path.exists(path):
            os.remove(path)
        self._show_template(self._selected_state)

    # --------------------------------------------------------------- verify

    def _run_verify_all(self) -> None:
        known = set(self._states())
        cases = discover_cases(self.shots_dir, self.testdata_dir, known)
        if not cases:
            self._log("No screenshots to verify: assign at least one screenshot to a template.")
            return
        results = run_cases(self.cfg, self.base_dir, cases)
        ok = sum(1 for r in results if r.ok)
        self._log(f"--- Verify all: {ok}/{len(results)} correct ---")
        for r in results:
            tag = "OK  " if r.ok else "FAIL"
            self._log(f"[{tag}] {r.case.label:32s} expected={r.case.expected:10s} got={r.got}")
        if ok == len(results):
            messagebox.showinfo("Auto-switcher", f"All good: {ok}/{len(results)} screenshots recognized correctly.")
        else:
            messagebox.showwarning("Auto-switcher", f"{len(results) - ok} out of {len(results)} screenshots were "
                                    "NOT recognized as expected. See the log for details.")

    # --------------------------------------------------------------- recalibrate

    def _open_recalibrate(self) -> None:
        known = set(self._states())
        cases = discover_cases(self.shots_dir, self.testdata_dir, known)
        if len(cases) < 2:
            messagebox.showinfo("Auto-switcher", "You need at least a couple of screenshots to recalibrate.")
            return
        suggestions = suggest_thresholds(self.cfg, self.base_dir, cases)

        dialog = tk.Toplevel(self)
        dialog.title("Recalibrate thresholds")
        dialog.geometry("720x420")
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(dialog, text="Thresholds computed from the current screenshots. Check the ones to apply.",
                 font=FONT).pack(anchor="w", padx=10, pady=(10, 4))

        cols = ("probe", "current", "suggested", "margin", "note")
        tree = ttk.Treeview(dialog, columns=cols, show="headings", selectmode="none", height=8)
        for c, w in zip(cols, (110, 130, 130, 90, 260)):
            tree.heading(c, text=c.capitalize())
            tree.column(c, width=w, anchor="w")
        tree.pack(fill="both", expand=True, padx=10)

        applicable: dict[str, ThresholdSuggestion] = {}
        checked: dict[str, tk.BooleanVar] = {}
        for s in suggestions:
            current = f"on={s.current_on} off={s.current_off}"
            if s.on is None:
                tree.insert("", "end", iid=s.roi, values=(s.roi, current, "-", "-", s.warning or ""))
            else:
                suggested = f"on={s.on} off={s.off}"
                note = s.warning or "ok"
                tree.insert("", "end", iid=s.roi, values=(s.roi, current, suggested, f"{s.margin:.3f}", note))
                applicable[s.roi] = s
                var = tk.BooleanVar(value=True)
                checked[s.roi] = var

        def apply() -> None:
            n = 0
            for roi, var in checked.items():
                if not var.get():
                    continue
                s = applicable[roi]
                self.cfg["detector"]["rois"][roi]["on"] = s.on
                self.cfg["detector"]["rois"][roi]["off"] = s.off
                n += 1
            if n:
                self._save_config()
                self._log(f"Recalibration applied to {n} probe(s).")
            dialog.destroy()

        btns = ttk.Frame(dialog)
        btns.pack(pady=10)
        ttk.Label(btns, text="Probes with no suggested threshold have insufficient data or can no "
                 "longer discriminate (see note) and should be left alone.", font=FONT,
                 wraplength=680, justify="left").pack(pady=(0, 8))
        ttk.Button(btns, text="Apply selected", command=apply).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)

    # --------------------------------------------------------------- monitor/vmix

    def _refresh_status(self) -> None:
        ok, detail = wizard.check_vmix(self.config_path)
        self.status_var.set(f"vMix: {'OK' if ok else 'UNREACHABLE'} - {detail}")
        self.monitor_var.set(f"Monitor: {wizard.current_monitor(self.config_path)}")

    def _open_monitor_picker(self) -> None:
        try:
            previews = wizard.save_previews(self.base_dir)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Auto-switcher", f"Could not read the monitors: {exc}")
            return
        if not previews:
            messagebox.showinfo("Auto-switcher", "No monitor found.")
            return

        dialog = tk.Toplevel(self)
        dialog.title("Choose the monitor")
        dialog.transient(self)
        dialog.grab_set()
        ttk.Label(dialog, text="Click the preview of the monitor showing the Filipino Zoom feed full-screen.",
                 font=FONT).pack(padx=10, pady=(10, 6))

        row = ttk.Frame(dialog)
        row.pack(padx=10, pady=(0, 10))
        images: list[tk.PhotoImage] = []

        def pick(idx: int) -> None:
            wizard.apply_monitor(self.config_path, idx)
            self.cfg = load_config(self.config_path)
            self._refresh_status()
            dialog.destroy()

        for idx, path, (w, h) in previews:
            col = ttk.Frame(row)
            col.pack(side="left", padx=8)
            img = _thumb(path, max_w=280, max_h=170)
            if img is not None:
                images.append(img)
                btn = ttk.Button(col, image=img, command=lambda i=idx: pick(i))
                btn.pack()
            ttk.Label(col, text=f"Monitor {idx}  ({w}x{h})", font=FONT).pack(pady=(4, 0))
        dialog._images_ref = images  # avoid garbage collection

    # --------------------------------------------------------------- start/stop

    def _start_common(self, dry_run: bool) -> bool:
        if self.switcher is not None:
            messagebox.showinfo("Auto-switcher", "Already running. Press STOP first.")
            return False
        self.cfg = load_config(self.config_path)
        setup_logging(self.base_dir, verbose=False)
        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        self._queue_handler = handler

        try:
            self.switcher = Switcher(self.cfg, self.base_dir, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Auto-switcher", f"Could not start: {exc}")
            logging.getLogger().removeHandler(handler)
            self.switcher = None
            return False

        self.switcher_thread = threading.Thread(target=self.switcher.run, daemon=True)
        self.switcher_thread.start()
        self.run_btn_test.configure(state="disabled")
        self.run_btn_live.configure(state="disabled")
        self.run_btn_stop.configure(state="normal")
        return True

    def _start_test(self) -> None:
        if self._start_common(dry_run=True):
            self._log("--- TEST started (no commands sent to vMix) ---")

    def _start_live(self) -> None:
        if not messagebox.askyesno("Auto-switcher", "Go LIVE? This will actually command vMix."):
            return
        if self._start_common(dry_run=False):
            self._log("--- GOING LIVE ---")

    def _stop_switcher(self) -> None:
        if self.switcher is None:
            return
        self.switcher.stop()
        self._log("--- stopped ---")
        logging.getLogger().removeHandler(self._queue_handler)
        self.switcher = None
        self.switcher_thread = None
        self.run_btn_test.configure(state="normal")
        self.run_btn_live.configure(state="normal")
        self.run_btn_stop.configure(state="disabled")

    def _poll_log_queue(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self._log(line)
        except queue.Empty:
            pass
        self.after(150, self._poll_log_queue)

    def on_close(self) -> None:
        if self.switcher is not None:
            self.switcher.stop()
        self.destroy()


def main() -> int:
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
