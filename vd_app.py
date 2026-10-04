"""
Virtual Dowel - vd_app.py  v2.1  (2026-10-04)
Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

The Virtual Dowel window. One window for the whole job:

  prepare    before probing (the custom cut C80 opens it): side-B files, reference holes, stock box,
             probe settings, checks. Start writes vd_run.sbp, which ShopBot runs next.
  align      after probing (vd_run.sbp opens it): aligns every file at once and lists PASS / FAIL.
  calresult  after a probe calibration run: the probe's numbers, saved into the settings.
  rough      after a rough locate (half slots): places the bay cut file and says what to run next.

All the work is done by vd_align.py; this file is only the window.
"""
from __future__ import annotations

import datetime
import math
import os
import traceback
import webbrowser
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import vd_align as va

MONO = ("Consolas", 9)
GREEN, RED, AMBER = "#1b7f3b", "#b3261e", "#8a5a00"


def _num(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return ""
    return f"{v:.4f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


class App:
    """mode: 'prepare', 'align' or 'calresult'. result_code is what the process exits with
    (0 = done / started, 3 = closed without doing anything, 1 = error)."""

    def __init__(self, mode: str, data_path: Optional[str] = None, root: Optional[tk.Tk] = None,
                 auto: bool = True):
        self.mode = mode
        self.data_path = data_path or {"calresult": va.CAL_DATA, "rough": va.ROUGH_DATA}.get(mode, va.DEFAULT_DATA)
        self.root = root or tk.Tk()
        self.result_code = 3
        self.jobs: List[str] = []
        self.results: Dict[str, va.Result] = {}
        self.hole_rows: List[Tuple[tk.StringVar, tk.StringVar, tk.StringVar, ttk.Frame]] = []
        self.pd: Optional[va.ProbeData] = None
        self.cal: Optional[va.Calibration] = None
        self.root.title("Virtual Dowel")
        self.root.minsize(820, 560)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.report_callback_exception = self._on_error
        self.stale = False
        try:
            ttk.Style(self.root).theme_use("vista")
        except tk.TclError:
            pass
        if mode == "calresult":
            self._build_calresult()
        elif mode == "rough":
            self._build_roughresult()
        else:
            self._build_main()
        self._bring_to_front()
        if auto and mode == "align":
            self.root.after(150, self.on_align_auto)

    # ------------------------------------------------------------------ dialogs (tests replace these)
    def ask_yes_no(self, title: str, text: str) -> bool:
        return messagebox.askyesno(title, text, parent=self.root)

    def info(self, title: str, text: str) -> None:
        messagebox.showinfo(title, text, parent=self.root)

    def error(self, title: str, text: str) -> None:
        messagebox.showerror(title, text, parent=self.root)

    def pick_files(self) -> List[str]:
        st = va._load_state()
        got = filedialog.askopenfilenames(
            parent=self.root, title="Side-B job files (.sbp) - Ctrl-click or Shift-click for several",
            initialdir=st.get("last_dir") or os.path.expanduser("~"),
            filetypes=[("ShopBot part files", "*.sbp"), ("All files", "*.*")])
        files = list(self.root.tk.splitlist(got)) if got else []
        if files:
            st["last_dir"] = os.path.dirname(files[0])
            va._save_state(st)
        return files

    def _untop(self) -> None:
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:                  # the window was closed already
            pass

    def _on_error(self, exc, val, tb) -> None:
        # under pythonw there is no console: show and log anything a button handler throws
        text = "".join(traceback.format_exception(exc, val, tb))
        va._log("WINDOW ERROR\n" + text)
        try:
            self.error("Virtual Dowel - problem", f"{val}\n\nDetails are in vd_launch.log.")
        except tk.TclError:
            pass

    def _bring_to_front(self) -> None:
        # opened by ShopBot's SHELL, the window would otherwise start behind ShopBot
        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(800, self._untop)
            self.root.focus_force()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ layout
    def _build_main(self) -> None:
        r = self.root
        outer = ttk.Frame(r, padding=10)
        outer.pack(fill="both", expand=True)
        steps = ("1  Prepare", "2  Probe (ShopBot)", "3  Align")
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(0, 8))
        cur = 0 if self.mode == "prepare" else 2
        for i, txt in enumerate(steps):
            tk.Label(bar, text=txt, padx=10, pady=3, font=("Segoe UI", 10, "bold" if i == cur else "normal"),
                     fg="white" if i == cur else "#555", bg="#2f5d8a" if i == cur else "#e4e4e4").pack(
                side="left", padx=(0, 4))
        self.subtitle = ttk.Label(bar, text="")
        self.subtitle.pack(side="right")
        if self.mode == "prepare":
            lf = ttk.Frame(outer)
            lf.pack(fill="x", pady=(0, 6))
            ttk.Label(lf, text="Locate with").pack(side="left")
            self.loc_var = tk.StringVar(value="holes")
            for val, txt in (("holes", "Through holes"), ("slots", "Half slots - bay cut first"),
                             ("through", "Through slots")):
                ttk.Radiobutton(lf, text=txt, value=val, variable=self.loc_var,
                                command=self._on_locate_mode).pack(side="left", padx=(10, 0))
            ttk.Button(lf, text="Guide", command=self.on_guide).pack(side="right")

        # side-B files
        jf = ttk.LabelFrame(outer, text="Side-B job files - one per tool", padding=6)
        jf.pack(fill="both", expand=False)
        cols = ("file", "result", "rotation", "spacing", "output")
        self.tree = ttk.Treeview(jf, columns=cols, show="headings", height=5, selectmode="browse")
        for c, w, t in (("file", 260, "File"), ("result", 80, "Result"), ("rotation", 90, "Rotation"),
                        ("spacing", 110, "Spacing error"), ("output", 260, "Output")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.tag_configure("pass", foreground=GREEN)
        self.tree.tag_configure("fail", foreground=RED)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_selected_report())
        jb = ttk.Frame(jf)
        jb.pack(side="left", fill="y", padx=(6, 0))
        ttk.Button(jb, text="Add files...", command=self.on_add_files).pack(fill="x")
        ttk.Button(jb, text="Remove", command=self.on_remove_file).pack(fill="x", pady=(4, 0))

        mid = ttk.Frame(outer)
        mid.pack(fill="x", pady=(8, 0))
        # reference holes
        hf = ttk.LabelFrame(mid, text="Reference holes - side-B design coordinates", padding=6)
        hf.pack(side="left", fill="both", expand=True)
        self.holes_frame = hf
        self.mid = mid
        hdr = ttk.Frame(hf)
        hdr.pack(fill="x")
        for t, w in (("#", 3), ("X", 11), ("Y", 11), ("Diameter", 11)):
            ttk.Label(hdr, text=t, width=w).pack(side="left", padx=2)
        self.holes_box = ttk.Frame(hf)
        self.holes_box.pack(fill="x")
        self.add_hole_btn = ttk.Button(hf, text="+ Add hole", command=lambda: self.add_hole_row())
        self.add_hole_btn.pack(anchor="w", pady=(4, 0))

        # stock box
        sf = ttk.LabelFrame(mid, text="Stock box - only for files without a stock size (Fusion)", padding=6)
        sf.pack(side="left", fill="both", padx=(8, 0))
        self.stock_vars = [tk.StringVar() for _ in range(4)]
        g = ttk.Frame(sf)
        g.pack(anchor="w")
        for i, t in enumerate(("X min", "Y min", "X max", "Y max")):
            ttk.Label(g, text=t).grid(row=0, column=i, padx=2, sticky="w")
            ttk.Entry(g, textvariable=self.stock_vars[i], width=9).grid(row=1, column=i, padx=2)
        self.stock_note = ttk.Label(sf, text="", wraplength=330, justify="left")
        self.stock_note.pack(anchor="w", pady=(4, 0))

        # half slots: the bay cut file (made in Fusion) and the optional key notch
        self.bay_var = tk.StringVar()
        self.key_vars = (tk.StringVar(), tk.StringVar())
        self.bay_frame = ttk.LabelFrame(outer, text="Half slots - bay cut file from Fusion (placed by the rough locate)",
                                        padding=6)
        bf0 = ttk.Frame(self.bay_frame)
        bf0.pack(fill="x")
        ttk.Entry(bf0, textvariable=self.bay_var).pack(side="left", fill="x", expand=True)
        ttk.Button(bf0, text="Choose...", command=self.on_pick_bay).pack(side="left", padx=(6, 0))
        bf1 = ttk.Frame(self.bay_frame)
        bf1.pack(fill="x", pady=(4, 0))
        ttk.Label(bf1, text="Key notch X, Y (only if cut - irregular stock)").pack(side="left")
        for v in self.key_vars:
            ttk.Entry(bf1, textvariable=v, width=9).pack(side="left", padx=(6, 0))

        # probe settings (prepare) or probe data (align)
        if self.mode == "prepare":
            self._build_probe_settings(outer)
        else:
            pf = ttk.LabelFrame(outer, text="Probe data", padding=6)
            pf.pack(fill="x", pady=(8, 0))
            self.probe_info = ttk.Label(pf, text="", justify="left", font=MONO)
            self.probe_info.pack(anchor="w")

        # messages / report
        mf = ttk.LabelFrame(outer, text="Checks and report", padding=4)
        mf.pack(fill="both", expand=True, pady=(8, 0))
        self.text = tk.Text(mf, height=10, wrap="word", font=MONO)
        sb = ttk.Scrollbar(mf, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)

        # buttons
        bf = ttk.Frame(outer)
        bf.pack(fill="x", pady=(8, 0))
        self.status = tk.Label(bf, text="", anchor="w", font=("Segoe UI", 10, "bold"))
        self.status.pack(side="left", fill="x", expand=True)
        ttk.Button(bf, text="Close", command=self.on_close).pack(side="right")
        if self.mode == "prepare":
            ttk.Button(bf, text="Start probing", command=self.on_start).pack(side="right", padx=(0, 6))
            self.rough_btn = ttk.Button(bf, text="Start rough locate", command=self.on_start_rough)
            ttk.Button(bf, text="Check", command=self.on_check).pack(side="right", padx=(0, 6))
        else:
            ttk.Button(bf, text="Align", command=self.on_align).pack(side="right", padx=(0, 6))

        self._load_initial()

    def _build_probe_settings(self, outer) -> None:
        pf = ttk.LabelFrame(outer, text="Probe", padding=6)
        pf.pack(fill="x", pady=(8, 0))
        self.machine = va.load_machine()
        self.mvars: Dict[str, tk.Variable] = {}
        for k in va.PROBE_FIELDS:
            kind = va.PROBE_FIELDS[k][0]
            v = self.machine[k]
            self.mvars[k] = tk.IntVar(value=int(v)) if kind == "bool" else tk.StringVar(value=_fmt(v))
        g = ttk.Frame(pf)
        g.pack(fill="x")
        ttk.Label(g, text="Input").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(g, from_=1, to=8, width=4, textvariable=self.mvars["pin"]).grid(row=0, column=1, padx=(2, 12))
        ttk.Label(g, text="Reads when deflected").grid(row=0, column=2, sticky="w")
        ttk.Combobox(g, values=("0", "1"), width=3, state="readonly",
                     textvariable=self.mvars["trig"]).grid(row=0, column=3, padx=(2, 12))
        ttk.Label(g, text="Ball diameter").grid(row=0, column=4, sticky="w")
        ttk.Entry(g, width=8, textvariable=self.mvars["tip"]).grid(row=0, column=5, padx=(2, 12))
        ttk.Label(g, text="Fine passes").grid(row=0, column=6, sticky="w")
        ttk.Spinbox(g, from_=1, to=20, width=4, textvariable=self.mvars["passes"]).grid(row=0, column=7, padx=2)
        g2 = ttk.Frame(pf)
        g2.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(g2, text="Turn the spindle 180° per hole (cancels a bent stylus)",
                        variable=self.mvars["spin180"]).pack(side="left")
        ttk.Checkbutton(g2, text="Finger test first", variable=self.mvars["test"]).pack(side="left", padx=12)
        self.sim_var = tk.IntVar(value=0)
        ttk.Checkbutton(g2, text="Dry run (no motion, in Preview)",
                        variable=self.sim_var).pack(side="left")
        g3 = ttk.Frame(pf)
        g3.pack(fill="x", pady=(4, 0))
        ttk.Button(g3, text="Advanced...", command=self.on_advanced).pack(side="left")
        ttk.Button(g3, text="Calibrate probe...", command=self.on_calibrate).pack(side="left", padx=6)
        cal = va.read_machine_file().get("calibration")
        txt = "No probe calibration yet."
        if isinstance(cal, dict):
            ecc = cal.get("ecc")
            txt = (f"Last calibration {cal.get('date', '?')}: repeatability {cal.get('repeat', 0):.4f}, "
                   f"effective ball {cal.get('eff_tip', 0):.4f}"
                   + (f", ball off spindle {math.hypot(*ecc):.4f}" if ecc else ""))
        self.cal_label = ttk.Label(g3, text=txt)
        self.cal_label.pack(side="left", padx=8)

    def _on_locate_mode(self) -> None:
        mode = self.loc_var.get()
        self.holes_frame.configure(text="Reference holes - side-B design coordinates" if mode == "holes" else
                                   "Slots - X and Y of the round-end centre and the width - side-B coordinates")
        if mode == "slots":
            self.bay_frame.pack(fill="x", pady=(8, 0), after=self.mid)
            self.rough_btn.pack(side="right", padx=(0, 6))
        else:
            self.bay_frame.pack_forget()
            self.rough_btn.pack_forget()

    def on_pick_bay(self) -> None:
        got = filedialog.askopenfilename(parent=self.root, title="Bay cut file (.sbp) from Fusion",
                                         filetypes=[("ShopBot part files", "*.sbp"), ("All files", "*.*")])
        if got:
            self.bay_var.set(got)

    def on_guide(self) -> None:
        path = os.path.join(va.HERE, "docs", "guide.html")
        if not os.path.isfile(path):
            self.error("Virtual Dowel", f"The guide is missing: {path}")
            return
        webbrowser.open("file:///" + path.replace(os.sep, "/").lstrip("/"))

    def read_key(self) -> Tuple[Optional[Tuple[float, float]], List[str]]:
        vals = [_num(v.get()) for v in self.key_vars]
        if all(v.get().strip() == "" for v in self.key_vars):
            return None, []
        if any(v is None for v in vals):
            return None, ["key notch: enter both X and Y, or leave both empty"]
        return (vals[0], vals[1]), []

    def _build_roughresult(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        self.status = tk.Label(outer, text="", anchor="w", font=("Segoe UI", 12, "bold"), fg="white",
                               bg="#2f5d8a", padx=10, pady=6)
        self.status.pack(fill="x")
        self.text = tk.Text(outer, height=22, width=100, wrap="word", font=MONO)
        self.text.pack(fill="both", expand=True, pady=(8, 0))
        bf = ttk.Frame(outer)
        bf.pack(fill="x", pady=(8, 0))
        ttk.Button(bf, text="Close", command=self.on_close).pack(side="right")
        try:
            res = va.rough_from_session(self.data_path)
        except (OSError, va.VDError) as e:
            self.status.configure(text="Rough locate - bay file NOT placed", bg=RED)
            self._set_text(f"{e}\n\nType C80 and start the rough locate again.")
            self.result_code = 1
            return
        if res.ok:
            name = os.path.basename(res.out_path)
            self.status.configure(text=f"Bay file placed - run {name}", bg=GREEN)
            self._set_text(f"Next:\n  1. FP {res.out_path}\n     Look at it in Preview first.\n"
                           "  2. Zero Z with the bay cutter. Do NOT zero X or Y.\n  3. Run it: it cuts the bays.\n"
                           "  4. Vacuum the slots and bays. Fit the probe.\n  5. Type C80 and click Start probing.\n\n"
                           + res.report)
            self.result_code = 0
        else:
            self.status.configure(text="Rough locate - bay file NOT placed", bg=RED)
            self._set_text("Nothing was written. Fix the problems below, then type C80 and start the rough "
                           "locate again.\n\n" + res.report)
            self.result_code = 1

    def _build_calresult(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        self.status = tk.Label(outer, text="", anchor="w", font=("Segoe UI", 12, "bold"), fg="white",
                               bg="#2f5d8a", padx=10, pady=6)
        self.status.pack(fill="x")
        self.text = tk.Text(outer, height=18, width=96, wrap="word", font=MONO)
        self.text.pack(fill="both", expand=True, pady=(8, 0))
        bf = ttk.Frame(outer)
        bf.pack(fill="x", pady=(8, 0))
        ttk.Button(bf, text="Close", command=self.on_close).pack(side="right")
        self.save_btn = ttk.Button(bf, text="Save to probe settings", command=self.on_save_cal)
        self.save_btn.pack(side="right", padx=(0, 6))
        try:
            with open(self.data_path, "r", encoding="latin-1", newline="") as fh:
                pd = va.parse_probe_data(fh.read(), self.data_path)
            self.cal = va.analyze_calibration(pd)
            self.status.configure(text="Probe calibration" + ("  (simulated)" if self.cal.sim else ""))
            use_input, use_ball = va.calibration_usable(self.cal)
            m = va.apply_calibration(va.load_machine(), self.cal)
            changes = []
            if use_input:
                changes.append(f"probe input {m['pin']}, reads {m['trig']} when deflected")
            if use_ball:
                changes.append(f"ball diameter {m['tip']:.4f} in")
            body = va.calibration_report(self.cal)
            if changes:
                body += "\n\nSave to probe settings sets: " + "; ".join(changes) + "."
            else:
                body += "\n\nNothing from this run can be saved to the probe settings."
                self.save_btn.state(["disabled"])
            self._set_text(body)
        except (OSError, va.VDError) as e:
            self.status.configure(text="Probe calibration - no result", bg=RED)
            self._set_text(f"The calibration data could not be read:\n\n{e}")
            self.save_btn.state(["disabled"])

    # ------------------------------------------------------------------ initial values
    def _load_initial(self) -> None:
        sess = va.load_session()
        if sess.get("kind") == "cal":
            sess = sess.get("keep") or {}
        jobs = [j for j in sess.get("jobs", []) if os.path.isfile(j)]
        holes = sess.get("holes") or []
        stock = sess.get("stock")
        if self.mode == "align":
            try:
                with open(self.data_path, "r", encoding="latin-1", newline="") as fh:
                    self.pd = va.parse_probe_data(fh.read(), self.data_path)
                if self.pd.cal_ref is not None:
                    raise va.VDError("this is calibration data, not job data - probe the job's holes")
                self._show_probe_info(sess)
            except (OSError, va.VDError) as e:
                self.pd = None
                self.probe_info.configure(text=f"No usable probe data: {e}")
        for j in jobs:
            self._add_job(j)
        if not holes and jobs:
            h, b = va.batch_defaults(jobs, self.pd.nholes if self.pd else None)[:2]
            holes = [list(x) for x in (h or [])]
            stock = stock or b
        n = self.pd.nholes if self.pd else max(2, len(holes))
        for i in range(max(n, len(holes))):
            x, y, d = (holes[i] + [None, None, None])[:3] if i < len(holes) else (None, None, None)
            self.add_hole_row(x, y, d)
        if stock:
            for v, s in zip(self.stock_vars, stock):
                v.set(_fmt(s))
        if self.mode == "prepare":
            self.loc_var.set(sess.get("mode") or "holes")
            self.bay_var.set(sess.get("bay") or "")
            key = sess.get("key")
            if key:
                for v, k in zip(self.key_vars, key):
                    v.set(_fmt(k))
            self._on_locate_mode()
        self._refresh_stock_note()
        self.subtitle.configure(text="Choose the files and holes, then Start probing" if self.mode == "prepare"
                                else "Results for the probing just done")

    def _show_probe_info(self, sess: dict) -> None:
        pd = self.pd
        holes, _, _ = va.compute_holes(pd)
        when = datetime.datetime.fromtimestamp(os.path.getmtime(self.data_path)).strftime("%Y-%m-%d %H:%M")
        lines = [f"Probed {when}   {pd.nholes} holes   {pd.passes} fine passes"
                 + ("   spindle 0/180" if pd.spin180 else "") + ("   SIMULATED" if pd.sim else "")]
        for h in holes:
            lines.append(f"  hole {h.index}: centre X {h.center[0]:.4f}  Y {h.center[1]:.4f}   "
                         f"diameter {h.dia:.4f}   repeat {h.repeat:.4f}")
        prepared = sess.get("prepared")
        if prepared and os.path.getmtime(self.data_path) + 5 < prepared:
            self.stale = True
            lines.append("NOT USABLE: this probe data is OLDER than the last Start probing - that run did not "
                         "finish, so this data belongs to an earlier board position. Probe again (C80).")
        self.probe_info.configure(text="\n".join(lines))

    # ------------------------------------------------------------------ jobs
    def _add_job(self, path: str) -> None:
        path = os.path.abspath(path)
        if path in self.jobs:
            return
        self.jobs.append(path)
        self.tree.insert("", "end", iid=path, values=(os.path.basename(path), "", "", "", ""))

    def on_add_files(self) -> None:
        files = self.pick_files()
        first = not self.jobs
        for f in files:
            self._add_job(f)
        if first and files and not any(self._hole_texts()[i][0] for i in range(len(self.hole_rows))):
            holes, stock, _ = va.batch_defaults(self.jobs, self.pd.nholes if self.pd else None)
            if holes:
                self._set_holes(holes)
            if stock and not any(v.get().strip() for v in self.stock_vars):
                for v, s in zip(self.stock_vars, stock):
                    v.set(_fmt(s))
        self._refresh_stock_note()

    def on_remove_file(self) -> None:
        sel = self.tree.selection()
        for iid in sel:
            self.tree.delete(iid)
            self.jobs.remove(iid)
            self.results.pop(iid, None)
        self._refresh_stock_note()

    def _refresh_stock_note(self) -> None:
        need, ext = va.needs_stock(self.jobs)
        if not self.jobs:
            txt = "Add the side-B files first."
        elif need:
            txt = "Needed: at least one file has no stock size."
            if ext:
                txt += f"\nIts toolpaths span X {ext[0]:.3f} .. {ext[2]:.3f},  Y {ext[1]:.3f} .. {ext[3]:.3f}."
            txt += "\nUse Setup 2's coordinates, e.g. origin back-left: 0, -H, W, 0."
        else:
            txt = "Not needed: every file states its stock size."
        self.stock_note.configure(text=txt)

    # ------------------------------------------------------------------ holes
    def add_hole_row(self, x=None, y=None, d=None) -> None:
        if len(self.hole_rows) >= 6:
            return
        row = ttk.Frame(self.holes_box)
        row.pack(fill="x", pady=1)
        vs = (tk.StringVar(value=_fmt(x)), tk.StringVar(value=_fmt(y)), tk.StringVar(value=_fmt(d)))
        ttk.Label(row, text=str(len(self.hole_rows) + 1), width=3).pack(side="left", padx=2)
        for v in vs:
            ttk.Entry(row, textvariable=v, width=11).pack(side="left", padx=2)
        rec = (vs[0], vs[1], vs[2], row)
        ttk.Button(row, text="✕", width=3, command=lambda: self.remove_hole_row(rec)).pack(side="left", padx=2)
        self.hole_rows.append(rec)
        self._renumber()

    def remove_hole_row(self, rec) -> None:
        if len(self.hole_rows) <= (1 if self.mode == "calresult" else 2):
            return
        rec[3].destroy()
        self.hole_rows.remove(rec)
        self._renumber()

    def _renumber(self) -> None:
        for i, (_, _, _, row) in enumerate(self.hole_rows, 1):
            row.winfo_children()[0].configure(text=str(i))
        try:
            self.add_hole_btn.state(["disabled"] if len(self.hole_rows) >= 6 else ["!disabled"])
        except (AttributeError, tk.TclError):
            pass

    def _set_holes(self, holes) -> None:
        while len(self.hole_rows) < len(holes):
            self.add_hole_row()
        for (vx, vy, vd, _), h in zip(self.hole_rows, holes):
            vx.set(_fmt(h[0]))
            vy.set(_fmt(h[1]))
            vd.set(_fmt(h[2]) if len(h) > 2 else "")

    def _hole_texts(self):
        return [(vx.get().strip(), vy.get().strip(), vd.get().strip()) for vx, vy, vd, _ in self.hole_rows]

    def read_holes(self) -> Tuple[List[va.DesignHole], List[str]]:
        holes, errs = [], []
        for i, (tx, ty, td) in enumerate(self._hole_texts(), 1):
            if not (tx or ty or td):
                continue
            x, y, d = _num(tx), _num(ty), _num(td)
            if x is None or y is None:
                errs.append(f"hole {i}: X and Y must be numbers")
            elif d is None or d <= 0:
                errs.append(f"hole {i}: enter its diameter")
            else:
                holes.append((x, y, d))
        return holes, errs

    def read_stock(self) -> Tuple[Optional[va.Box], List[str]]:
        vals = [v.get().strip() for v in self.stock_vars]
        if not any(vals):
            return None, []
        nums = [_num(v) for v in vals]
        if any(n is None for n in nums):
            return None, ["stock box: fill in all four numbers, or leave all four empty"]
        if not (nums[2] > nums[0] and nums[3] > nums[1]):
            return None, ["stock box: X max must be larger than X min, and Y max larger than Y min"]
        return (nums[0], nums[1], nums[2], nums[3]), []

    def read_machine(self) -> Tuple[Dict[str, float], List[str]]:
        m, errs = {}, []
        for k, var in self.mvars.items():
            v = _num(str(var.get()))
            if v is None:
                errs.append(f"{va.PROBE_FIELDS[k][4]}: enter a number")
            else:
                m[k] = v
        if not errs:
            errs = va.machine_errors(m)
        return m, errs

    # ------------------------------------------------------------------ text
    def _set_text(self, s: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", s)
        self.text.configure(state="disabled")

    def _set_status(self, s: str, colour: str = "black") -> None:
        self.status.configure(text=s, fg=colour)

    def show_selected_report(self) -> None:
        sel = self.tree.selection()
        if sel and sel[0] in self.results:
            self._set_text(self.results[sel[0]].report)

    # ------------------------------------------------------------------ prepare
    def _gather(self):
        holes, errs = self.read_holes()
        stock, e2 = self.read_stock()
        m, e3 = self.read_machine()
        return holes, stock, m, errs + e2 + e3

    def _show_checks(self, checks) -> None:
        lines = []
        for job, r in checks:
            self.results[job] = r
            self.tree.item(job, values=(os.path.basename(job), "ready" if r.ok else "FAIL", "", "", ""),
                           tags=("pass" if r.ok else "fail",))
            lines.append(("OK    " if r.ok else "FAIL  ") + os.path.basename(job))
            lines += ["      - " + f for f in r.fails]
            lines += ["      (note) " + w for w in r.warns]
        return lines

    def on_check(self) -> bool:
        holes, stock, m, errs = self._gather()
        if not errs:
            errs = va.hole_errors(holes, m["tip"])
        if not self.jobs:
            errs.append("add the side-B job files")
        elif self.loc_var.get() != "holes" and va.slot_box(self.jobs, stock) is None:
            errs.append("slots need the stock box (side-B coordinates): it tells which edge each slot opens to")
        if errs:
            self._set_text("Fix these first:\n\n" + "\n".join("  - " + e for e in errs))
            self._set_status("Not ready", RED)
            return False
        checks = va.precheck_jobs(self.jobs, holes, stock, m["tip"])
        lines = self._show_checks(checks)
        ok = all(r.ok for _, r in checks)
        self._set_text(("Everything checks out - ready to probe.\n\n" if ok else
                        "Some files would FAIL even with perfect probing:\n\n") + "\n".join(lines))
        self._set_status("Ready to probe" if ok else "Not ready", GREEN if ok else RED)
        return ok

    def on_start(self) -> None:
        holes, stock, m, errs = self._gather()
        if errs:
            self._set_text("Fix these first:\n\n" + "\n".join("  - " + e for e in errs))
            self._set_status("Not ready", RED)
            return
        sim = bool(self.sim_var.get())
        prep = va.prepare_probing(self.jobs, holes, stock, m, sim=sim, mode=self.loc_var.get())
        if not prep.ok:
            lines = self._show_checks(prep.checks)
            self._set_text("Not started:\n\n" + "\n".join("  - " + e for e in prep.errors)
                           + ("\n\n" + "\n".join(lines) if lines else ""))
            self._set_status("Not ready", RED)
            return
        self.result_code = 0
        self.root.destroy()          # ShopBot continues with vd_run.sbp

    def on_start_rough(self) -> None:
        holes, stock, m, errs = self._gather()
        key, e2 = self.read_key()
        errs += e2
        bay = self.bay_var.get().strip()
        if not bay:
            errs.append("choose the bay cut file")
        if not self.jobs:
            errs.append("add the side-B job files")
        if errs:
            self._set_text("Fix these first:\n\n" + "\n".join("  - " + e for e in errs))
            self._set_status("Not ready", RED)
            return
        if stock is None:
            stock = va.slot_box(self.jobs, None)
        prep = va.prepare_rough(self.jobs, bay, holes, stock, m, key=key, sim=bool(self.sim_var.get()))
        if not prep.ok:
            self._set_text("Not started:\n\n" + "\n".join("  - " + e for e in prep.errors))
            self._set_status("Not ready", RED)
            return
        self.result_code = 0
        self.root.destroy()          # ShopBot continues with the rough locate in vd_run.sbp

    def on_advanced(self) -> None:
        top = tk.Toplevel(self.root)
        top.title("Virtual Dowel - advanced probe settings")
        top.transient(self.root)
        f = ttk.Frame(top, padding=10)
        f.pack(fill="both", expand=True)
        for i, k in enumerate(("depth", "margin", "backoff", "fast", "slow", "zspd")):
            ttk.Label(f, text=va.PROBE_FIELDS[k][4]).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(f, width=8, textvariable=self.mvars[k]).grid(row=i, column=1, padx=6)
        ttk.Checkbutton(f, text=va.PROBE_FIELDS["c7"][4], variable=self.mvars["c7"]).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(f, text="Distances in inches, speeds in inches per second.").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(f, text="OK", command=top.destroy).grid(row=8, column=1, sticky="e", pady=(8, 0))

    def on_calibrate(self) -> None:
        top = tk.Toplevel(self.root)
        top.title("Virtual Dowel - probe calibration")
        top.transient(self.root)
        f = ttk.Frame(top, padding=10)
        f.pack(fill="both", expand=True)
        intro = ("Probes one round hole of known size many times. It measures how repeatable the probe is, "
                 "the ball diameter as the probe sees it, and how far the ball sits off the spindle axis. "
                 "It can also find which input the probe is wired to.\n\n"
                 "Use a hole with a clean wall you can measure - a ring gauge, or a hole bored in aluminium "
                 "or acrylic, measured with calipers. Job data and aligned files are not touched.")
        ttk.Label(f, text=intro, wraplength=440, justify="left").grid(row=0, column=0, columnspan=2, sticky="w")
        self.cal_ref = tk.StringVar()
        self.cal_passes = tk.StringVar(value="10")
        self.cal_spin = tk.IntVar(value=1)
        self.cal_detect = tk.IntVar(value=1)
        ttk.Label(f, text="Measured hole diameter (inches)").grid(row=1, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(f, width=9, textvariable=self.cal_ref).grid(row=1, column=1, sticky="w", pady=(8, 2))
        ttk.Label(f, text="Passes").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(f, from_=3, to=20, width=5, textvariable=self.cal_passes).grid(row=2, column=1, sticky="w")
        ttk.Checkbutton(f, text="Turn the spindle 180° halfway (measures the ball offset)",
                        variable=self.cal_spin).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(f, text="Find the probe input first (you hold the tip over for 3 s)",
                        variable=self.cal_detect).grid(row=4, column=0, columnspan=2, sticky="w")
        bf = ttk.Frame(f)
        bf.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(bf, text="Cancel", command=top.destroy).pack(side="right")
        ttk.Button(bf, text="Start calibration", command=lambda: self.on_start_calibration(top)).pack(
            side="right", padx=(0, 6))

    def on_start_calibration(self, top=None) -> None:
        m, errs = self.read_machine()
        ref = _num(self.cal_ref.get())
        passes = _num(self.cal_passes.get())
        if passes is None or passes != int(passes) or not 3 <= passes <= 20:
            errs.append("passes: a whole number from 3 to 20")
        if errs:
            self.error("Virtual Dowel", "\n".join(errs))
            return
        prep = va.prepare_calibration(m, ref or 0.0, passes=int(passes), spin180=bool(self.cal_spin.get()),
                                      detect=bool(self.cal_detect.get()), sim=bool(self.sim_var.get()))
        if not prep.ok:
            self.error("Virtual Dowel", "\n".join(prep.errors))
            return
        self.result_code = 0
        self.root.destroy()          # ShopBot continues with the calibration run

    # ------------------------------------------------------------------ align
    def on_align_auto(self) -> None:
        if self.stale:
            self._set_text("This probe data belongs to an earlier probing run. Probe again (type C80 in ShopBot).")
            self._set_status("Not aligned - no usable probe data", RED)
        elif self.pd is not None and self.jobs and all(any(t) for t in self._hole_texts()):
            self.on_align()
        elif self.pd is not None:
            self._set_text("Add the side-B files and check the hole values, then click Align.")

    def on_align(self) -> None:
        if self.pd is None or self.stale:
            self.error("Virtual Dowel", "There is no usable probe data. Probe the holes first (type C80 in ShopBot).")
            self._set_status("Not aligned - no usable probe data", RED)
            return
        holes, errs = self.read_holes()
        stock, e2 = self.read_stock()
        errs += e2
        if not self.jobs:
            errs.append("add the side-B job files")
        if len(holes) != self.pd.nholes:
            errs.append(f"probing measured {self.pd.nholes} holes but the table has {len(holes)}")
        if errs:
            self._set_text("Fix these first:\n\n" + "\n".join("  - " + e for e in errs))
            self._set_status("Not aligned", RED)
            return
        th = va.load_settings()
        allow_old = False
        age = va.data_age_minutes(self.data_path)
        if age > th["max_data_age_min"]:
            allow_old = self.ask_yes_no(
                "Virtual Dowel - old probe data",
                f"The probe data is {age:.0f} minutes old.\n\nUse it only if the board has not been touched "
                "and X/Y zero has not changed since probing. Use it anyway?")
            if not allow_old:
                return
        results = va.align_batch(self.data_path, self.jobs, holes, stock, th=th, allow_old_data=allow_old)
        for job, r in results:
            self.results[job] = r
            rot = f"{r.rotation_deg:+.3f}°" if r.rotation_deg is not None else ""
            sp = f"{r.spacing_err:.4f}" if r.spacing_err is not None else ""
            out = os.path.basename(r.out_path) if r.out_path else "(nothing written)"
            self.tree.item(job, values=(os.path.basename(job), "PASS" if r.ok else "FAIL", rot, sp, out),
                           tags=("pass" if r.ok else "fail",))
        n_ok = sum(1 for _, r in results if r.ok)
        if n_ok == len(results):
            self._set_status(f"PASS - {n_ok} file(s) aligned. Fit each cutter, zero Z only, run its _VD.sbp.", GREEN)
        else:
            self._set_status(f"{len(results) - n_ok} of {len(results)} file(s) FAILED - only PASS files were written.",
                             RED)
        first = next((j for j, r in results if not r.ok), results[0][0] if results else None)
        if first:
            self.tree.selection_set(first)
            self.tree.see(first)
            self.show_selected_report()
        self.result_code = 0 if n_ok == len(results) else 1

    # ------------------------------------------------------------------ calibration result
    def on_save_cal(self) -> None:
        if self.cal is None:
            return
        if not any(va.calibration_usable(self.cal)):
            return
        m = va.apply_calibration(va.load_machine(), self.cal)
        va.save_machine(m, calibration=self.cal.as_record())
        self.save_btn.state(["disabled"])
        self.info("Virtual Dowel", "Saved. The next probing run uses these probe settings.")
        self.result_code = 0

    def on_close(self) -> None:
        if self.mode == "prepare" and self.result_code != 0:
            va.write_run_stub()          # nothing runs when the window is closed without Start
        self.root.destroy()


def run(mode: str, data_path: Optional[str] = None) -> int:
    if mode == "prepare":
        # if the window cannot open or crashes, ShopBot must not run an old probing file
        va.write_run_stub("The Virtual Dowel window closed without starting probing. If it did not open "
                          "at all see vd_launch.log in the VirtualDowel folder.")
    try:
        app = App(mode, data_path)
    except Exception:
        va._log("WINDOW CRASH\n" + traceback.format_exc())
        raise
    app.root.mainloop()
    return app.result_code
