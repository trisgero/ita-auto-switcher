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
from .version import version_label
from .vmix import VmixClient, VmixError
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
        self._selected_kind: str = "state"  # "state" or "overlay"
        self._selected_state: str | None = None
        self._selected_overlay: str | None = None
        self._list_kinds: list[tuple[str, str]] = []

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.switcher: Switcher | None = None
        self.switcher_thread: threading.Thread | None = None

        self._build_ui()
        self._refresh_template_list()
        self._refresh_status()
        self.after(150, self._poll_log_queue)
        self.after(300, self._show_startup_summary)

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
        ttk.Button(top, text="Input & template summary...", command=self._show_startup_summary).pack(
            side="left", padx=(6, 0))

        self.run_btn_test = ttk.Button(top, text="TEST (doesn't touch vMix)", command=self._start_test)
        self.run_btn_live = ttk.Button(top, text="GO LIVE", command=self._start_live)
        self.run_btn_stop = ttk.Button(top, text="STOP", command=self._stop_switcher, state="disabled")
        self.run_btn_stop.pack(side="right")
        self.run_btn_live.pack(side="right", padx=(0, 6))
        self.run_btn_test.pack(side="right", padx=(0, 6))

        version_row = ttk.Frame(self, padding=(8, 0, 8, 4))
        version_row.pack(side="top", fill="x")
        ttk.Label(version_row, text=f"Build: {version_label()}", font=("Segoe UI", 8),
                 foreground="#888").pack(side="left")

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
        self.delete_btn = ttk.Button(btns, text="Delete", command=self._delete_template)
        self.delete_btn.pack(side="left", padx=(6, 0))

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

        # Only shown for overlays (lis_box, lower_third, ...): which vMix
        # OverlayInput channel toggles them. States don't have a channel.
        # Columns 3/4, leaving column 2 free for enabled_check in both modes.
        self.channel_label = ttk.Label(form, text="Channel:", font=FONT)
        self.channel_var = tk.StringVar()
        self.channel_entry = ttk.Spinbox(form, from_=1, to=99, textvariable=self.channel_var, width=6,
                                         command=self._on_channel_changed)
        self.channel_entry.bind("<FocusOut>", lambda _e: self._on_channel_changed())
        self.channel_entry.bind("<Return>", lambda _e: self._on_channel_changed())
        self.channel_label.grid(row=0, column=3, sticky="w", padx=(16, 0))
        self.channel_entry.grid(row=0, column=4, sticky="w", padx=(6, 0))

        # Only shown for overlays: which OTHER overlays, when on, hide this
        # one - one checkbox per other overlay, reflecting config's
        # "suppressed_by" list for the selected one. States don't use this.
        self.suppressed_section = ttk.Frame(right)
        self.suppressed_section.pack(anchor="w", fill="x", pady=(6, 0))
        ttk.Label(self.suppressed_section, text="Suppressed by (hidden while these are on):",
                 font=FONT).pack(anchor="w")
        self.suppressed_checks_frame = ttk.Frame(self.suppressed_section)
        self.suppressed_checks_frame.pack(anchor="w", pady=(2, 0))
        self._suppressed_vars: dict[str, tk.BooleanVar] = {}

        self.after_suppressed_separator = ttk.Separator(right, orient="horizontal")
        self.after_suppressed_separator.pack(fill="x", pady=8)

        ttk.Label(right, text="Primary screenshot (calibration)", font=FONT_BOLD).pack(anchor="w")
        shot_row = ttk.Frame(right)
        shot_row.pack(fill="x", pady=(4, 4))
        self.thumb_label = ttk.Label(shot_row, text="(none)", relief="groove", anchor="center")
        self.thumb_label.pack(side="left")
        shot_btns = ttk.Frame(shot_row)
        shot_btns.pack(side="left", fill="y", padx=(10, 0))
        self.choose_shot_btn = ttk.Button(shot_btns, text="Choose screenshot...", command=self._choose_primary_shot)
        self.choose_shot_btn.pack(fill="x")
        self.remove_shot_btn = ttk.Button(shot_btns, text="Remove", command=self._remove_primary_shot)
        self.remove_shot_btn.pack(fill="x", pady=(4, 0))

        ttk.Label(right, text="Additional verification screenshots", font=FONT_BOLD).pack(anchor="w", pady=(8, 0))
        verif_row = ttk.Frame(right)
        verif_row.pack(fill="both", expand=False, pady=(4, 0))
        self.verif_list = tk.Listbox(verif_row, font=FONT, height=5)
        self.verif_list.pack(side="left", fill="both", expand=True)
        verif_btns = ttk.Frame(verif_row)
        verif_btns.pack(side="left", fill="y", padx=(10, 0))
        self.add_verif_btn = ttk.Button(verif_btns, text="Add...", command=self._add_verif_shot)
        self.add_verif_btn.pack(fill="x")
        self.remove_verif_btn = ttk.Button(verif_btns, text="Remove", command=self._remove_verif_shot)
        self.remove_verif_btn.pack(fill="x", pady=(4, 0))
        self._shot_buttons = [self.choose_shot_btn, self.remove_shot_btn, self.add_verif_btn, self.remove_verif_btn]

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

    def _overlays(self) -> list[str]:
        return [k for k in self.cfg.get("overlays", {}) if not k.startswith("_")]

    def _log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # --------------------------------------------------------------- template list

    def _refresh_template_list(self) -> None:
        self.template_list.delete(0, "end")
        self._list_kinds = []
        for state in self._states():
            self.template_list.insert("end", state)
            self._list_kinds.append(("state", state))
        overlays = self._overlays()
        if overlays:
            self.template_list.insert("end", "── Overlays ──")
            self._list_kinds.append(("separator", ""))
            for name in overlays:
                self.template_list.insert("end", f"  {name}")
                self._list_kinds.append(("overlay", name))

        target = None
        if self._selected_kind == "state" and self._selected_state in self._states():
            target = ("state", self._selected_state)
        elif self._selected_kind == "overlay" and self._selected_overlay in overlays:
            target = ("overlay", self._selected_overlay)
        if target is not None:
            idx = self._list_kinds.index(target)
            self.template_list.selection_set(idx)
            self._show_item(*target)
        elif self._states():
            self.template_list.selection_set(0)
            self._show_item("state", self._states()[0])

    def _on_template_selected(self, _evt=None) -> None:
        sel = self.template_list.curselection()
        if not sel:
            return
        kind, name = self._list_kinds[sel[0]]
        if kind == "separator":
            return
        self._show_item(kind, name)

    def _show_item(self, kind: str, name: str) -> None:
        if kind == "state":
            self._show_state(name)
        else:
            self._show_overlay(name)

    def _show_state(self, state: str) -> None:
        self._selected_kind = "state"
        self._selected_state = state
        self._selected_overlay = None
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
        self.channel_label.grid_remove()
        self.channel_entry.grid_remove()
        self.enabled_check.configure(text="Rule active")
        self.enabled_check.grid()
        self.suppressed_section.pack_forget()

        if rule is not None:
            self.enabled_check.configure(state="normal")
            self.enabled_var.set(bool(rule.get("enabled", True)))
        else:
            self.enabled_check.configure(state="disabled")
            self.enabled_var.set(has_rule)

        self.delete_btn.configure(state="normal")
        for b in self._shot_buttons:
            b.configure(state="normal")

        primary_path = self._primary_shot_path(state)
        self._set_thumb(primary_path)

        self.verif_list.delete(0, "end")
        for case in self._verif_cases(state):
            self.verif_list.insert("end", os.path.basename(case))

    def _show_overlay(self, name: str) -> None:
        self._selected_kind = "overlay"
        self._selected_overlay = name
        self._selected_state = None
        probe = self.cfg["overlays"][name]
        self.detail_title.configure(text=f"{name}  (independent overlay)")
        self.detail_note.configure(
            text="Independent overlay, toggled on/off via vMix's OverlayInput<channel> "
            "API, separate from the verse-card templates above. Channel and vMix input "
            "here must match how this overlay is actually wired in vMix. Its detection "
            "geometry/thresholds are calibrated separately (config.json's \"overlays\" "
            "section and calibrate.py), not from a screenshot picked here."
        )

        self.input_var.set(str(probe.get("vmix_input", 0)))
        self.channel_var.set(str(probe.get("channel", 0)))
        self.enabled_check.configure(text="Enabled", state="normal")
        self.enabled_var.set(bool(probe.get("enabled", True)))
        self.enabled_check.grid()
        self.channel_label.grid()
        self.channel_entry.grid()
        self.suppressed_section.pack(anchor="w", fill="x", pady=(6, 0), before=self.after_suppressed_separator)
        self._rebuild_suppressed_checks(name)

        self.delete_btn.configure(state="disabled")
        for b in self._shot_buttons:
            b.configure(state="disabled")

        self._set_thumb(None)
        self.verif_list.delete(0, "end")

    def _rebuild_suppressed_checks(self, name: str) -> None:
        for w in self.suppressed_checks_frame.winfo_children():
            w.destroy()
        self._suppressed_vars = {}

        others = [o for o in self._overlays() if o != name]
        if not others:
            ttk.Label(self.suppressed_checks_frame, text="(no other overlays)", font=FONT).pack(anchor="w")
            return

        current = set(self.cfg["overlays"][name].get("suppressed_by", []))
        for other in others:
            var = tk.BooleanVar(value=other in current)
            self._suppressed_vars[other] = var
            ttk.Checkbutton(self.suppressed_checks_frame, text=other, variable=var,
                            command=lambda o=other: self._on_suppressed_toggle(o)).pack(anchor="w")

    def _on_suppressed_toggle(self, other: str) -> None:
        if self._selected_kind != "overlay" or not self._selected_overlay:
            return
        probe = self.cfg["overlays"][self._selected_overlay]
        current = list(probe.get("suppressed_by", []))
        want_on = self._suppressed_vars[other].get()
        if want_on and other not in current:
            current.append(other)
        elif not want_on and other in current:
            current.remove(other)
        probe["suppressed_by"] = current
        self._save_config()

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
            self._selected_kind = "state"
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
        try:
            number = int(self.input_var.get())
        except ValueError:
            return
        if self._selected_kind == "state" and self._selected_state:
            self.cfg["states"][self._selected_state] = number
            self._sync_managed_inputs()
            self._save_config()
        elif self._selected_kind == "overlay" and self._selected_overlay:
            self.cfg["overlays"][self._selected_overlay]["vmix_input"] = number
            self._save_config()

    def _on_channel_changed(self) -> None:
        if self._selected_kind != "overlay" or not self._selected_overlay:
            return
        try:
            number = int(self.channel_var.get())
        except ValueError:
            return
        self.cfg["overlays"][self._selected_overlay]["channel"] = number
        self._save_config()

    def _on_enabled_changed(self) -> None:
        if self._selected_kind == "state" and self._selected_state:
            for rule in self.cfg["detector"]["rules"]:
                if rule.get("state") == self._selected_state:
                    rule["enabled"] = bool(self.enabled_var.get())
            self._save_config()
        elif self._selected_kind == "overlay" and self._selected_overlay:
            self.cfg["overlays"][self._selected_overlay]["enabled"] = bool(self.enabled_var.get())
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

    # ------------------------------------------------------ startup summary

    def _vmix_input_titles(self) -> dict[int, str]:
        """Best-effort: input number -> title as vMix sees it right now.
        Empty if vMix isn't reachable yet (e.g. still starting up)."""
        vmix_cfg = {k: v for k, v in self.cfg["vmix"].items() if not k.startswith("_")}
        try:
            return VmixClient(**vmix_cfg).state().inputs
        except VmixError:
            return {}

    def _summary_rows(self) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str, str]]]:
        titles = self._vmix_input_titles()

        state_rows = []
        for name, number in self.cfg["states"].items():
            if name.startswith("_"):
                continue
            state_rows.append((name, str(number), titles.get(int(number), "")))

        overlay_rows = []
        for name, probe in self.cfg.get("overlays", {}).items():
            if name.startswith("_"):
                continue
            channel = probe.get("channel")
            vmix_input = probe.get("vmix_input")
            title = titles.get(int(vmix_input), "") if vmix_input is not None else ""
            if not probe.get("enabled", True):
                title = f"{title} [DISABLED]" if title else "[DISABLED]"
            overlay_rows.append((name, str(channel), str(vmix_input), title))

        return state_rows, overlay_rows

    def _show_startup_summary(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("vMix Input & Template Summary")
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text="Verify that every template and overlay is mapped to the right vMix input "
            "before going live. Double-click a value in the Input column to change it.",
            font=FONT, wraplength=560, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        ttk.Label(dialog, text="States (verse card)", font=FONT_BOLD).pack(anchor="w", padx=12)
        cols = ("template", "input", "vmix_title")
        tree_states = ttk.Treeview(dialog, columns=cols, show="headings", selectmode="none", height=7)
        for c, label, w in zip(cols, ("Template", "Input", "vMix Title"), (120, 60, 300)):
            tree_states.heading(c, text=label)
            tree_states.column(c, width=w, anchor="w")
        tree_states.pack(fill="x", padx=12, pady=(2, 10))

        ttk.Label(dialog, text="Independent overlays (lis box, lower third, ...)", font=FONT_BOLD).pack(
            anchor="w", padx=12)
        cols2 = ("overlay", "channel", "input", "vmix_title")
        tree_overlays = ttk.Treeview(dialog, columns=cols2, show="headings", selectmode="none", height=4)
        for c, label, w in zip(cols2, ("Overlay", "Channel", "Input", "vMix Title"), (140, 60, 60, 260)):
            tree_overlays.heading(c, text=label)
            tree_overlays.column(c, width=w, anchor="w")
        tree_overlays.pack(fill="x", padx=12, pady=(2, 4))

        note_var = tk.StringVar()
        ttk.Label(dialog, textvariable=note_var, font=FONT, foreground="#a33").pack(
            anchor="w", padx=12, pady=(0, 8))

        def populate() -> None:
            state_rows, overlay_rows = self._summary_rows()
            tree_states.delete(*tree_states.get_children())
            for tname, number, title in state_rows:
                tree_states.insert("", "end", iid=tname, values=(tname, number, title or "-"))
            tree_overlays.delete(*tree_overlays.get_children())
            for oname, channel, vmix_input, title in overlay_rows:
                tree_overlays.insert("", "end", iid=oname, values=(oname, channel, vmix_input, title or "-"))
            if not self._vmix_input_titles():
                note_var.set("vMix unreachable: can't show the real input titles, only the "
                              "configured numbers. Click \"Refresh\" once vMix is ready.")
            else:
                note_var.set("")

        def edit_input_cell(tree: ttk.Treeview, column_id: str, on_commit) -> None:
            def on_double_click(event: tk.Event) -> None:
                if tree.identify("region", event.x, event.y) != "cell":
                    return
                if tree.identify_column(event.x) != column_id:
                    return
                row = tree.identify_row(event.y)
                if not row:
                    return
                bbox = tree.bbox(row, column_id)
                if not bbox:
                    return
                x, y, w, h = bbox
                current = tree.set(row, column_id)
                edit_var = tk.StringVar(value=current)
                entry = ttk.Entry(tree, textvariable=edit_var, font=FONT)
                entry.place(x=x, y=y, width=w, height=h)
                entry.focus_set()
                entry.selection_range(0, "end")

                def finish(_evt=None) -> None:
                    entry.destroy()

                def commit(_evt=None) -> None:
                    new_value = edit_var.get().strip()
                    finish()
                    if new_value and new_value != current:
                        on_commit(row, new_value)

                entry.bind("<Return>", commit)
                entry.bind("<FocusOut>", commit)
                entry.bind("<Escape>", finish)

            tree.bind("<Double-1>", on_double_click)

        def commit_state_input(state: str, new_value: str) -> None:
            try:
                number = int(new_value)
            except ValueError:
                messagebox.showerror("Auto-switcher", f"'{new_value}' is not a valid input number.")
                return
            self.cfg["states"][state] = number
            self._sync_managed_inputs()
            self._save_config()
            populate()
            if self._selected_kind == "state" and self._selected_state == state:
                self._show_state(state)

        def commit_overlay_input(overlay: str, new_value: str) -> None:
            try:
                number = int(new_value)
            except ValueError:
                messagebox.showerror("Auto-switcher", f"'{new_value}' is not a valid input number.")
                return
            self.cfg["overlays"][overlay]["vmix_input"] = number
            self._save_config()
            populate()
            if self._selected_kind == "overlay" and self._selected_overlay == overlay:
                self._show_overlay(overlay)

        edit_input_cell(tree_states, "#2", commit_state_input)
        edit_input_cell(tree_overlays, "#3", commit_overlay_input)

        populate()

        btns = ttk.Frame(dialog)
        btns.pack(pady=(0, 12))
        ttk.Button(btns, text="Refresh", command=populate).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=dialog.destroy).pack(side="left", padx=4)

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
