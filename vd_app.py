"""
Virtual Dowel - vd_app.py  v2.4  (2026-10-12)
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
import sys
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


METHODS = {
    "holes": ("Through holes", "Two holes of different sizes, cut through the board by side A on the flip axis. "
              "The most accurate. Needs the Z travel to lift a cutter as long as the board is thick above the board."),
    "slots": ("Half slots", "For thick boards: two slots in the board's edges, cut half depth by side A. On side B a "
              "short bay cut opens them, then the probe comes in from the side. Sizes: click Slot and bay sizes."),
    "through": ("Through slots", "Two slots in the board's edges, cut through from outside with a bit as long as the "
                "board is thick. Use when that bit cannot be lifted above the board."),
    "centre": ("Centre and line", "No flip. Any part - a 3D print, a casting, a found object - is placed by two "
               "references: bores in its top face or notches in its edge, on a line through the job's X0 Y0, halfway "
               "between them. Their spacing is reported, not checked, so a part that shrank or grew still goes to "
               "the midpoint, turned to the line."),
}
STEPS = {"holes": ("Prepare", "Probe", "Align", "Cut side B"), "through": ("Prepare", "Probe", "Align", "Cut side B"),
         "slots": ("Prepare", "Rough locate", "Cut bays", "Probe", "Align", "Cut side B"),
         "centre": ("Prepare", "Probe", "Align", "Cut")}
REFS = {"holes": "Bores in the top face", "through": "Notches in the edge"}     # centre and line: the references

NEXT = {
    "holes": """Through holes - what to do now
  Before this: side A has cut two holes of different sizes through the board, on the flip axis.
  1. Add files: the side-B job files from Fusion or VCarve, one per tool.
  2. Enter each hole: X and Y of its centre and its diameter, in side-B (Setup 2) coordinates.
  3. Stock box: only if a file does not state its stock size (the note says).
  4. Click Check and fix anything marked FAIL.
  5. Fit the PROBE, plug in its cable, and click Start probing.
     ShopBot asks you to jog the probe over each hole; then this window comes back with the results.""",
    "slots": """Half slots - what to do now
  Before this: side A has cut two half slots in the board's edges, on the flip axis, of different widths.
  1. Add files: the side-B job files, and choose the bay cut file (made in Fusion).
  2. Enter each slot: X and Y of its round-end centre and its width, in side-B coordinates.
  3. Stock box: needed unless a file states its stock size (the note says).
  4. Click Check and fix anything marked FAIL.
  5. Fit the BAY CUTTER and click Start rough locate.
     ShopBot asks you to jog the bit tip over each slot opening on the board's edge.
  After that: cut the bays, fit the probe, type C80 again and click Start probing.""",
    "slots2": """Half slots - the bay file is placed: {bay}
  1. If not done yet: in ShopBot, FP that file. Look at it in Preview, zero Z with the bay cutter
     (do NOT zero X or Y) and run it.
  2. Vacuum the slots. Fit the PROBE and plug in its cable.
  3. Click Start probing. At each slot, jog the probe ball outside the opening - anywhere up to 1 in
     (25 mm) out - in line with the slot, at the park height from Slot and bay sizes.
  To place the bay file again instead, click Start rough locate.""",
    "through": """Through slots - what to do now
  Before this: side A has cut two slots through the board in its edges, on the flip axis, of different widths.
  1. Add files: the side-B job files, one per tool.
  2. Enter each slot: X and Y of its round-end centre and its width, in side-B coordinates.
  3. Stock box: needed unless a file states its stock size (the note says).
  4. Click Check and fix anything marked FAIL.
  5. Fit the PROBE and click Start probing. At each slot, jog the probe ball outside the opening -
     anywhere up to 1 in (25 mm) out - in line with the slot.""",
    "centre": """Centre and line - what to do now
  Before this: the part has two references of different sizes - bores in its top face or notches in its edge -
  on one line through the job's X0 Y0, with X0 Y0 halfway between them. No flip: the job is in the part's
  own coordinates. Clamp the part square to the table, within 5 degrees.
  1. Add files: the job files for the part, one per tool.
  2. References: choose bores or notches. Enter each: X and Y of its centre (notch: round-end centre) and
     its diameter (notch: width), in job coordinates. Exactly two.
  3. Stock box: the part's outline in job coordinates. Notches need it (which edge each opens to).
  4. Click Check and fix anything marked FAIL.
  5. Fit the PROBE and click Start probing. Bores: jog the probe over each bore. Notches: jog the ball
     outside the opening - up to 1 in (25 mm) out - in line with the notch.
  The distance between the references is reported, not checked - a 3D print shrinks, a casting too. Only
  the midpoint and the line through them are placed.""",
}


class App:
    """mode: 'prepare', 'align', 'calresult' or 'rough'. result_code is what the process exits with
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
        self.root.title({"prepare": "Virtual Dowel - prepare", "align": "Virtual Dowel - results",
                         "calresult": "Virtual Dowel - probe calibration",
                         "rough": "Virtual Dowel - rough locate"}.get(mode, "Virtual Dowel"))
        self.method = "holes"            # how the board is located: holes / slots / through
        self.stage2 = False              # half slots: the bay file is placed, probing comes next
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
        self.steps_bar = ttk.Frame(outer)
        self.steps_bar.pack(fill="x", pady=(0, 8))
        self.loc_var = tk.StringVar(value="holes")
        self.ref_var = tk.StringVar(value="holes")       # centre and line: bores (holes) or notches (through)
        if self.mode == "prepare":
            lf = self.method_row = ttk.Frame(outer)
            lf.pack(fill="x", pady=(0, 2))
            ttk.Label(lf, text="Locate the part with", font=("Segoe UI", 10, "bold")).pack(side="left")
            for val in METHODS:
                ttk.Radiobutton(lf, text=METHODS[val][0], value=val, variable=self.loc_var,
                                command=self._on_locate_mode).pack(side="left", padx=(10, 0))
            ttk.Button(lf, text="Thick-stock guide", command=self.on_guide).pack(side="right")
            ttk.Button(lf, text="Slot and bay sizes...", command=self.on_sizes).pack(side="right", padx=(0, 6))
            self.method_note = ttk.Label(outer, text="", wraplength=900, justify="left")
            self.method_note.pack(fill="x", pady=(0, 6))
            self.ref_frame = ttk.Frame(outer)
            ttk.Label(self.ref_frame, text="References", font=("Segoe UI", 10, "bold")).pack(side="left")
            for val, txt in REFS.items():
                ttk.Radiobutton(self.ref_frame, text=txt, value=val, variable=self.ref_var,
                                command=self._on_locate_mode).pack(side="left", padx=(10, 0))

        # side-B files
        jf = ttk.LabelFrame(outer, text="Side-B job files - the files for the flipped side, one per tool", padding=6)
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
            lbl = ttk.Label(hdr, text=t, width=w)
            lbl.pack(side="left", padx=2)
        self.size_header = lbl
        self.holes_box = ttk.Frame(hf)
        self.holes_box.pack(fill="x")
        self.add_hole_btn = ttk.Button(hf, text="+ Add hole", command=lambda: self.add_hole_row())
        self.add_hole_btn.pack(anchor="w", pady=(4, 0))

        # stock box
        sf = ttk.LabelFrame(mid, text="Stock box - side-B coordinates", padding=6)
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
        mf = ttk.LabelFrame(outer, text="What to do - checks - reports", padding=4)
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
        if self.mode == "prepare":
            ttk.Button(bf, text="Close - run nothing", command=self.on_close).pack(side="right")
            self.start_btn = ttk.Button(bf, text="Start probing (probe in)", command=self.on_start)
            self.rough_btn = ttk.Button(bf, text="Start rough locate (bay cutter in)", command=self.on_start_rough)
            self.check_btn = ttk.Button(bf, text="Check", command=self.on_check)
        else:
            ttk.Button(bf, text="Close", command=self.on_close).pack(side="right")
            ttk.Button(bf, text="Open the folder", command=self.on_open_folder).pack(side="right", padx=(0, 6))
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
        ttk.Label(g, text="Probe input").grid(row=0, column=0, sticky="w")
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
        ttk.Checkbutton(g2, text="Finger test before moving", variable=self.mvars["test"]).pack(side="left", padx=12)
        self.sim_var = tk.IntVar(value=0)
        ttk.Checkbutton(g2, text="Dry run - no motion, you type the positions (rehearse in Preview)",
                        variable=self.sim_var).pack(side="left")
        g3 = ttk.Frame(pf)
        g3.pack(fill="x", pady=(4, 0))
        ttk.Button(g3, text="Advanced...", command=self.on_advanced).pack(side="left")
        ttk.Button(g3, text="Calibrate probe...", command=self.on_calibrate).pack(side="left", padx=6)
        cal = va.read_machine_file().get("calibration")
        txt = "No probe calibration yet - Calibrate probe finds the input and measures the ball."
        if isinstance(cal, dict):
            ecc = cal.get("ecc")
            txt = (f"Last calibration {cal.get('date', '?')}: repeatability {cal.get('repeat', 0):.4f}, "
                   f"effective ball {cal.get('eff_tip', 0):.4f}"
                   + (f", ball off spindle {math.hypot(*ecc):.4f}" if ecc else ""))
        self.cal_label = ttk.Label(g3, text=txt)
        self.cal_label.pack(side="left", padx=8)

    def _on_locate_mode(self) -> None:
        self._set_method(self.loc_var.get())
        if self.mode != "prepare":
            return
        self.method_note.configure(text=METHODS[self.method][1])
        for b in (self.start_btn, self.rough_btn, self.check_btn):
            b.pack_forget()
        # right to left: Start probing, then (half slots) the rough locate, then Check - so they read in work order
        self.start_btn.pack(side="right", padx=(0, 6))
        if self.method == "slots":
            self.bay_frame.pack(fill="x", pady=(8, 0), after=self.mid)
            self.rough_btn.pack(side="right", padx=(0, 6))
        else:
            self.bay_frame.pack_forget()
        if self.method == "centre":
            self.ref_frame.pack(fill="x", pady=(0, 6), after=self.method_note)
        else:
            self.ref_frame.pack_forget()
        self.check_btn.pack(side="right", padx=(0, 6))
        self.show_next_steps()

    def probe_mode(self) -> str:
        """What the routine probes: holes, slots (half slots) or through (slots). Centre and line: bores or notches."""
        return self.ref_var.get() if self.method == "centre" else self.method

    def _set_method(self, method: str) -> None:
        """Labels that depend on holes or slots, and the step bar."""
        self.method = method if method in METHODS else "holes"
        slots = self.probe_mode() != "holes"
        if self.method == "centre":
            title = ("References - X and Y of the centre (notch: round-end centre) and the diameter (notch: width) "
                     "- job coordinates")
        elif slots:
            title = "Slots - X and Y of the round-end centre and the width - side-B coordinates"
        else:
            title = "Reference holes - X and Y of the centre and the diameter - side-B coordinates"
        self.holes_frame.configure(text=title)
        self.size_header.configure(text="Width" if slots else "Diameter")
        self.add_hole_btn.configure(text="+ Add slot" if slots else "+ Add hole")
        self._refresh_stock_note()
        self._draw_steps()

    def _draw_steps(self) -> None:
        for w in self.steps_bar.winfo_children():
            w.destroy()
        steps = STEPS[self.method]
        if self.mode == "align":
            cur = steps.index("Align")
        elif self.method == "slots" and self.stage2:
            cur = steps.index("Probe")
        else:
            cur = 0
        for i, txt in enumerate(steps):
            tk.Label(self.steps_bar, text=f"{i + 1}  {txt}", padx=8, pady=3,
                     font=("Segoe UI", 10, "bold" if i == cur else "normal"), fg="white" if i == cur else "#555",
                     bg="#2f5d8a" if i == cur else "#e4e4e4").pack(side="left", padx=(0, 4))

    def show_next_steps(self) -> None:
        """The report box shows what to do next until a check or a result replaces it."""
        if self.method == "slots" and self.stage2:
            bay = self.bay_var.get().strip()
            out = va.default_rough_path(bay, bool(self.sim_var.get())) if bay else "(the bay file)_VDROUGH.sbp"
            self._set_text(NEXT["slots2"].format(bay=out))
        else:
            self._set_text(NEXT[self.method])
        self._set_status("", "black")

    def on_open_folder(self) -> None:
        folder = os.path.dirname(self.jobs[0]) if self.jobs else va.HERE
        try:
            if os.name == "nt":
                os.startfile(folder)                         # noqa: S606 - Windows Explorer
            else:
                import subprocess
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
        except OSError as e:
            self.error("Virtual Dowel", f"Could not open {folder}: {e}")

    def on_pick_bay(self) -> None:
        got = filedialog.askopenfilename(parent=self.root, title="Bay cut file (.sbp) from Fusion",
                                         filetypes=[("ShopBot part files", "*.sbp"), ("All files", "*.*")])
        if got:
            self.bay_var.set(got)

    def on_guide(self) -> None:
        path = va.guide_path()
        if path is None:
            self.error("Virtual Dowel", "The guide is missing: docs\\guide.html next to INSTALL.bat")
            return
        webbrowser.open("file:///" + path.replace(os.sep, "/").lstrip("/"))

    # ------------------------------------------------------------------ size calculator (half slots)
    SIZE_FIELDS = (("Probe", None), ("ball", "Ball diameter"), ("stem", "Stem diameter"),
                   ("free", "Ball centre to body"), ("body", "Body diameter"), ("length", "Ball centre to collet nut"),
                   ("Stock and slots", None), ("thick", "Stock thickness"), ("slot_depth", "Side-A slot depth"),
                   ("widths", "Slot widths"), ("slot_length", "Edge to round-end centre"),
                   ("Planned bay (optional)", None), ("bay_width", "Bay width"), ("bay_reach", "Bay reach past centre"))

    def on_sizes(self) -> None:
        """A live view of the half-slot size rules (vd_align.slot_bay_sizes): what the Fusion slots and bays
        need for this probe and stock. Nothing is read from the job files."""
        top = tk.Toplevel(self.root)
        top.title("Virtual Dowel - slot and bay sizes")
        top.transient(self.root)
        self.sizes_top = top
        saved = va.load_sizes_inputs()
        widths = [t[2] for t in self._hole_texts() if t[2]]
        start = dict(saved, ball=_num(str(self.mvars["tip"].get())) or 0.125,
                     widths=", ".join(widths) if len(widths) >= 2 else "0.625, 0.75", bay_width="", bay_reach="")
        self.size_vars: Dict[str, tk.StringVar] = {}
        left = ttk.Frame(top, padding=10)
        left.grid(row=0, column=0, sticky="ns")
        r = 0
        for key, label in self.SIZE_FIELDS:
            if label is None:
                ttk.Label(left, text=key, font=("Segoe UI", 10, "bold")).grid(row=r, column=0, columnspan=2,
                                                                             sticky="w", pady=(8 if r else 0, 2))
            else:
                ttk.Label(left, text=label).grid(row=r, column=0, sticky="w")
                v = tk.StringVar(value=_fmt(start[key]) if isinstance(start[key], float) else str(start[key]))
                ttk.Entry(left, textvariable=v, width=12).grid(row=r, column=1, padx=(8, 0), pady=1)
                v.trace_add("write", lambda *a: self._sizes_later())
                self.size_vars[key] = v
            r += 1
        self.size_mm = tk.IntVar(value=0)
        ttk.Checkbutton(left, text="All values in mm", variable=self.size_mm,
                        command=self._sizes_later).grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))
        right = ttk.Frame(top, padding=(0, 10, 10, 10))
        right.grid(row=0, column=1, sticky="nsew")
        self.size_status = tk.Label(right, text="", anchor="w", font=("Segoe UI", 12, "bold"), fg="white", padx=8, pady=4)
        self.size_status.pack(fill="x")
        self.size_canvas = tk.Canvas(right, width=760, height=400, bg="white", highlightthickness=0)
        self.size_canvas.pack(fill="both", expand=True, pady=(6, 0))
        self.size_text = tk.Text(right, height=11, width=96, wrap="word", font=MONO)
        self.size_text.pack(fill="x", pady=(6, 0))
        bf = ttk.Frame(right)
        bf.pack(fill="x", pady=(6, 0))
        ttk.Button(bf, text="Close", command=self._sizes_close).pack(side="right")
        top.protocol("WM_DELETE_WINDOW", self._sizes_close)
        self._sizes_job = None
        self.update_sizes()

    def _sizes_later(self) -> None:
        if getattr(self, "_sizes_job", None):
            self.root.after_cancel(self._sizes_job)
        self._sizes_job = self.root.after(150, self.update_sizes)

    def _sizes_close(self) -> None:
        vals = {k: _num(v.get()) for k, v in self.size_vars.items() if k in va.SIZES_DEFAULTS}
        if all(x is not None and x > 0 for x in vals.values()) and not self.size_mm.get():
            va.save_sizes_inputs(vals)                       # remembered in inches for next time
        self.sizes_top.destroy()

    def read_sizes(self):
        """(values, errors) from the calculator fields."""
        vals, errs = {}, []
        for key, label in self.SIZE_FIELDS:
            if label is None:
                continue
            txt = self.size_vars[key].get().strip()
            if key == "widths":
                try:
                    w = [float(x) for x in txt.replace(";", ",").split(",") if x.strip()]
                except ValueError:
                    w = []
                if len(w) < 2 or any(x <= 0 for x in w):
                    errs.append("slot widths: two or more numbers with commas between them")
                vals[key] = w
            elif key in ("bay_width", "bay_reach") and not txt:
                vals[key] = None
            else:
                x = _num(txt)
                if x is None or x <= 0:
                    errs.append(f"{label.lower()}: enter a number larger than 0")
                vals[key] = x
        return vals, errs

    def update_sizes(self) -> None:
        self._sizes_job = None
        vals, errs = self.read_sizes()
        c = self.size_canvas
        c.delete("all")
        if errs:
            self.size_status.configure(text="Enter the numbers", bg=AMBER)
            self._set_size_text("\n".join(errs))
            return
        res = va.slot_bay_sizes(mm=bool(self.size_mm.get()), **vals)
        self.size_result = res
        self.size_status.configure(text="OK - these sizes work with this probe" if res.ok else
                                   "NOT OK - see the red parts and the problems below", bg=GREEN if res.ok else RED)
        self._set_size_text(res.text())
        self.draw_sizes(c, vals, res)

    def _set_size_text(self, s: str) -> None:
        self.size_text.configure(state="normal")
        self.size_text.delete("1.0", "end")
        self.size_text.insert("1.0", s)
        self.size_text.configure(state="disabled")

    def draw_sizes(self, c: tk.Canvas, v: dict, r) -> None:
        """Left: side B cut on the slot axis. Right: top view of one slot and its bay. Real proportions."""
        k = 25.4 if r.unit == "mm" else 1.0
        ink, wood, slot_c, bay_c, probe_c, bad = "#1b2533", "#ead9b2", "#bfa8ee", "#e6dcf8", "#0e7490", RED
        ball, stem, free, body, length = v["ball"], v["stem"], v["free"], v["body"], v["length"]
        thick, depth, L = v["thick"], v["slot_depth"], v["slot_length"]
        w = max(v["widths"])
        rho = (w - ball) / 2
        clear = body if r.body_in_bay else stem
        reach = v["bay_reach"] if v["bay_reach"] else r.min_bay_reach
        bay_w = v["bay_width"] if v["bay_width"] else r.min_bay_width
        reach_bad = v["bay_reach"] is not None and v["bay_reach"] < r.min_bay_reach - 1e-9
        width_bad = v["bay_width"] is not None and v["bay_width"] < r.min_bay_width - 1e-9
        park_ok = r.park_lo <= r.park_hi
        h = (r.park_lo + r.park_hi) / 2 if park_ok else max(r.park_hi, ball)
        park_x = -0.3 * k

        # ---- section on the slot axis (x along the slot from the edge, z up from the spoilboard)
        x0w, x1w = park_x - clear - 0.4 * k, L + reach + 0.6 * k
        z0w, z1w = -0.65 * k, max(thick, h + length + 0.2 * k) + 0.2 * k
        W, H, ox, oy = 430, 360, 10, 30
        lab = ("Segoe UI", 9)
        s = min(W / (x1w - x0w), H / (z1w - z0w))
        X = lambda x: ox + (x - x0w) * s          # noqa: E731
        Y = lambda z: oy + (z1w - z) * s          # noqa: E731
        c.create_rectangle(X(x0w), Y(0), X(x1w), Y(-0.25 * k), fill="#d5dbe2", outline="#8c97a4")
        c.create_rectangle(X(0), Y(thick), X(x1w), Y(0), fill=wood, outline=ink)
        c.create_rectangle(X(0), Y(thick), X(L + reach), Y(r.bay_floor), fill=bay_c,
                           outline=bad if reach_bad else "#6d3fc0", width=2 if reach_bad else 1)
        if reach_bad:
            c.create_line(X(L + r.min_bay_reach), Y(thick), X(L + r.min_bay_reach), Y(r.bay_floor),
                          fill=bad, dash=(4, 3))
        c.create_rectangle(X(0), Y(depth), X(L + w / 2), Y(0), fill=slot_c, outline="#6d3fc0")
        c.create_line(X(L), Y(-0.3 * k), X(L), Y(thick + 0.1 * k), fill="#4e5a69", dash=(8, 3, 2, 3))
        if park_ok:
            c.create_rectangle(X(park_x - 0.12 * k), Y(r.park_hi), X(park_x + 0.12 * k), Y(r.park_lo),
                               outline=probe_c, dash=(3, 2))
        body_bad = not park_ok
        for x, dash in ((park_x, None), (L + rho, (5, 3))):
            col = bad if body_bad else probe_c
            c.create_oval(X(x - ball / 2), Y(h + ball / 2), X(x + ball / 2), Y(h - ball / 2), outline=col, dash=dash,
                          fill="" if dash else col)
            c.create_rectangle(X(x - stem / 2), Y(h + free), X(x + stem / 2), Y(h + ball / 2), outline=col, dash=dash)
            c.create_rectangle(X(x - body / 2), Y(h + length), X(x + body / 2), Y(h + free), outline=col, dash=dash)
            c.create_rectangle(X(x - body * 0.8), Y(h + length + 0.15 * k), X(x + body * 0.8), Y(h + length),
                               outline=col, dash=dash)
        f = lambda val: f"{val:.3f}"              # noqa: E731
        c.create_text(X(L + reach) + 4, Y(r.bay_floor), text=f"bay floor {f(r.bay_floor)}", anchor="nw", fill=ink,
                      font=lab)
        c.create_text(X(x1w) - 4, Y(thick) - 2, text=f"bay depth {f(r.bay_depth)}", anchor="se", fill=ink, font=lab)
        c.create_text(X(x0w) + 2, Y(-0.3 * k), text=(f"park the ball {f(r.park_lo)} to {f(r.park_hi)} above the "
                                                       "spoilboard" if park_ok else "no park height works"),
                      anchor="nw", fill=probe_c if park_ok else bad, font=lab)
        c.create_text(ox, 6, text="Side B, cut on the slot axis", anchor="nw", fill=ink, font=("Segoe UI", 10, "bold"))

        # ---- top view (x along the slot, y across it)
        ox2, W2 = 460, 290
        half = bay_w / 2
        x0t, x1t = -0.5 * k, L + reach + 0.4 * k
        y0t, y1t = -(max(half, r.min_bay_width / 2) + 0.3 * k), max(half, r.min_bay_width / 2) + 0.3 * k
        H2 = H - 40
        s2 = min(W2 / (x1t - x0t), H2 / (y1t - y0t))
        X2 = lambda x: ox2 + (x - x0t) * s2       # noqa: E731
        Y2 = lambda y: oy + (y1t - y) * s2        # noqa: E731
        c.create_rectangle(X2(0), Y2(y1t), X2(x1t), Y2(y0t), fill=wood, outline="")
        c.create_line(X2(0), Y2(y1t), X2(0), Y2(y0t), fill=ink)
        c.create_rectangle(X2(0), Y2(half), X2(L + reach), Y2(-half), fill=bay_c,
                           outline=bad if (width_bad or reach_bad) else "#6d3fc0", width=2 if (width_bad or reach_bad) else 1)
        if width_bad or reach_bad:
            mh = r.min_bay_width / 2
            c.create_rectangle(X2(0), Y2(mh), X2(L + r.min_bay_reach), Y2(-mh), outline=bad, dash=(4, 3))
        c.create_rectangle(X2(0), Y2(w / 2), X2(L), Y2(-w / 2), fill=slot_c, outline="")
        c.create_oval(X2(L - w / 2), Y2(w / 2), X2(L + w / 2), Y2(-w / 2), fill=slot_c, outline="")
        back = rho + 0.08 * k
        env = clear / 2 + 0.4 * k                 # the probe body plus the room the rough fit needs
        for (bx, by) in ((L + rho, 0.0), (L - back, rho), (L - back, -rho)):
            outside = (abs(by) + env > half + 1e-6) or (bx + env > L + reach + 1e-6)
            c.create_oval(X2(bx - env), Y2(by + env), X2(bx + env), Y2(by - env),
                          outline=bad if outside else "#9fb8c2", dash=(1, 3))
            c.create_oval(X2(bx - clear / 2), Y2(by + clear / 2), X2(bx + clear / 2), Y2(by - clear / 2),
                          outline=probe_c, dash=(4, 3))
            c.create_oval(X2(bx - ball / 2), Y2(by + ball / 2), X2(bx + ball / 2), Y2(by - ball / 2),
                          fill=probe_c, outline="")
        c.create_text(ox2, 6, text="Top view: slot, bay, probe " + ("body" if r.body_in_bay else "stem"),
                      anchor="nw", fill=ink, font=("Segoe UI", 10, "bold"))
        c.create_text(ox2, oy + H2 + 6, text=f"bay at least {f(r.min_bay_width)} wide and {f(r.min_bay_reach)}\n"
                                              "past the round-end centre\ndotted: probe " +
                                              ("body" if r.body_in_bay else "stem") + " + 0.4 for the rough fit",
                      anchor="nw", fill=ink, font=lab)

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
        probe_mode = sess.get("mode") or ("slots" if self.pd is not None and self.pd.open else "holes")
        self.loc_var.set("centre" if sess.get("fit") == "centre" else probe_mode)
        self.ref_var.set(probe_mode if probe_mode in REFS else "holes")
        self.bay_var.set(sess.get("bay") or "")
        key = sess.get("key")
        if key:
            for v, k in zip(self.key_vars, key):
                v.set(_fmt(k))
        # half slots: after a rough locate, the next step is probing
        self.stage2 = sess.get("kind") == "rough" and os.path.exists(va.ROUGH_DATA)
        self._on_locate_mode()

    def _show_probe_info(self, sess: dict) -> None:
        pd = self.pd
        holes, _, _ = va.compute_holes(pd)
        when = datetime.datetime.fromtimestamp(os.path.getmtime(self.data_path)).strftime("%Y-%m-%d %H:%M")
        what = "slots" if pd.open else "holes"
        lines = [f"Probed {when}   {pd.nholes} {what}   {pd.passes} fine passes"
                 + ("   spindle 0/180" if pd.spin180 else "") + ("   SIMULATED" if pd.sim else "")]
        for h in holes:
            if h.open:
                lines.append(f"  slot {h.index}: round-end centre X {h.center[0]:.4f}  Y {h.center[1]:.4f}   "
                             f"width {h.dia:.4f}   repeat {h.repeat:.4f}")
            else:
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
        elif need and self.probe_mode() != "holes":
            txt = "Needed: the file has no stock size, and slots need the box to know which edge each opens to."
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
            what = "slot" if self.probe_mode() != "holes" else "hole"
            if x is None or y is None:
                errs.append(f"{what} {i}: X and Y must be numbers")
            elif d is None or d <= 0:
                errs.append(f"{what} {i}: enter its {'width' if what == 'slot' else 'diameter'}")
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
        elif self.probe_mode() != "holes" and va.slot_box(self.jobs, stock) is None:
            errs.append("slots need the stock box (side-B coordinates): it tells which edge each slot opens to")
        if errs:
            self._set_text("Fix these first:\n\n" + "\n".join("  - " + e for e in errs))
            self._set_status("Not ready", RED)
            return False
        checks = va.precheck_jobs(self.jobs, holes, stock, m["tip"], centre=self.method == "centre")
        lines = self._show_checks(checks)
        ok = all(r.ok for _, r in checks)
        nxt = ("Fit the bay cutter and click Start rough locate." if self.method == "slots" and not self.stage2
               else "Fit the probe and click Start probing.")
        self._set_text((f"Everything checks out. Next: {nxt}\n\n" if ok else
                        "Some files would FAIL even with perfect probing:\n\n") + "\n".join(lines))
        self._set_status(f"Ready - {nxt}" if ok else "Not ready", GREEN if ok else RED)
        return ok

    def on_start(self) -> None:
        holes, stock, m, errs = self._gather()
        if errs:
            self._set_text("Fix these first:\n\n" + "\n".join("  - " + e for e in errs))
            self._set_status("Not ready", RED)
            return
        sim = bool(self.sim_var.get())
        prep = va.prepare_probing(self.jobs, holes, stock, m, sim=sim, mode=self.probe_mode(),
                                  centre=self.method == "centre")
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
            self._set_text("Add the side-B files and check the hole or slot values, then click Align.")

    def on_align(self) -> None:
        if self.pd is None or self.stale:
            self.error("Virtual Dowel", "There is no usable probe data. Probe first (type C80 in ShopBot).")
            self._set_status("Not aligned - no usable probe data", RED)
            return
        holes, errs = self.read_holes()
        stock, e2 = self.read_stock()
        errs += e2
        if not self.jobs:
            errs.append("add the side-B job files")
        if len(holes) != self.pd.nholes:
            errs.append(f"probing measured {self.pd.nholes} but the table has {len(holes)} - add or remove rows")
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
        results = va.align_batch(self.data_path, self.jobs, holes, stock, th=th, allow_old_data=allow_old,
                                 centre=self.method == "centre")
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


if __name__ == "__main__":
    # double-clicked: the same as "vd_align.py prepare", logged in vd_launch.log
    sys.exit(va.main(sys.argv[1:] or ["prepare"]))
