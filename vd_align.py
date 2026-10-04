#!/usr/bin/env python3
"""
Virtual Dowel - vd_align.py  v2.0.1  (2026-10-04)
Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

Reads the raw touch data written by vd_probe.sbp, computes the centers of the
reference holes, fits the rotation + translation that maps the side-B job's
design coordinates onto the probed machine coordinates, and writes a rotated /
shifted copy of the side-B OpenSBP (.sbp) job file ("<name>_VD.sbp").

Standard library only (Python 3.8+). tkinter is used for the file picker and
dialogs when it is available; everything also works from the command line:

    python vd_align.py --data vd_probe_data.csv --job sideB.sbp --holes "12,1,0.5;12,29,0.625"
    (add --stock "24,30" for posts such as Fusion's that do not write the stock size)

Exit codes: 0 = PASS (file written), 1 = FAIL (checks failed / unsupported file),
            2 = usage or I/O problem, 3 = cancelled by the user.
"""
from __future__ import annotations

import argparse
import datetime
import itertools
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

VERSION = "2.0.1"
__author__ = "Nikolai Kozak"
__copyright__ = "Copyright (c) 2026 Nikolai Kozak. All rights reserved."
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.join(HERE, "vd_probe_data.csv")
STATE_FILE = os.path.join(HERE, "vd_state.json")
LOG_FILE = os.path.join(HERE, "vd_launch.log")        # vd_align.bat starts it; Python appends
SETTINGS_FILE = os.path.join(HERE, "vd_settings.json")
MACHINE_FILE = os.path.join(HERE, "vd_machine.json")    # probe settings chosen in the window
TEMPLATE_FILE = os.path.join(HERE, "vd_probe.sbp")      # the probing routine, used as a template
RUN_FILE = os.path.join(HERE, "vd_run.sbp")             # written by the window, run by ShopBot
CAL_DATA = os.path.join(HERE, "vd_cal_data.csv")        # calibration touches (never the job data)

# Distances in INCHES (scaled by 25.4 when the probe data is in mm).
# Override any of them with a vd_settings.json file next to this script.
DEFAULTS: Dict[str, float] = {
    "max_rotation_deg": 5.0,        # FAIL above this: wrong holes / wrong side / wrong flip
    "spacing_warn": 0.005,          # hole-to-hole distance: measured vs design
    "spacing_fail": 0.020,
    "residual_warn": 0.005,         # per-hole fit residual (only informative with 3+ holes)
    "residual_fail": 0.020,
    "diameter_abs_tol": 0.060,      # FAIL if a hole measures this far from its design diameter
    "diameter_rel_tol": 0.015,      # FAIL if hole sizes disagree with each other by more than this
    "roundness_warn": 0.005,        # |Dx - Dy| per hole
    "roundness_fail": 0.015,
    "repeat_warn": 0.002,           # spread of the fine-pass centers of one hole
    "repeat_fail": 0.006,
    "eccentricity_warn": 0.010,     # probe tip off the spindle axis (spin-180 runs only)
    "ecc_consistency_warn": 0.002,  # eccentricity measured at different holes should agree
    "offset_consistency": 0.0015,   # (working - base) must not change during probing; SB3 rounds %(1) to 0.001
    "zero_check_tol": 0.002,        # window of the runtime "X/Y zero unchanged" check
    "symmetry_tol": 0.040,          # layouts symmetric within this cannot detect a wrong flip
    "max_data_age_min": 120,        # refuse probe data older than this (minutes)
    "allow_symmetric_layout": 0,    # 1 = only WARN (not FAIL) for layouts blind to a wrong flip
    "allow_offaxis_ambiguity": 0,   # 1 = only WARN when side-A and side-B hole coordinates could be confused
    "mirror_shift_warn_only": 0.100,  # a side-A/side-B mix-up that would shift the job less than this only WARNs
    "check_file_offset": 1,         # 1 = aligned file refuses to run with an FP offset/scale set
    "slot_wall_margin": 0.02,       # half slots: the wall touches must be this far behind the round end
    "rough_spacing_fail": 0.5,      # rough fit: jogged slot openings vs design openings
    "bay_reach": 3.0,               # rough fit: every cut of the bay file must be this close to a slot
}
DIST_KEYS = {"spacing_warn", "spacing_fail", "residual_warn", "residual_fail", "diameter_abs_tol",
             "diameter_rel_tol", "roundness_warn", "roundness_fail", "repeat_warn", "repeat_fail",
             "eccentricity_warn", "ecc_consistency_warn", "offset_consistency", "zero_check_tol",
             "symmetry_tol", "mirror_shift_warn_only", "slot_wall_margin", "rough_spacing_fail", "bay_reach"}

DesignHole = Tuple[float, float, Optional[float]]     # x, y, diameter (None = not given)


# --------------------------------------------------------------------------------------
# errors / small helpers
# --------------------------------------------------------------------------------------
class VDError(Exception):
    """Any problem that must stop the alignment (message is shown to the operator)."""


class ProbeDataError(VDError):
    pass


class TransformError(VDError):
    def __init__(self, line_no: int, line: str, msg: str):
        super().__init__(f"line {line_no}: {msg}\n    >> {line.strip()}")
        self.line_no = line_no


def split_lines(text: str) -> List[str]:
    """Split on CRLF/LF only. (str.splitlines() also splits on \\x85, \\x0b, \\x0c ... which can
    turn part of a comment into an executable line.)"""
    parts = re.split(r"\r?\n", text)
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def msg_safe(s: str) -> str:
    """Text that is safe inside an OpenSBP MSGBOX body (no commas, quotes, parentheses, colons)."""
    return re.sub(r"[,'\"():\r\n]", " ", s)


def _mean(vals: Sequence[float]) -> float:
    return sum(vals) / len(vals)


# --------------------------------------------------------------------------------------
# probe data
# --------------------------------------------------------------------------------------
@dataclass
class Touch:
    hole: int
    orient: int
    pas: int
    axis: int      # 1 = X, 2 = Y
    sign: int      # +1 / -1
    x: float
    y: float
    z: float
    bx: float      # table-base coordinates at the same moment
    by: float
    line: int


@dataclass
class ProbeData:
    units: int                 # 0 = inch, 1 = mm (SB3 %(25))
    sim: bool
    tip: float
    nominal: float             # routine's vd_hole (largest hole)
    nholes: int
    passes: int
    spin180: bool
    pin: int
    trig: int
    mode: int                  # SB3 %(22) while probing (MOVE mode for a real run)
    date: str
    cfg: List[float]
    touches: List[Touch]
    path: str = ""
    cal_ref: Optional[float] = None            # calibration run: measured diameter of the reference hole
    detected: Optional[Tuple[int, int]] = None  # input found automatically: (pin, deflected state)
    open: Dict[int, Tuple[int, int]] = field(default_factory=dict)   # half slots: hole -> (axis, sign) it opens to

    @property
    def unit_name(self) -> str:
        return "mm" if self.units == 1 else "in"

    @property
    def scale(self) -> float:
        return 25.4 if self.units == 1 else 1.0


def _f(tok: str, line_no: int) -> float:
    t = tok.strip()
    if t == "":
        raise ProbeDataError(f"probe data line {line_no}: empty number")
    try:
        v = float(t)
    except ValueError:
        raise ProbeDataError(
            f"probe data line {line_no}: cannot read number {tok!r} "
            "(if Windows uses a decimal comma, switch the number format to use a point)")
    if not math.isfinite(v):
        raise ProbeDataError(f"probe data line {line_no}: non-finite number {tok!r}")
    return v


def _i(tok: str, line_no: int) -> int:
    v = _f(tok, line_no)
    if abs(v - round(v)) > 1e-6:
        raise ProbeDataError(f"probe data line {line_no}: expected a whole number, got {tok!r}")
    return int(round(v))


def parse_probe_data(text: str, path: str = "") -> ProbeData:
    header = None
    cfg: List[float] = []
    date = ""
    touches: List[Touch] = []
    end_count = None
    cal_ref: Optional[float] = None
    detected: Optional[Tuple[int, int]] = None
    opened: Dict[int, Tuple[int, int]] = {}
    for n, raw in enumerate(split_lines(text), 1):
        s = raw.replace('"', "").strip()
        if not s:
            continue
        tag, _, rest = s.partition(",")
        tag = tag.strip().upper()
        if tag == "DT":
            date = rest.strip()
            continue
        f = rest.split(",") if rest else []
        if tag == "VD":
            if len(f) != 11:
                raise ProbeDataError(f"probe data line {n}: header has {len(f)} fields, expected 11 "
                                     "(data written by a different vd_probe.sbp version?)")
            if _i(f[0], n) != 1:
                raise ProbeDataError(f"probe data line {n}: unsupported data version {f[0]!r}")
            header = dict(units=_i(f[1], n), sim=_i(f[2], n), tip=_f(f[3], n), nominal=_f(f[4], n),
                          nholes=_i(f[5], n), passes=_i(f[6], n), spin=_i(f[7], n),
                          pin=_i(f[8], n), trig=_i(f[9], n), mode=_i(f[10], n))
        elif tag == "CFG":
            cfg = [_f(p, n) for p in f]
        elif tag == "T":
            if header is None:
                raise ProbeDataError(f"probe data line {n}: touch record before header")
            if len(f) != 10:
                raise ProbeDataError(f"probe data line {n}: touch record has {len(f)} fields, expected 10")
            t = Touch(hole=_i(f[0], n), orient=_i(f[1], n), pas=_i(f[2], n), axis=_i(f[3], n),
                      sign=_i(f[4], n), x=_f(f[5], n), y=_f(f[6], n), z=_f(f[7], n),
                      bx=_f(f[8], n), by=_f(f[9], n), line=n)
            if t.axis not in (1, 2) or t.sign not in (1, -1):
                raise ProbeDataError(f"probe data line {n}: bad axis/direction")
            touches.append(t)
        elif tag == "O":
            # half slot: the hole opens toward (axis, sign); the routine makes no touch in that direction
            if header is None or len(f) != 3:
                raise ProbeDataError(f"probe data line {n}: bad open side record")
            h, ax, sg = _i(f[0], n), _i(f[1], n), _i(f[2], n)
            if ax not in (1, 2) or sg not in (1, -1):
                raise ProbeDataError(f"probe data line {n}: bad open side")
            if not 1 <= h <= header["nholes"]:
                raise ProbeDataError(f"probe data line {n}: open side for hole {h} out of range")
            opened[h] = (ax, sg)
        elif tag == "H":
            pass  # routine's own running estimate - informational only
        elif tag == "CAL":
            cal_ref = _f(f[0], n) if f else 0.0
        elif tag == "DET":
            if len(f) != 2:
                raise ProbeDataError(f"probe data line {n}: input detection record needs 2 fields")
            detected = (_i(f[0], n), _i(f[1], n))
        elif tag == "END":
            end_count = _i(f[0], n) if f else -1
        else:
            raise ProbeDataError(f"probe data line {n}: unknown record {tag!r}")
    if header is None:
        raise ProbeDataError("probe data has no header - probing was cancelled or this is not a "
                             "vd_probe.sbp data file. Run vd_probe.sbp again.")
    if end_count is None:
        raise ProbeDataError("probe data is INCOMPLETE (no END record) - probing was aborted. "
                             "Run vd_probe.sbp again.")
    if header["units"] not in (0, 1):
        raise ProbeDataError("probe data: unknown units code")
    lo = 1 if cal_ref is not None else 2
    if not lo <= header["nholes"] <= 6:
        raise ProbeDataError(f"probe data: the number of holes must be {lo} to 6")
    if header["passes"] < 1:
        raise ProbeDataError("probe data: at least one fine pass is required")
    if end_count != header["nholes"]:
        raise ProbeDataError("probe data: END record does not match the number of holes")
    return ProbeData(units=header["units"], sim=bool(header["sim"]), tip=header["tip"],
                     nominal=header["nominal"], nholes=header["nholes"], passes=header["passes"],
                     spin180=bool(header["spin"]), pin=header["pin"], trig=header["trig"],
                     mode=header["mode"], date=date, cfg=cfg, touches=touches, path=path,
                     cal_ref=cal_ref, detected=detected, open=opened)


# --------------------------------------------------------------------------------------
# hole centers
# --------------------------------------------------------------------------------------
@dataclass
class HoleResult:
    index: int                       # 1-based probing order
    center: Tuple[float, float]      # final estimate (orientation-averaged)
    per_orient: Dict[int, Tuple[float, float]]
    dia_x: float
    dia_y: float
    repeat: float                    # max distance of a fine-pass center from its orientation mean
    n_passes: int
    ecc: Optional[Tuple[float, float]] = None   # probe tip offset from spindle axis (spin180)
    z: float = 0.0
    open: Optional[Tuple[int, int]] = None      # half slot: (axis, sign) it opens to; centre = round end
    wall_margin: Optional[float] = None         # half slot: how far behind the round end the wall touches were

    @property
    def dia(self) -> float:
        return (self.dia_x + self.dia_y) / 2.0


def _slot_pass(wp: Touch, wm: Touch, end: Touch, inward: Tuple[float, float],
               theta: float) -> Tuple[Tuple[float, float], float, float]:
    """One pass on a half slot: two wall touches in opposite directions across the slot, then the end
    touch along the slot from their midpoint. The touches run along the machine axes and the slot is
    turned by theta. Returns the centre of the round end, the ball-centre half-width rho and how far
    behind that centre the wall touches were. A push past contact that is the same on both axes (ball
    size, pretravel, latency) widens the wall pair exactly as much as it moves the end touch, so it
    cancels; only a difference between the axes remains, with opposite signs at opposite slots."""
    mx, my = (wp.x + wm.x) / 2.0, (wp.y + wm.y) / 2.0
    rho = math.hypot(wp.x - wm.x, wp.y - wm.y) / 2.0 * math.cos(theta)
    c, s = math.cos(theta), math.sin(theta)
    ux, uy = c * inward[0] - s * inward[1], s * inward[0] + c * inward[1]
    wx, wy = end.x - mx, end.y - my
    uw = ux * wx + uy * wy
    disc = uw * uw - (wx * wx + wy * wy) + rho * rho
    if disc < 0:
        raise ProbeDataError(f"probe data line {end.line}: the slot end touch does not fit the slot width")
    t = uw - math.sqrt(disc)                 # centre = midpoint + t * slot axis, rho from the end touch
    return (mx + t * ux, my + t * uy), rho, t


def compute_holes(pd: ProbeData, theta: float = 0.0) -> Tuple[List[HoleResult], Tuple[float, float], Tuple[float, float]]:
    """Returns hole results, the X/Y zero's table-base position %(6)/%(7) and its spread.
    theta: board rotation from a first fit; only half slots use it (their touches run along the machine
    axes, so a turned slot reads a little wide)."""
    orients = [0, 1] if pd.spin180 else [0]
    by_key: Dict[Tuple[int, int, int, int, int], Touch] = {}
    for t in pd.touches:
        if not (1 <= t.hole <= pd.nholes):
            raise ProbeDataError(f"probe data line {t.line}: hole number {t.hole} out of range")
        if t.orient not in orients:
            raise ProbeDataError(f"probe data line {t.line}: unexpected spindle orientation {t.orient}")
        if t.pas < 0 or t.pas > pd.passes:
            raise ProbeDataError(f"probe data line {t.line}: pass number {t.pas} out of range")
        if pd.open.get(t.hole) == (t.axis, t.sign):
            raise ProbeDataError(f"probe data line {t.line}: a touch toward the open side of slot {t.hole}")
        key = (t.hole, t.orient, t.pas, t.axis, t.sign)
        if key in by_key:
            raise ProbeDataError(f"probe data line {t.line}: duplicate touch record")
        by_key[key] = t
    if not pd.touches:
        raise ProbeDataError("probe data contains no touches")

    # %(6)/%(7) ("Base Coord") on the user's SB3 do NOT follow the tool: every touch of a real run read
    # the same value (1.8707 while %(1) read 34.2). They hold the table position of the current X/Y zero,
    # so they only change when X or Y is zeroed. Touch positions therefore come from the working
    # location %(1)/%(2) (0.001 resolution), and %(6)/%(7) show whether zero changed during probing.
    basex = [t.bx for t in pd.touches]
    basey = [t.by for t in pd.touches]
    offset = (sorted(basex)[len(basex) // 2], sorted(basey)[len(basey) // 2])
    spread = (max(basex) - min(basex), max(basey) - min(basey))

    def wx(t: Touch) -> float:
        return t.x

    def wy(t: Touch) -> float:
        return t.y

    holes: List[HoleResult] = []
    for h in range(1, pd.nholes + 1):
        per_orient: Dict[int, Tuple[float, float]] = {}
        dxs: List[float] = []
        dys: List[float] = []
        zs: List[float] = []
        repeat = 0.0
        margins: List[float] = []
        op = pd.open.get(h)
        for o in orients:
            cxs, cys = [], []
            for k in range(1, pd.passes + 1):
                if op is not None:
                    ac = 3 - op[0]
                    try:
                        wp, wm = by_key[(h, o, k, ac, 1)], by_key[(h, o, k, ac, -1)]
                        end = by_key[(h, o, k, op[0], -op[1])]
                    except KeyError:
                        raise ProbeDataError(f"probe data: slot {h} orientation {o} pass {k} is missing touches")
                    if not (wx(wp) > wx(wm) if ac == 1 else wy(wp) > wy(wm)):
                        raise ProbeDataError(f"probe data: slot {h} pass {k} touches are in the wrong order")
                    inward = (-op[1], 0.0) if op[0] == 1 else (0.0, -op[1])
                    (cx, cy), rho, margin = _slot_pass(wp, wm, end, inward, theta)
                    cxs.append(cx)
                    cys.append(cy)
                    dxs.append(2 * rho + pd.tip)          # slot width; no second diameter to compare
                    dys.append(2 * rho + pd.tip)
                    margins.append(margin)
                    zs.extend([wp.z, wm.z, end.z])
                    continue
                try:
                    xp = by_key[(h, o, k, 1, 1)]
                    xm = by_key[(h, o, k, 1, -1)]
                    yp = by_key[(h, o, k, 2, 1)]
                    ym = by_key[(h, o, k, 2, -1)]
                except KeyError:
                    raise ProbeDataError(f"probe data: hole {h} orientation {o} pass {k} is missing touches")
                if not (wx(xp) > wx(xm) and wy(yp) > wy(ym)):
                    raise ProbeDataError(f"probe data: hole {h} pass {k} touches are in the wrong order")
                cxs.append((wx(xp) + wx(xm)) / 2.0)
                cys.append((wy(yp) + wy(ym)) / 2.0)
                dxs.append(wx(xp) - wx(xm) + pd.tip)
                dys.append(wy(yp) - wy(ym) + pd.tip)
                zs.extend([xp.z, xm.z, yp.z, ym.z])
            c = (_mean(cxs), _mean(cys))
            per_orient[o] = c
            repeat = max(repeat, max(math.hypot(a - c[0], b - c[1]) for a, b in zip(cxs, cys)))
        if pd.spin180:
            c0, c1 = per_orient[0], per_orient[1]
            center = ((c0[0] + c1[0]) / 2.0, (c0[1] + c1[1]) / 2.0)
            # spindle position when the tip is centred = hole - tip_offset, so
            # orientation 0 reads (hole - e) and orientation 1 reads (hole + e)
            ecc = ((c1[0] - c0[0]) / 2.0, (c1[1] - c0[1]) / 2.0)
        else:
            center = per_orient[0]
            ecc = None
        holes.append(HoleResult(index=h, center=center, per_orient=per_orient, dia_x=_mean(dxs),
                                dia_y=_mean(dys), repeat=repeat, n_passes=pd.passes, ecc=ecc,
                                z=_mean(zs), open=op, wall_margin=min(margins) if margins else None))
    return holes, offset, spread


# --------------------------------------------------------------------------------------
# rigid fit and hole matching
# --------------------------------------------------------------------------------------
@dataclass
class Fit:
    theta: float          # radians, CCW positive
    tx: float
    ty: float
    residuals: List[Tuple[float, float]]

    @property
    def c(self) -> float:
        return math.cos(self.theta)

    @property
    def s(self) -> float:
        return math.sin(self.theta)

    def point(self, x: float, y: float) -> Tuple[float, float]:
        c, s = self.c, self.s
        return (c * x - s * y + self.tx, s * x + c * y + self.ty)

    def vec(self, x: float, y: float) -> Tuple[float, float]:
        c, s = self.c, self.s
        return (c * x - s * y, s * x + c * y)

    @property
    def rms(self) -> float:
        return math.sqrt(_mean([rx * rx + ry * ry for rx, ry in self.residuals]))

    @property
    def max_resid(self) -> float:
        return max(math.hypot(rx, ry) for rx, ry in self.residuals)


def fit_rigid(design: Sequence[Tuple[float, float]], measured: Sequence[Tuple[float, float]]) -> Fit:
    """Least-squares rotation + translation (no scale) with measured ~= R * design + t."""
    n = len(design)
    if n < 2 or n != len(measured):
        raise VDError("need at least two matching hole pairs")
    pcx = _mean([p[0] for p in design]); pcy = _mean([p[1] for p in design])
    mcx = _mean([m[0] for m in measured]); mcy = _mean([m[1] for m in measured])
    sdot = scross = 0.0
    for (px, py), (mx, my) in zip(design, measured):
        px -= pcx; py -= pcy; mx -= mcx; my -= mcy
        sdot += px * mx + py * my
        scross += px * my - py * mx
    if abs(sdot) + abs(scross) < 1e-12:
        raise VDError("hole positions coincide - cannot determine rotation")
    th = math.atan2(scross, sdot)
    c, s = math.cos(th), math.sin(th)
    tx = mcx - (c * pcx - s * pcy)
    ty = mcy - (s * pcx + c * pcy)
    res = [((c * px - s * py + tx) - mx, (s * px + c * py + ty) - my)
           for (px, py), (mx, my) in zip(design, measured)]
    return Fit(th, tx, ty, res)


def _spacing_errors(design, measured) -> List[Tuple[int, int, float, float]]:
    out = []
    for i, j in itertools.combinations(range(len(design)), 2):
        dd = math.dist(design[i], design[j])
        dm = math.dist(measured[i], measured[j])
        out.append((i, j, dd, dm - dd))
    return out


def layout_is_symmetric(design: Sequence[DesignHole], tol: float, dtol: float) -> bool:
    """True if the hole layout maps onto itself under a 180 deg turn. Such a layout cannot tell a
    board flipped over the intended axis from one flipped over the other axis (or turned end for
    end): the holes simply trade places and every check still passes."""
    cx = _mean([d[0] for d in design]); cy = _mean([d[1] for d in design])
    for x, y, d in design:
        rx, ry = 2 * cx - x, 2 * cy - y
        if not any(math.hypot(rx - x2, ry - y2) <= tol and
                   (d is None or d2 is None or abs(d - d2) <= dtol) for x2, y2, d2 in design):
            return False
    return True


@dataclass
class Alignment:
    fit: Fit
    perm: Tuple[int, ...]          # perm[i] = index (0-based probing order) of the hole matched to design i
    spacing: List[Tuple[int, int, float, float]]
    size_bias: float               # mean (measured - design) diameter
    failures: List[str]
    warnings: List[str]


def _size_match(design: Sequence[DesignHole], holes: Sequence[HoleResult], perm, nominal: float,
                abs_tol: float, rel_tol: float) -> Tuple[bool, float, float]:
    """Hole identity by size. The probe trips after a little deflection, so every measured
    diameter reads a similar amount large - compare sizes relative to that common bias."""
    r = [holes[perm[i]].dia - (d[2] if d[2] is not None else nominal) for i, d in enumerate(design)]
    bias = _mean(r)
    worst_rel = max(abs(x - bias) for x in r)
    worst_abs = max(abs(x) for x in r)
    return (worst_rel <= rel_tol and worst_abs <= abs_tol), bias, worst_rel


def align(design: Sequence[DesignHole], holes: Sequence[HoleResult], th: Dict[str, float],
          scale: float, nominal: float) -> Alignment:
    n = len(design)
    if n != len(holes):
        raise VDError(f"{len(holes)} holes were probed but {n} design positions were given")
    if n > 6:
        raise VDError("at most 6 reference holes are supported")
    given = [d[2] is not None for d in design]
    if any(given) and not all(given):
        raise VDError("give a diameter for every hole or for none")
    tol = {k: (v * scale if k in DIST_KEYS else v) for k, v in th.items()}
    max_rot = math.radians(th["max_rotation_deg"])
    dxy = [(d[0], d[1]) for d in design]
    measured = [h.center for h in holes]

    failures: List[str] = []
    warnings: List[str] = []
    if layout_is_symmetric(design, tol["symmetry_tol"], 4 * tol["diameter_rel_tol"]):
        msg = ("this hole layout looks the same after a half turn, so a board flipped the WRONG WAY "
               "(end over end instead of side over side) would pass unnoticed. Make the holes different "
               "sizes at least 0.1 in apart (e.g. 0.500 and 0.625 in) or add a hole off-centre")
        if th.get("allow_symmetric_layout", 0):
            warnings.append(msg)
        else:
            failures.append(msg + "; or set allow_symmetric_layout to 1 in vd_settings.json")

    cands = []
    size_ok_any = False
    for perm in itertools.permutations(range(n)):
        ok_size, bias, worst_rel = _size_match(design, holes, perm, nominal,
                                               tol["diameter_abs_tol"], tol["diameter_rel_tol"])
        if not ok_size:
            continue
        size_ok_any = True
        m = [measured[perm[i]] for i in range(n)]
        try:
            f = fit_rigid(dxy, m)
        except VDError:
            continue
        sp = _spacing_errors(dxy, m)
        worst = max(abs(e) for *_, e in sp)
        cands.append((f, perm, sp, worst, bias))
    if not size_ok_any:
        sizes = ", ".join(f"{h.dia:.4f}" for h in holes)
        want = ", ".join(f"{(d[2] if d[2] is not None else nominal):.4f}" for d in design)
        failures.append(f"measured hole sizes ({sizes}) do not match the design sizes ({want}) in any "
                        "order - wrong holes, wrong diameters entered, or wrong vd_tip / vd_hole setting")
        ident = tuple(range(n))
        f = fit_rigid(dxy, measured)
        return Alignment(f, ident, _spacing_errors(dxy, measured), 0.0, failures, warnings)
    if not cands:
        raise VDError("hole positions coincide - cannot determine rotation")

    def valid(c):
        f, _, _, worst, _ = c
        return (abs(f.theta) <= max_rot and worst <= tol["spacing_fail"]
                and (n < 3 or f.max_resid <= tol["residual_fail"]))

    ok = sorted([c for c in cands if valid(c)], key=lambda c: c[0].rms)
    if not ok:
        f, perm, sp, worst, bias = min(cands, key=lambda c: (abs(c[0].theta), c[0].rms))
        if abs(f.theta) > max_rot:
            failures.append(f"rotation {math.degrees(f.theta):+.3f} deg exceeds the "
                            f"{th['max_rotation_deg']:.1f} deg limit - board flipped the wrong way, wrong "
                            "hole coordinates, or wrong side")
        if worst > tol["spacing_fail"]:
            failures.append(f"hole spacing differs from the design by {worst:.4f} "
                            f"(limit {tol['spacing_fail']:.4f}) - wrong hole coordinates or a bad touch")
        if n >= 3 and f.max_resid > tol["residual_fail"]:
            failures.append(f"fit residual {f.max_resid:.4f} exceeds {tol['residual_fail']:.4f}")
        return Alignment(f, perm, sp, bias, failures, warnings)
    if len(ok) > 1 and ok[1][0].rms < 3 * ok[0][0].rms + tol["spacing_warn"]:
        failures.append("hole matching is ambiguous (symmetric layout) - use holes of different sizes")
    f, perm, sp, worst, bias = ok[0]
    if perm != tuple(range(n)):
        warnings.append("holes were probed in a different order than listed; matched by size and "
                        f"position (listed hole i = probed hole {[p + 1 for p in perm]})")
    if worst > tol["spacing_warn"]:
        warnings.append(f"hole spacing differs from design by up to {worst:.4f} "
                        "(material movement, machine scale, or imprecise design coordinates)")
    if n >= 3 and f.max_resid > tol["residual_warn"]:
        warnings.append(f"fit residual up to {f.max_resid:.4f}")
    return Alignment(f, perm, sp, bias, failures, warnings)


def hole_checks(pd: ProbeData, holes: Sequence[HoleResult], spread: Tuple[float, float],
                th: Dict[str, float]) -> Tuple[List[str], List[str]]:
    s = pd.scale
    fails: List[str] = []
    warns: List[str] = []
    for h in holes:
        if h.wall_margin is not None and h.wall_margin < th["slot_wall_margin"] * s:
            fails.append(f"slot {h.index}: the wall touches were {h.wall_margin:.3f} from the centre of the round "
                         "end - they must be on the straight part of the slot. Make the slot longer")
        rnd = abs(h.dia_x - h.dia_y)
        if rnd > th["roundness_fail"] * s:
            fails.append(f"hole {h.index}: X and Y diameters differ by {rnd:.4f} - chips/fuzz in the hole, "
                         "a false touch, or a damaged hole")
        elif rnd > th["roundness_warn"] * s:
            warns.append(f"hole {h.index}: X and Y diameters differ by {rnd:.4f}")
        if h.repeat > th["repeat_fail"] * s:
            fails.append(f"hole {h.index}: fine-pass centers scatter by {h.repeat:.4f} - unreliable touches")
        elif h.repeat > th["repeat_warn"] * s:
            warns.append(f"hole {h.index}: fine-pass centers scatter by {h.repeat:.4f}")
    if pd.spin180:
        eccs = [h.ecc for h in holes if h.ecc is not None]
        mag = max(math.hypot(*e) for e in eccs)
        if mag > th["eccentricity_warn"] * s:
            warns.append(f"probe tip is {mag:.4f} off the spindle axis - check the probe stylus / collet")
        ex = _mean([e[0] for e in eccs]); ey = _mean([e[1] for e in eccs])
        sp = max(math.hypot(e[0] - ex, e[1] - ey) for e in eccs)
        if sp > th["ecc_consistency_warn"] * s:
            warns.append(f"probe offset measured at different holes disagrees by {sp:.4f} - "
                         "the spindle may not have been turned a consistent 180 deg")
    if max(spread) > th["offset_consistency"] * s:
        if pd.sim:
            warns.append("simulated data without a fixed X/Y zero - the 'zero unchanged' check is left out")
        else:
            fails.append("X/Y zero was changed during probing (e.g. while the keypad was open between holes) - "
                         "probe all holes again without re-zeroing")
    return fails, warns


# --------------------------------------------------------------------------------------
# job file: material box from the VCarve / Aspire ShopBot post header
# --------------------------------------------------------------------------------------
def material_box(lines: Sequence[str]) -> Optional[Tuple[float, float, float, float]]:
    w = h = None
    origin = None
    for ln in lines[:60]:
        m = re.search(r"Length of material in X\s*=\s*([-+0-9.]+)", ln, re.I)
        if m:
            w = float(m.group(1))
        m = re.search(r"Length of material in Y\s*=\s*([-+0-9.]+)", ln, re.I)
        if m:
            h = float(m.group(1))
        m = re.search(r"Home Position Information\s*=\s*([A-Za-z ]+)", ln, re.I)
        if m:
            origin = m.group(1).strip().lower()
    if w is None or h is None or origin is None:
        return None
    if origin.startswith("bottom left"):
        return (0.0, 0.0, w, h)
    if origin.startswith("top left"):
        return (0.0, -h, w, 0.0)
    if origin.startswith("top right"):
        return (-w, -h, 0.0, 0.0)
    if origin.startswith("bottom right"):
        return (-w, 0.0, 0.0, h)
    if origin.startswith("center") or origin.startswith("centre"):
        return (-w / 2, -h / 2, w / 2, h / 2)
    return None


def toolpath_extents(lines: Sequence[str]) -> Optional[Tuple[float, float, float, float]]:
    """X/Y range of the job's move end points in its own (design) coordinates, or None."""
    tr = SbpTransformer(Fit(0.0, 0.0, 0.0, [(0.0, 0.0)]))
    try:
        tr.transform(list(lines))
    except VDError:
        return None
    return tr.stats.toolpath_box()


def box_coverage(tp: Tuple[float, float, float, float], box: Tuple[float, float, float, float]) -> float:
    """Fraction of the toolpath extent rectangle that lies inside the stock box."""
    tx0, ty0, tx1, ty1 = tp
    pad = 0.01
    tx0 -= pad; ty0 -= pad; tx1 += pad; ty1 += pad
    ix = max(0.0, min(tx1, box[2]) - max(tx0, box[0]))
    iy = max(0.0, min(ty1, box[3]) - max(ty0, box[1]))
    return (ix * iy) / ((tx1 - tx0) * (ty1 - ty0))


SIDE_NAMES = {(1, -1): "-X", (1, 1): "+X", (2, -1): "-Y", (2, 1): "+Y"}


def open_side(x: float, y: float, box: Tuple[float, float, float, float]) -> Tuple[int, int]:
    """A half slot opens toward its nearest stock edge: (axis, sign)."""
    x0, y0, x1, y1 = box
    return min((x - x0, (1, -1)), (x1 - x, (1, 1)), (y - y0, (2, -1)), (y1 - y, (2, 1)))[1]


def slot_opening(d: DesignHole, box: Tuple[float, float, float, float]) -> Tuple[float, float]:
    """Where a half slot's axis meets the stock edge - the opening the operator jogs to for the rough fit."""
    ax, sg = open_side(d[0], d[1], box)
    if ax == 1:
        return (box[2] if sg > 0 else box[0], d[1])
    return (d[0], box[3] if sg > 0 else box[1])


def _same_layout(a: Sequence[DesignHole], b: Sequence[DesignHole], tol: float, dtol: float) -> bool:
    return all(any(math.hypot(x - x2, y - y2) <= tol and (d is None or d2 is None or abs(d - d2) <= dtol)
                   for x2, y2, d2 in b) for x, y, d in a)


def has_mirror_symmetry(design: Sequence[DesignHole], tol: float, dtol: float) -> bool:
    """True if the layout is congruent to its own mirror image (always true for 2 holes; false for
    3+ holes not in a line unless the layout itself is mirror-symmetric). For such layouts, side-A
    coordinates typed by mistake would fit the probe data as well as the right ones."""
    mir = [(x, -y) for x, y, _ in design]
    pts = [(x, y) for x, y, _ in design]
    n = len(design)
    for perm in itertools.permutations(range(n)):
        if any(design[i][2] is not None and design[perm[i]][2] is not None and
               abs(design[i][2] - design[perm[i]][2]) > dtol for i in range(n)):
            continue
        try:
            f = fit_rigid([mir[perm[i]] for i in range(n)], pts)
        except VDError:
            continue
        if f.max_resid <= tol:
            return True
    return False


def mirror_alternatives(design: Sequence[DesignHole], holes: Sequence[HoleResult], th: Dict[str, float],
                        scale: float, nominal: float, box) -> List[Tuple[str, Fit]]:
    """The classic mistake: typing the SIDE-A numbers of the holes instead of the side-B ones.
    Side-A numbers are the side-B numbers mirrored about the flip axis. If the mirrored layout
    fits the probe data just as well, the probe data cannot tell the two apart and the job could
    be shifted by up to the board width. Returns the mirrored layouts that fit."""
    x0, y0, x1, y1 = box
    tol = th["symmetry_tol"] * scale
    dtol = 4 * th["diameter_rel_tol"] * scale
    quiet = dict(th, allow_symmetric_layout=1)
    out = []
    for name, mir in (("left-right", [(x0 + x1 - x, y, d) for x, y, d in design]),
                      ("end-over-end", [(x, y0 + y1 - y, d) for x, y, d in design])):
        if _same_layout(mir, design, tol, dtol):
            continue                       # holes on / symmetric about this flip axis: no ambiguity
        alt = align(mir, holes, quiet, scale, nominal)
        if not alt.failures:
            out.append((name, alt.fit))
    return out


# --------------------------------------------------------------------------------------
# OpenSBP transformer
# --------------------------------------------------------------------------------------
NUM_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
LABEL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*$")
WORD_RE = re.compile(r"^([A-Za-z_]+)")
CUSTOM_DIGIT_RE = re.compile(r"^(C\d+)(?=$|[\s,])(.*)$", re.I | re.S)
CMD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9#])(?=$|[\s,])(.*)$", re.S)
IF_RE = re.compile(r"^IF\b(.*?)\bTHEN\b(.*)$", re.I | re.S)
ONINP_RE = re.compile(r"^ON\s+INP(?:UT)?\s*\((.*?)\)\s*(.*)$", re.I | re.S)
UNIT_CHECK_RE = re.compile(r"%\(\s*25\s*\)\s*=\s*([01])\s*THEN\s+GOTO", re.I)
SR_LINE_RE = re.compile(r"^\s*SR(?=$|[\s,'])", re.I)

LINEAR = {"M2": 2, "M3": 3, "M4": 4, "M5": 5, "J2": 2, "J3": 3, "J4": 4, "J5": 5}
SINGLE = {"MX": ("M2", 0), "MY": ("M2", 1), "JX": ("J2", 0), "JY": ("J2", 1)}
PASS_CMDS = {
    # speeds / values / settings that do not touch X-Y geometry
    "MS", "JS", "VS", "VR", "VC", "VP", "VB", "VH", "VI", "VL", "VN",
    "SF", "SW", "SO", "SV", "SL", "SM", "SP", "SC",
    "TR",                                  # spindle RPM
    "MZ", "JZ", "MA", "MB", "JA", "JB",    # non-XY motion
    "ZZ", "ZA", "ZB",                      # zero Z / A / B only
    "HA", "HB", "HC", "HE", "HN", "HQ", "HR", "HT", "HU", "HW",
    "RA", "RI", "RS", "RZ", "FE", "FS", "UD", "UL", "UU", "UV",
    "MO", "M0",                            # motors off
}
REJECT_CMDS = {
    "CC": "CC circles are placed relative to the tool position with fixed angles",
    "CA": "CA arches have a fixed alignment angle",
    "CP": "CP circles are not supported (only CG arcs are)",
    "CR": "CR rectangles are always axis-aligned and cannot be rotated",
    "MD": "MD moves use fixed angles",
    "MI": "MI indexer/oscillation is not supported",
    "MN": "MN (nudge) changes the tool location during the file",
    "FP": "calls another part file, which would not be aligned",
    "FG": "calls another part file, which would not be aligned",
    "FC": "file conversion inside a job is not supported",
    "FN": "file commands inside a job are not supported",
    "SI": "SI send-lines is not supported",
    "ZX": "re-zeroes X", "ZY": "re-zeroes Y", "Z2": "re-zeroes X and Y", "Z3": "re-zeroes X and Y",
    "Z4": "re-zeroes X and Y", "Z5": "re-zeroes X and Y", "ZT": "re-zeroes table base coordinates",
    "ST": "resets the location to table base coordinates", "UZ": "zeroes all locations",
    "UR": "resets settings", "US": "saves settings", "UI": "installs firmware",
    "VO": "temporary tool offsets would shift X/Y",
    "VD": "display/units settings inside a job are not allowed",
    "VU": "unit calibration inside a job is not allowed",
    "RP": "replays recorded moves", "RR": "replays recorded moves",
    "TC": "virtual tools are not supported in a job", "TD": "virtual tools are not supported in a job",
    "TF": "virtual tools are not supported in a job", "TH": "virtual tools are not supported in a job",
    "TI": "virtual tools are not supported in a job", "TS": "virtual tools are not supported in a job",
    "TT": "virtual tools are not supported in a job", "TU": "virtual tools are not supported in a job",
}
KW_PASS = {"GOTO", "RETURN", "END", "ENDALL", "PRINT", "OPEN", "CLOSE", "WRITE", "PLAY", "WARNING",
           "REM", "EXIT", "SETUSRVAL", "SETUSRVALCLRD", "GETUSRVAL", "GETUSRVALCLRD", "GETUSRPATH",
           "GETAPPPATH", "GETPARTFILENAME", "SETSPINDLESTATUS", "GETSPINDLESTATUS"}
KW_OPERATOR = {"MSGBOX", "INPUT"}          # operator gets control: re-check X/Y zero afterwards
CHECK_LABEL = "VDALIGN_CHECK"
MARKER = "VIRTUAL DOWEL ALIGNED FILE"


def split_comment(line: str) -> Tuple[str, str]:
    """Split at the first apostrophe that is not inside double quotes."""
    inq = False
    for i, ch in enumerate(line):
        if ch == '"':
            inq = not inq
        elif ch == "'" and not inq:
            return line[:i], line[i:]
    return line, ""


def split_params(rest: str) -> List[str]:
    """OpenSBP: the first comma OR the run of whitespace after the command is the separator
    (Programming Handbook: 'M2,, 4.2' and 'M2 , 4.2' both leave X blank)."""
    if rest.strip() == "":
        return []
    if rest[0] == ",":
        body = rest[1:]
    elif rest[0].isspace():
        body = rest.lstrip()
    else:
        raise ValueError("no separator after command")
    return [p.strip() for p in body.split(",")]


def fmt(v: float) -> str:
    s = f"{v:.6f}"
    return "0.000000" if s == "-0.000000" else s


@dataclass
class TransformStats:
    lines: int = 0
    moves: int = 0
    arcs: int = 0
    checks: int = 0
    custom_cuts: List[str] = field(default_factory=list)
    xmin: float = math.inf
    xmax: float = -math.inf
    ymin: float = math.inf
    ymax: float = -math.inf
    file_units: Optional[int] = None
    warnings: List[str] = field(default_factory=list)
    cut_points: List[Tuple[float, float]] = field(default_factory=list)   # design XY of every feed move (M/CG)
    design_box: List[float] = field(default_factory=lambda: [math.inf, math.inf, -math.inf, -math.inf])

    def extent(self, p: Tuple[float, float]) -> None:
        self.xmin = min(self.xmin, p[0]); self.xmax = max(self.xmax, p[0])
        self.ymin = min(self.ymin, p[1]); self.ymax = max(self.ymax, p[1])

    def design_extent(self, p: Tuple[float, float]) -> None:
        b = self.design_box
        b[0] = min(b[0], p[0]); b[1] = min(b[1], p[1]); b[2] = max(b[2], p[0]); b[3] = max(b[3], p[1])

    def toolpath_box(self) -> Optional[Tuple[float, float, float, float]]:
        b = self.design_box
        return None if b[0] > b[2] else (b[0], b[1], b[2], b[3])


class SbpTransformer:
    """Applies p -> R p + t to every X/Y location of an OpenSBP file (exact, line by line)."""

    def __init__(self, fit: Fit):
        self.fit = fit
        self.abs: Optional[bool] = True    # None = unknown (after a jump in a file that uses SR)
        self.mode_known = False
        self.uses_sr = False
        self.pos: Optional[Tuple[float, float]] = None   # current XY in DESIGN coordinates
        self.ij: Optional[Tuple[float, float]] = None     # modal CG center offsets (design frame)
        self.stats = TransformStats()
        self._warned: set = set()

    # -- helpers ------------------------------------------------------------------------
    def _warn(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self.stats.warnings.append(msg)

    @staticmethod
    def _num(s: str, what: str) -> float:
        if not NUM_RE.match(s):
            raise ValueError(f"{what} must be a plain number, found {s!r} "
                             "(variables/expressions in X/Y/I/J cannot be aligned)")
        return float(s)

    def _need_mode(self) -> None:
        if self.abs is None:
            raise ValueError("this move follows a label or GOSUB in a file that uses relative mode (SR); "
                             "whether it is absolute or relative cannot be known - not supported")
        if not self.mode_known:
            self.mode_known = True
            self._warn("mode", "no SA/SR before the first move - absolute mode assumed (SA added to header)")

    def _need_pos(self, what: str = "this move leaves X or Y blank (or moves one axis only)") -> Tuple[float, float]:
        if self.pos is None:
            raise ValueError(f"{what} but the current position is unknown here (start of file, after a "
                             "label, GOSUB, JH/MH, SK or a custom cut); it cannot be aligned safely")
        return self.pos

    def _jump_target(self) -> None:
        """Control may arrive here from elsewhere: forget everything modal."""
        self.pos = None
        self.ij = None
        if self.uses_sr:
            self.abs = None

    # -- main entry -----------------------------------------------------------------------
    def transform(self, lines: List[str]) -> List[str]:
        self.uses_sr = any(SR_LINE_RE.match(split_comment(ln)[0]) for ln in lines)
        out: List[str] = []
        for n, line in enumerate(lines, 1):
            self.stats.lines += 1
            try:
                new, extra = self._line(line)
            except ValueError as e:
                raise TransformError(n, line, str(e))
            out.append(new)
            out.extend(extra)
            self.stats.checks += len(extra)
        return out

    def _line(self, line: str) -> Tuple[str, List[str]]:
        code, comment = split_comment(line)
        s = code.strip()
        if s == "":
            if MARKER in comment.upper():
                raise ValueError("this file was already aligned by Virtual Dowel - use the original job file")
            return line, []
        indent = code[: len(code) - len(code.lstrip())]
        ws = code[len(code.rstrip()):]

        def rebuild(new_code: str) -> str:
            return indent + new_code + ws + comment

        check = [indent + "GOSUB " + CHECK_LABEL]

        if LABEL_RE.match(s):
            if s.upper().startswith("VDALIGN_"):
                raise ValueError("this file already contains Virtual Dowel labels - use the original job file")
            self._jump_target()
            return line, []
        if s.startswith("&"):
            return line, []                                   # variable assignment
        m = WORD_RE.match(s)
        word = m.group(1).upper() if m else ""
        if word == "IF":
            mi = IF_RE.match(s)
            if not mi:
                raise ValueError("IF without THEN")
            um = UNIT_CHECK_RE.search(s)
            if um and "GOTO" in mi.group(2).upper():
                # "IF %(25)=1 THEN GOTO UNIT_ERROR" -> the file is meant for inches (%(25)=0)
                self.stats.file_units = 1 - int(um.group(1))
            kind = self._clause(mi.group(2).strip())
            if kind == "custom":
                self.stats.custom_cuts.append(mi.group(2).strip())
                self.pos = None
                return line, check
            if kind == "gosub":
                self._jump_target()
            return line, []
        if word == "ON":
            mo = ONINP_RE.match(s)
            if not mo:
                raise ValueError("unrecognised ON statement")
            action = mo.group(2).strip()
            am = WORD_RE.match(action) if action else None
            if am and am.group(1).upper() in ("GOTO", "GOSUB"):
                raise ValueError("ON INPUT with GOTO/GOSUB inside a job file is not supported "
                                 "(unpredictable jumps break position tracking)")
            if am and am.group(1).upper() == "MSGBOX":
                return line, []            # message-only handler, e.g. Fusion's spindle-lockout warning
            if action:
                self._clause(action)
            return line, []
        if word == "GOSUB":
            self._jump_target()
            return line, []
        if word == "SHELL":
            self._warn("shell", "the job runs an external program (SHELL) - make sure it does not move the machine")
            return line, []
        if word == "PAUSE":
            arg = s[5:].strip()
            if arg and NUM_RE.match(arg):
                return line, []                               # timed pause
            return line, check                                # operator pause / PAUSE UNTIL
        if word in KW_OPERATOR:
            return line, check
        if word in KW_PASS:
            return line, []

        # ---- two-letter commands ----
        mc = CUSTOM_DIGIT_RE.match(s) or CMD_RE.match(s)
        if not mc:
            raise ValueError("unrecognised line (G-code and unknown commands are not supported; "
                             "post-process the job with a ShopBot/OpenSBP post)")
        cmd = mc.group(1).upper()
        rest = mc.group(2)
        try:
            params = split_params(rest)
        except ValueError:
            raise ValueError("cannot parse the command parameters")

        if cmd in ("C#", "CN") or re.match(r"^C\d+$", cmd):
            self.stats.custom_cuts.append(s)
            self.pos = None
            return line, check
        if cmd == "SA":
            self.abs = True; self.mode_known = True
            return line, []
        if cmd == "SR":
            self.abs = False; self.mode_known = True
            self._warn("sr", "file uses relative mode (SR); relative moves were rotated")
            return line, []
        if cmd in ("JH", "MH"):
            # Lifts to Safe-Z if below it, then goes to the machine's current X0,Y0 - exactly what the
            # unaligned file would do. It is a travel move, so it is passed through unchanged.
            self.pos = None
            self._warn("home", f"{cmd} (go to X0,Y0) is kept as-is: it returns to YOUR current X/Y zero at "
                               "safe height, not to the part origin")
            return line, []
        if cmd == "SK":
            self.pos = None
            self._warn("sk", "file opens the keypad (SK) - X/Y zero is re-checked when it closes")
            return line, check
        if cmd == "VA":
            bad = [i for i, p in enumerate(params) if p != "" and i not in (2, 3, 4)]
            if bad:
                raise ValueError("VA changes X/Y (or base) locations inside the job; not allowed")
            return line, []
        if cmd in PASS_CMDS:
            return line, []
        if cmd in REJECT_CMDS:
            raise ValueError(f"{cmd} is not supported: {REJECT_CMDS[cmd]}")
        if cmd in LINEAR:
            new = self._linear(cmd, mc.group(1), params)
            return (line if new is None else rebuild(new)), []
        if cmd in SINGLE:
            return rebuild(self._single(cmd, params)), []
        if cmd == "CG":
            return rebuild(self._cg(mc.group(1), params)), []
        raise ValueError(f"unknown command {cmd!r} (G-code is not supported; use a ShopBot/OpenSBP post)")

    def _clause(self, clause: str) -> str:
        """Classify the action part of an IF/ON statement: 'pass', 'gosub', 'custom'."""
        if clause == "" or clause.startswith("&"):
            return "pass"
        m = WORD_RE.match(clause)
        word = m.group(1).upper() if m else ""
        if word == "GOTO" or word in ("END", "ENDALL", "RETURN", "PRINT"):
            return "pass"
        if word == "GOSUB":
            return "gosub"
        if word in ("PAUSE", "MSGBOX", "INPUT"):
            raise ValueError("operator prompts inside IF/ON are not supported")
        mc = CUSTOM_DIGIT_RE.match(clause) or CMD_RE.match(clause)
        if not mc:
            raise ValueError("unrecognised statement after THEN")
        cmd = mc.group(1).upper()
        if cmd in ("C#", "CN") or re.match(r"^C\d+$", cmd):
            return "custom"
        if cmd in PASS_CMDS:
            return "pass"
        raise ValueError(f"{cmd} inside IF/ON is not supported (conditional motion or mode changes "
                         "cannot be aligned safely)")

    # -- motion ---------------------------------------------------------------------------
    def _opt(self, s: str, what: str) -> Optional[float]:
        return None if s == "" else self._num(s, what)

    def _apply(self, x: Optional[float], y: Optional[float]) -> Optional[Tuple[str, str]]:
        """Transform one X/Y pair (None = blank). Absolute: blanks take the current position.
        Relative: blanks are zero. Returns None when both are blank (X/Y untouched)."""
        self._need_mode()
        if x is None and y is None:
            return None
        if self.abs:
            if x is None or y is None:
                cur = self._need_pos()
                x = cur[0] if x is None else x
                y = cur[1] if y is None else y
            q = self.fit.point(x, y)
            self.pos = (x, y)
            self.stats.extent(q)
            self.stats.design_extent((x, y))
        else:
            # a relative move is aligned by rotating it only if the tool is at an aligned point; after
            # JH/MH, SK or a custom cut it is not (e.g. JH leaves it at machine X0,Y0)
            cur = self._need_pos("this is a relative move (SR)")
            dx = 0.0 if x is None else x
            dy = 0.0 if y is None else y
            q = self.fit.vec(dx, dy)
            self.pos = (cur[0] + dx, cur[1] + dy)
            self.stats.extent(self.fit.point(*self.pos))
            self.stats.design_extent(self.pos)
        return fmt(q[0]), fmt(q[1])

    @staticmethod
    def _join(cmd: str, p: List[str]) -> str:
        # blank parameters are written as a single space, like the ShopBot posts do ("CG, ,1,2")
        return cmd + "," + ",".join(x if x != "" else " " for x in p) if p else cmd

    def _linear(self, cmd: str, orig_cmd: str, p: List[str]) -> Optional[str]:
        """Returns the rewritten command, or None when X and Y are both blank (line kept as-is)."""
        n = LINEAR[cmd]
        if len(p) > n:
            raise ValueError(f"{cmd} has {len(p)} parameters, expected at most {n}")
        xs = p[0] if len(p) > 0 else ""
        ys = p[1] if len(p) > 1 else ""
        r = self._apply(self._opt(xs, "X"), self._opt(ys, "Y"))
        if cmd[0] == "M" and self.pos is not None:
            self.stats.cut_points.append(self.pos)          # also a Z-only feed move (plunge) at a known point
        if r is None:
            return None
        self.stats.moves += 1
        return self._join(orig_cmd, [r[0], r[1]] + p[2:])

    def _single(self, cmd: str, p: List[str]) -> str:
        if len(p) != 1 or p[0] == "":
            raise ValueError(f"{cmd} needs exactly one value")
        new_cmd, axis = SINGLE[cmd]
        v = self._num(p[0], "X" if axis == 0 else "Y")
        r = self._apply(v, None) if axis == 0 else self._apply(None, v)
        assert r is not None
        if new_cmd == "M2":
            self.stats.cut_points.append(self.pos)
        self.stats.moves += 1
        return f"{new_cmd},{r[0]},{r[1]}"

    def _cg(self, orig_cmd: str, p: List[str]) -> str:
        if len(p) > 14:
            raise ValueError("CG has too many parameters")
        self._need_mode()
        if not self.abs:
            raise ValueError("CG arcs in relative mode (SR) are not supported")
        p = list(p) + [""] * max(0, 5 - len(p))
        for idx, name in ((9, "X proportion"), (10, "Y proportion")):
            if idx < len(p) and p[idx] != "":
                if not NUM_RE.match(p[idx]) or abs(float(p[idx]) - 1.0) > 1e-9:
                    raise ValueError(f"CG with a {name} other than 1 (ellipse) cannot be rotated")
        if p[0] != "":
            d = self._num(p[0], "CG diameter")
            if d <= 0:
                raise ValueError("CG diameter must be positive")
            if p[1] == "" or p[2] == "":
                raise ValueError("CG with a diameter needs an explicit end point")
            cur = self.pos
            if cur is not None and math.hypot(float(p[1]) - cur[0], float(p[2]) - cur[1]) < 1e-9:
                raise ValueError("CG with a diameter cannot describe a full circle")
        else:
            if (p[3] == "" or p[4] == "") and self.ij is None:
                raise ValueError("CG without a diameter or I/J offsets and no earlier I/J to inherit")
            if p[1] == "" and p[2] == "":
                # the centre is the current position plus I/J, so the position must be an aligned one
                self._need_pos("this CG is a full circle around the current position")
            i = self._num(p[3], "I") if p[3] != "" else self.ij[0]
            j = self._num(p[4], "J") if p[4] != "" else self.ij[1]
            self.ij = (i, j)
            ri, rj = self.fit.vec(i, j)
            p[3], p[4] = fmt(ri), fmt(rj)
        r = self._apply(self._opt(p[1], "X end"), self._opt(p[2], "Y end"))
        if r is not None:
            p[1], p[2] = r
        self.stats.arcs += 1
        if self.pos is not None:
            self.stats.cut_points.append(self.pos)
        while len(p) > 5 and p[-1] == "":
            p.pop()
        return self._join(orig_cmd, p)


def detect_units_comment(lines: Sequence[str]) -> Optional[int]:
    for ln in lines[:60]:
        u = ln.upper().replace(" ", "")
        if "UNITS:INCH" in u or "FILEININCHES" in u:
            return 0
        if "UNITS:MM" in u or "FILEINMM" in u or "UNITS:MILLIMET" in u:
            return 1
    return None


# --------------------------------------------------------------------------------------
# output file assembly
# --------------------------------------------------------------------------------------
def build_output(body: List[str], pd: ProbeData, fit: Fit, job_path: str,
                 offset: Optional[Tuple[float, float]], th: Dict[str, float], summary: str,
                 probed_at: str = "", confirm: Optional[str] = None) -> List[str]:
    deg = math.degrees(fit.theta)
    tol = th["zero_check_tol"] * pd.scale
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    head = [
        f"'==== {MARKER} - vd_align.py v{VERSION} ====",
        f"'Source job   : {job_path}",
        f"'Probe data   : {pd.date}   units {pd.unit_name}" + ("   ** SIMULATED **" if pd.sim else ""),
        f"'Aligned      : {now}",
        f"'Rotation     : {deg:+.5f} deg (counter-clockwise positive)",
        f"'Translation  : X {fit.tx:+.5f}  Y {fit.ty:+.5f}",
    ]
    head += ["'" + ln for ln in summary.splitlines()]
    head += [
        "'Do NOT re-zero X or Y after probing. Zero Z with the cutter before running.",
        "'" + "=" * 70,
        "SA",
    ]
    if pd.sim:
        # %(22) = 1 is PREVIEW mode in SB3; a simulated alignment must never cut
        head += [
            "IF %(22) = 1 THEN GOTO VDALIGN_SIMOK",
            "MSGBOX(This file was aligned to SIMULATED probe data. It only runs in PREVIEW mode.,16,"
            "Virtual Dowel - SIMULATION)",
            "END",
            "VDALIGN_SIMOK:",
        ]
    head.append(f"GOSUB {CHECK_LABEL}")
    info = msg_safe(f"rotation {deg:+.4f} deg - probed {probed_at}") if probed_at else \
        msg_safe(f"rotation {deg:+.4f} deg")
    if pd.sim:
        head.append(f"MSGBOX(SIMULATED alignment - {info}. Preview only.,OKCancel,Virtual Dowel)")
    else:
        head.append(f"MSGBOX({msg_safe(confirm.format(info=info))},OKCancel,Virtual Dowel)" if confirm else
                    f"MSGBOX(Virtual Dowel aligned file - {info}. Probe removed and cutter installed - "
                    "Z zeroed with the cutter - X and Y NOT re-zeroed since probing?,OKCancel,Virtual Dowel)")
    head.append('IF &msganswer = "Cancel" THEN GOTO VDALIGN_QUIT')

    tail = ["", "END", "'==== Virtual Dowel runtime checks ====", f"{CHECK_LABEL}:",
            f"IF %(25) = {1 - pd.units} THEN GOTO VDALIGN_BADUNITS"]
    if not pd.sim:
        # %(22) was recorded while the holes were really probed, i.e. in MOVE mode. The zero checks
        # below only make sense in MOVE mode (Preview tracks locations differently), so they run
        # whenever the mode equals that recorded value.
        tail += [f"IF %(22) = {pd.mode} THEN GOTO VDALIGN_CHECK_MOVE", "RETURN", "VDALIGN_CHECK_MOVE:"]
        if th.get("check_file_offset", 1):
            tail += [
                "IF %(66) > 1.00001 THEN GOTO VDALIGN_BADFILE",
                "IF %(66) < 0.99999 THEN GOTO VDALIGN_BADFILE",
                "IF %(67) > 1.00001 THEN GOTO VDALIGN_BADFILE",
                "IF %(67) < 0.99999 THEN GOTO VDALIGN_BADFILE",
                f"IF %(61) > {tol:.5f} THEN GOTO VDALIGN_BADFILE",
                f"IF %(61) < {-tol:.5f} THEN GOTO VDALIGN_BADFILE",
                f"IF %(62) > {tol:.5f} THEN GOTO VDALIGN_BADFILE",
                f"IF %(62) < {-tol:.5f} THEN GOTO VDALIGN_BADFILE",
            ]
        if offset is not None:
            # %(6)/%(7) = table position of the X/Y zero; they change only when X or Y is zeroed
            ox, oy = offset
            tail += [
                f"IF %(6) > {ox + tol:.5f} THEN GOTO VDALIGN_BADZERO",
                f"IF %(6) < {ox - tol:.5f} THEN GOTO VDALIGN_BADZERO",
                f"IF %(7) > {oy + tol:.5f} THEN GOTO VDALIGN_BADZERO",
                f"IF %(7) < {oy - tol:.5f} THEN GOTO VDALIGN_BADZERO",
            ]
    tail += [
        "RETURN",
        "VDALIGN_BADZERO:",
        "MSGBOX(X or Y zero has changed since the holes were probed. This aligned file is no longer "
        "valid - probe the holes again. Stopping.,16,Virtual Dowel)",
        "ENDALL",
        "VDALIGN_BADFILE:",
        "MSGBOX(A file offset or X/Y proportion is set in the FP settings. Run this file with no offset "
        "and proportions of 1. Stopping.,16,Virtual Dowel)",
        "ENDALL",
        "VDALIGN_BADUNITS:",
        "MSGBOX(Units changed since the holes were probed. Stopping.,16,Virtual Dowel)",
        "ENDALL",
        "VDALIGN_QUIT:",
        "END",
    ]
    return head + body + tail


# --------------------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------------------
def make_report(pd: ProbeData, holes: Sequence[HoleResult], design: Sequence[DesignHole],
                al: Optional[Alignment], offset, spread, fails: List[str], warns: List[str],
                job: str, out: Optional[str], stats: Optional[TransformStats],
                notes: Sequence[str] = ()) -> str:
    u = pd.unit_name
    L: List[str] = []
    status = "FAIL" if fails else "PASS"
    L.append(f"VIRTUAL DOWEL ALIGNMENT  -  {status}")
    L.append(f"vd_align.py v{VERSION}   {datetime.datetime.now():%Y-%m-%d %H:%M}")
    L.append(f"Probe data : {pd.path}  (probed {pd.date}; units {u}; {pd.passes} fine pass(es)"
             f"{'; spindle 0/180' if pd.spin180 else ''})" + ("  ** SIMULATED **" if pd.sim else ""))
    L.append(f"Job file   : {job}")
    L.append(f"Output     : {out if out else '(none written)'}")
    if fails:
        L.append("")
        L.append("FAILED CHECKS:")
        L += ["  - " + f for f in fails]
    if warns:
        L.append("")
        L.append("WARNINGS:")
        L += ["  - " + w for w in warns]
    if al is None:
        return "\n".join(L)
    L.append("")
    L.append("Hole  design X     design Y    design D   measured X   measured Y   meas D   resid   repeat")
    fit = al.fit
    for i, d in enumerate(design):
        h = holes[al.perm[i]]
        rx, ry = fit.residuals[i]
        dd = f"{d[2]:.4f}" if d[2] is not None else "   -  "
        L.append(f"{i + 1:>4}  {d[0]:>11.4f}  {d[1]:>11.4f}  {dd:>8}  {h.center[0]:>11.4f}  {h.center[1]:>11.4f}  "
                 f"{h.dia:>7.4f}  {math.hypot(rx, ry):>6.4f}  {h.repeat:>6.4f}")
    L.append("")
    for i, j, dd, err in al.spacing:
        L.append(f"Spacing hole {i + 1}-{j + 1}: design {dd:.4f}  measured {dd + err:.4f}  error {err:+.4f} {u}")
    deg = math.degrees(fit.theta)
    L.append(f"Rotation   : {deg:+.4f} deg (CCW+)  = {math.tan(fit.theta) * 10:+.4f} {u} per 10 {u}")
    L.append(f"Translation: X {fit.tx:+.4f}  Y {fit.ty:+.4f} {u}  (design origin -> machine working coords)")
    L.append(f"Probe size bias: {al.size_bias:+.4f} {u} (measured diameters minus design; ~2x probe pretravel)")
    L += list(notes)
    if pd.spin180:
        e = [h.ecc for h in holes if h.ecc]
        ex = _mean([a for a, _ in e]); ey = _mean([b for _, b in e])
        L.append(f"Probe tip offset from spindle axis: X {ex:+.4f} Y {ey:+.4f} {u} (cancelled by 0/180 averaging)")
    else:
        L.append("Probe tip offset from spindle axis: not measured (vd_spin180 = 0). Any stylus runout "
                 "shifts the whole job by that amount.")
    if offset is not None:
        L.append(f"X/Y zero at probing (table position %(6)/%(7)): X {offset[0]:+.4f}  Y {offset[1]:+.4f} "
                 f"(spread {max(spread):.5f})")
    if stats:
        L.append(f"Transformed: {stats.moves} moves, {stats.arcs} arcs, {stats.checks} zero checks inserted, "
                 f"{stats.lines} lines")
        if stats.custom_cuts:
            L.append("Custom cuts in the job (run as-is, zero re-checked after each): "
                     + "; ".join(sorted(set(stats.custom_cuts))))
        if stats.xmin <= stats.xmax:
            L.append(f"Aligned extents: X {stats.xmin:.3f} .. {stats.xmax:.3f}   Y {stats.ymin:.3f} .. {stats.ymax:.3f}")
    return "\n".join(L)


# --------------------------------------------------------------------------------------
# main pipeline
# --------------------------------------------------------------------------------------
def load_settings() -> Dict[str, float]:
    th = dict(DEFAULTS)
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            user = json.load(fh)
        for k, v in user.items():
            if k in th:
                th[k] = float(v)
    return th


def read_text(path: str) -> Tuple[List[str], str]:
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("latin-1")
    # A bare CR (old Mac) or a mix of CRLF and bare LF could make ShopBot see line breaks this
    # script does not (or the reverse) - refuse rather than guess.
    if re.search(r"\r(?!\n)", text):
        raise VDError("the job file contains bare CR line breaks (old Mac format) - re-save it with "
                      "Windows (CRLF) line endings")
    if "\r\n" in text and re.search(r"(?<!\r)\n", text):
        raise VDError("the job file mixes CRLF and LF line endings - re-save it with Windows (CRLF) endings")
    nl = "\r\n" if "\r\n" in text else "\n"
    return split_lines(text), nl


def sidecar_path(job: str) -> str:
    return job + ".vdholes.txt"


def parse_hole(s: str) -> DesignHole:
    parts = [p for p in re.split(r"[,\s;]+", s.strip()) if p]
    if len(parts) not in (2, 3):
        raise VDError(f"expected 'X, Y' or 'X, Y, diameter' but got {s!r}")
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        raise VDError(f"expected numbers in 'X, Y, diameter' but got {s!r}")
    if len(vals) == 3 and vals[2] <= 0:
        raise VDError("hole diameter must be positive")
    return (vals[0], vals[1], vals[2] if len(vals) == 3 else None)


Box = Tuple[float, float, float, float]      # xmin, ymin, xmax, ymax


def parse_stock(s: str) -> Box:
    """'W, H' (origin at the stock's lower-left corner) or 'xmin, ymin, xmax, ymax'."""
    parts = [p for p in re.split(r"[,\s;]+", s.strip()) if p]
    try:
        v = [float(p) for p in parts]
    except ValueError:
        raise VDError(f"expected numbers for the stock size but got {s!r}")
    if len(v) == 2:
        v = [0.0, 0.0, v[0], v[1]]
    if len(v) != 4 or not (v[2] > v[0] and v[3] > v[1]):
        raise VDError("stock size must be 'X size, Y size' or 'xmin, ymin, xmax, ymax' with max > min")
    return (v[0], v[1], v[2], v[3])


def read_sidecar_full(job: str) -> Tuple[Optional[List[DesignHole]], Optional[Box]]:
    p = sidecar_path(job)
    if not os.path.exists(p):
        return None, None
    pts: List[DesignHole] = []
    stock: Optional[Box] = None
    with open(p, "r", encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.split("#", 1)[0].strip()
            if not ln:
                continue
            key = ""
            if "=" in ln:
                key, ln = ln.split("=", 1)
            if key.strip().lower() == "stock":
                stock = parse_stock(ln)
            else:
                pts.append(parse_hole(ln))
    return (pts or None), stock


def read_sidecar(job: str) -> Optional[List[DesignHole]]:
    return read_sidecar_full(job)[0]


def write_sidecar(job: str, pts: Sequence[DesignHole], stock: Optional[Box] = None) -> None:
    with open(sidecar_path(job), "w", encoding="utf-8") as fh:
        fh.write("# Virtual Dowel reference holes in THIS job file's design coordinates: X, Y, diameter\n")
        for i, (x, y, d) in enumerate(pts, 1):
            fh.write(f"hole{i} = {x:.4f}, {y:.4f}" + (f", {d:.4f}" if d is not None else "") + "\n")
        if stock is not None:
            fh.write("# stock box in this file's coordinates: xmin, ymin, xmax, ymax\n")
            fh.write("stock = " + ", ".join(f"{v:.4f}" for v in stock) + "\n")


def parse_holes_arg(s: str) -> List[DesignHole]:
    return [parse_hole(chunk) for chunk in s.split(";") if chunk.strip()]


def data_age_minutes(path: str) -> float:
    return (time.time() - os.path.getmtime(path)) / 60.0


def default_out_path(job_path: str, sim: bool) -> str:
    stem, _ = os.path.splitext(job_path)
    return stem + ("_VDSIM.sbp" if sim else "_VD.sbp")


@dataclass
class Result:
    ok: bool
    report: str
    out_path: Optional[str]
    fails: List[str] = field(default_factory=list)
    warns: List[str] = field(default_factory=list)
    rotation_deg: Optional[float] = None
    spacing_err: Optional[float] = None       # worst hole-to-hole spacing error
    shift: Optional[Tuple[float, float]] = None


def run_alignment(data_path: str, job_path: str, design: List[DesignHole],
                  out_path: Optional[str] = None, th: Optional[Dict[str, float]] = None,
                  allow_old_data: bool = False, stock: Optional[Box] = None, dry: bool = False) -> Result:
    """dry = check only (before probing, with made-up probe data): nothing is written or deleted."""
    th = th or load_settings()
    with open(data_path, "r", encoding="latin-1", newline="") as fh:
        pd = parse_probe_data(fh.read(), data_path)
    if out_path is None:
        out_path = default_out_path(job_path, pd.sim)
    real = lambda p: os.path.normcase(os.path.realpath(p))   # noqa: E731
    if real(out_path) in (real(job_path), real(data_path)):
        raise VDError("the output file must not be the job file or the probe data file")
    if not dry and os.path.exists(out_path) and not is_aligned_file(out_path):
        raise VDError(f"{out_path} exists and was not written by Virtual Dowel - rename or move it, "
                      "then align again")
    retired: List[str] = []
    if not dry:
        # a simulated run only replaces simulated outputs; real aligned files stay
        if not pd.sim:
            retired = retire_other_runs(data_path)
        # never leave an older aligned file behind when this run fails
        stale_set = {out_path, default_out_path(job_path, True)} | ({default_out_path(job_path, False)} if not pd.sim else set())
        for stale in stale_set:
            if os.path.exists(stale) and is_aligned_file(stale):
                os.remove(stale)

    fails: List[str] = []
    warns: List[str] = []
    age = data_age_minutes(data_path)
    if age > th["max_data_age_min"] and not allow_old_data and not dry:
        fails.append(f"the probe data is {age:.0f} minutes old (limit {th['max_data_age_min']:.0f}) - "
                     "probe the holes again, or confirm that nothing has moved")
    probed_at = datetime.datetime.fromtimestamp(os.path.getmtime(data_path)).strftime("%Y-%m-%d at %H.%M")

    holes, offset, spread = compute_holes(pd)
    if pd.open:
        # half-slot touches run along the machine axes: a turned slot needs the board angle from a first fit
        first = align(design, holes, th, pd.scale, pd.nominal)
        if abs(first.fit.theta) <= math.radians(th["max_rotation_deg"]):
            holes = compute_holes(pd, first.fit.theta)[0]
    f1, w1 = hole_checks(pd, holes, spread, th)
    fails += f1
    warns += w1
    al = align(design, holes, th, pd.scale, pd.nominal)
    fails += al.failures
    warns += al.warnings
    if pd.sim:
        warns.append("SIMULATED probe data - the output only runs in PREVIEW mode")
    elif pd.mode != 0:
        fails.append(f"probe data was recorded with ShopBot mode {pd.mode} (expected 0 = MOVE)")

    lines, nl = read_text(job_path)
    notes: List[str] = []
    if retired:
        notes.append("Removed aligned files made from an earlier probing run (old board position): "
                     + "; ".join(os.path.basename(p) for p in retired))
    box = material_box(lines)
    box_src = "job header" if box is not None else None
    if box is not None:
        notes.append(f"Material box: X {box[0]:g} .. {box[2]:g}   Y {box[1]:g} .. {box[3]:g}  (from the job header)")
        if stock is not None and max(abs(a - b) for a, b in zip(box, stock)) > 0.01 * pd.scale:
            warns.append("the stock size you entered differs from the job header; the header was used")
    elif stock is not None:
        box = stock
        box_src = "stock size you entered"
        notes.append(f"Material box: X {box[0]:g} .. {box[2]:g}   Y {box[1]:g} .. {box[3]:g}  (entered by you)")
    if box is not None:
        tp = toolpath_extents(lines)
        if tp is not None:
            cov = box_coverage(tp, box)
            notes.append(f"Toolpaths (design coords): X {tp[0]:.3f} .. {tp[2]:.3f}   Y {tp[1]:.3f} .. {tp[3]:.3f}"
                         f"  ({cov * 100:.0f}% inside the material box)")
            if cov < 0.5:
                fails.append(f"the {box_src} (X {box[0]:g}..{box[2]:g}, Y {box[1]:g}..{box[3]:g}) does not cover "
                             f"the toolpaths (X {tp[0]:.3f}..{tp[2]:.3f}, Y {tp[1]:.3f}..{tp[3]:.3f}) - check which "
                             "corner the side-B origin is at (e.g. a back-left origin puts the stock at negative Y)")
                box = None                       # an inconsistent box cannot support the checks below
                box_src = "bad"
    if box is not None:
        x0, y0, x1, y1 = box
        e = 0.01 * pd.scale
        outside = [i + 1 for i, d in enumerate(design)
                   if not (x0 - e <= d[0] <= x1 + e and y0 - e <= d[1] <= y1 + e)]
        if outside:
            fails.append(f"hole(s) {outside} lie outside the material in the job file "
                         f"(X {x0:g}..{x1:g}, Y {y0:g}..{y1:g}) - wrong coordinates")
        if not al.failures:
            for name, alt in mirror_alternatives(design, holes, th, pd.scale, pd.nominal, box):
                shift = math.hypot(alt.tx - al.fit.tx, alt.ty - al.fit.ty)
                msg = (f"these holes have different side-A and side-B coordinates ({name} flip), and the "
                       "side-A numbers fit the probe data just as well - if they were typed by mistake the "
                       f"job would be shifted by {shift:.3f} {pd.unit_name}. Put the holes on the flip axis "
                       "(material centerline) or use 3 holes that are not in a line")
                if shift < th["mirror_shift_warn_only"] * pd.scale:
                    warns.append(msg)
                elif th.get("allow_offaxis_ambiguity", 0):
                    warns.append(msg + " (allowed by settings - double-check the coordinates)")
                else:
                    fails.append(msg + "; or set allow_offaxis_ambiguity to 1 in vd_settings.json")
    elif box_src != "bad" and has_mirror_symmetry(design, th["symmetry_tol"] * pd.scale,
                                                  4 * th["diameter_rel_tol"] * pd.scale):
        msg = ("the job header has no material size (Fusion's ShopBot post does not write it), so the "
               "script cannot tell whether side-A hole coordinates were typed instead of side-B ones - and "
               "with this hole layout both would fit. Enter the stock size when asked (or --stock), or use "
               "3 holes that are not in a line")
        if th.get("allow_offaxis_ambiguity", 0):
            warns.append(msg + " (allowed by settings - double-check the coordinates)")
        else:
            fails.append(msg + "; or set allow_offaxis_ambiguity to 1 in vd_settings.json")

    if pd.open and box_src != "bad":
        if box is None:
            fails.append("half slots need the stock box (enter it for files without a stock size): it tells "
                         "which edge each slot opens to")
        else:
            for i, d in enumerate(design):
                got = holes[al.perm[i]].open
                want = open_side(d[0], d[1], box)
                if got is not None and got != want:
                    fails.append(f"slot {i + 1} was probed as opening toward {SIDE_NAMES[got]}, but in the design it "
                                 f"opens toward {SIDE_NAMES[want]} (its nearest stock edge)")

    stats: Optional[TransformStats] = None
    data = None
    if not fails:
        tr = SbpTransformer(al.fit)
        body = None
        try:
            body = tr.transform(lines)
        except TransformError as e:
            fails.append(f"job file not supported - {e}")
        stats = tr.stats
        warns += stats.warnings
        if body is not None and stats.moves + stats.arcs == 0:
            fails.append("no X/Y moves were found in the job file - nothing to align")
        fu = stats.file_units if stats.file_units is not None else detect_units_comment(lines)
        if fu is not None and fu != pd.units:
            fails.append(f"job file is in {'mm' if fu else 'inches'} but the probe data is in {pd.unit_name}")
        elif fu is None:
            warns.append("could not detect the job file's units - make sure they match the probe data")
        if body is not None and not fails:
            use_offset = offset if max(spread) <= th["offset_consistency"] * pd.scale else None
            summary = "\n".join(
                f"Hole {i + 1}: design {d[0]:.4f},{d[1]:.4f} -> measured "
                f"{holes[al.perm[i]].center[0]:.4f},{holes[al.perm[i]].center[1]:.4f}"
                for i, d in enumerate(design))
            out_lines = build_output(body, pd, al.fit, job_path, use_offset, th, summary, probed_at)
            data = (nl.join(out_lines) + nl).encode("latin-1", errors="replace")

    written = None
    if data is not None and not fails and not dry:
        with open(out_path, "wb") as fh:
            fh.write(data)
        written = out_path
        record_output(written, data_path)
    report = make_report(pd, holes, design, al, offset, spread, fails, warns, job_path, written, stats, notes)
    if not dry:
        rep_path = os.path.splitext(out_path)[0] + "_report.txt"
        try:
            with open(rep_path, "w", encoding="utf-8") as fh:
                fh.write(report + "\n\n---- raw probe data ----\n")
                with open(data_path, "r", encoding="latin-1") as src:
                    fh.write(src.read())
        except OSError:
            pass
    ok = not fails and (data is not None if dry else written is not None)
    return Result(ok=ok, report=report, out_path=written, fails=list(fails), warns=list(warns),
                  rotation_deg=math.degrees(al.fit.theta),
                  spacing_err=max((abs(e) for *_, e in al.spacing), default=0.0),
                  shift=(al.fit.tx, al.fit.ty))


# --------------------------------------------------------------------------------------
# user interface
# --------------------------------------------------------------------------------------
def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(st, fh, indent=1)
    except OSError:
        pass


def _data_id(data_path: str) -> Optional[str]:
    """Fingerprint of one probing run (every run writes new touches and a new start time)."""
    try:
        with open(data_path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        return None


def _recorded_outputs(st: dict) -> List[dict]:
    outs = []
    for o in st.get("outputs", []):
        if isinstance(o, str):                          # written by v1.3 - probe run unknown
            outs.append({"path": o, "data_id": None})
        elif isinstance(o, dict) and isinstance(o.get("path"), str):
            outs.append(o)
    return outs


def record_output(path: str, data_path: Optional[str] = None) -> None:
    path = os.path.abspath(path)
    st = _load_state()
    outs = [o for o in _recorded_outputs(st) if o["path"] != path]
    outs.append({"path": path, "data_id": _data_id(data_path) if data_path else None})
    st["outputs"] = outs[-200:]
    _save_state(st)


def is_aligned_file(path: str) -> bool:
    """True if Virtual Dowel wrote this file: its first line is the aligned-file header (build_output).
    Nothing else is ever deleted or overwritten as an output."""
    try:
        with open(path, "r", encoding="latin-1") as fh:
            return fh.readline().startswith(f"'==== {MARKER}")
    except OSError:
        return False


def _remove_outputs(keep) -> List[str]:
    st = _load_state()
    kept, removed = [], []
    for o in _recorded_outputs(st):
        if keep(o):
            kept.append(o)
            continue
        try:
            if os.path.exists(o["path"]) and is_aligned_file(o["path"]):    # replaced by the user: left alone
                os.remove(o["path"])
                removed.append(o["path"])
        except OSError:
            kept.append(o)                              # still listed - the next run tries again
    st["outputs"] = kept
    _save_state(st)
    return removed


def new_session() -> List[str]:
    """Called by vd_probe.sbp when a new probing run starts: aligned files from earlier runs
    belong to an earlier board position, so remove them before anyone can run them by mistake."""
    return _remove_outputs(lambda o: False)


def retire_other_runs(data_path: str) -> List[str]:
    """The same protection, repeated whenever the alignment runs: remove every aligned file made
    from a DIFFERENT probing run. This covers a launch that ShopBot's SHELL could not start,
    so the cleanup at the start of probing never happened."""
    cur = _data_id(data_path)
    return _remove_outputs(lambda o: cur is not None and o.get("data_id") == cur)


# --------------------------------------------------------------------------------------
# one-time install: point the ShopBot files at the folder they actually live in
# --------------------------------------------------------------------------------------
# ShopBot's SHELL changed the command text on the user's SB3: spaces were put around "/" and
# every "~" was removed (so 8.3 short names like DOWNLO~1 broke). Other operator characters
# probably get the same treatment, so the folder path may only use characters that survive.
SHELL_SAFE_PATH = re.compile(r"^[A-Za-z]:[A-Za-z0-9_.\\]*$")


def _long_path(p: str) -> str:
    """Full Windows names - a folder reached through an 8.3 name like DOWNLO~1 is expanded."""
    if os.name != "nt":
        return p
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetLongPathNameW(p, buf, 1024)
        return buf.value if 0 < n < 1024 else p
    except Exception:
        return p


def shell_safe_path(p: str) -> bool:
    if os.name == "nt" or re.match(r"^[A-Za-z]:", p):
        return bool(SHELL_SAFE_PATH.match(p))
    return bool(re.match(r"^[A-Za-z0-9_./]*$", p))     # POSIX paths: only for the tests


def _runs(exe: str, code: str) -> bool:
    try:
        return subprocess.run([exe, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def shopbot_python(folder: str, make_venv: bool = True) -> Tuple[str, List[str]]:
    """The python.exe that vd_probe.sbp starts with SHELL, and notes for the installer output.
    On the user's SB3, SHELL started cmd.exe but never started vd_align.bat, and it alters "-", "/"
    and "~". So SB3 starts python.exe directly, from a path with plain characters only: a small
    virtual environment inside this folder (its python.exe forwards to the installed Python)."""
    notes: List[str] = []
    cands: List[str] = []
    if make_venv:
        env = os.path.join(folder, "venv")
        exe = os.path.join(env, "Scripts", "python.exe") if os.name == "nt" else os.path.join(env, "bin", "python")
        try:
            import venv
            venv.create(env, with_pip=False, clear=True)
            cands.append(exe)
        except Exception as e:                                  # noqa: BLE001 - report and fall back
            notes.append(f"could not create the venv folder ({e}) - trying the installed Python directly")
    cands += [p for p in (sys.executable, shutil.which("py"), shutil.which("python")) if p]
    for exe in cands:
        if not shell_safe_path(exe):
            notes.append(f"not usable from ShopBot (characters in its path): {exe}")
            continue
        if not _runs(exe, "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"):
            notes.append(f"does not run: {exe}")
            continue
        if not _runs(exe, "import tkinter"):
            notes.append("WARNING: tkinter is missing - the alignment window cannot open. Reinstall Python "
                         "with the Tcl/Tk option.")
        # pythonw.exe is the same Python without the black console window behind the Virtual Dowel window
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.name == "nt" and os.path.isfile(pyw) and shell_safe_path(pyw) and \
                _runs(pyw, "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"):
            return pyw, notes
        return exe, notes
    raise VDError("found no Python that ShopBot can start. " + " / ".join(notes))


CUSTOM_CUT_MARK = "'VIRTUAL DOWEL CUSTOM CUT"
CUSTOM_CUT_NUMBERS = range(80, 90)


def start_file_text(py: str, script: str, run_file: str, custom: bool = False) -> str:
    """vd_start.sbp / the custom cut: open the window (step 1), then run what it prepared (step 2).
    SHELL ... SYNC waits until the window is closed. The window always rewrites vd_run.sbp - with the
    probing routine on Start, or with a stub when closed without starting. vd_run.sbp is emptied first,
    so if Python does not start at all, the FP runs nothing instead of an earlier job's probing run."""
    lines = [CUSTOM_CUT_MARK if custom else "'VIRTUAL DOWEL - vd_start.sbp",
             "'Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.",
             "'Opens the Virtual Dowel window: choose the side-B files - holes - probe settings there.",
             "'Clicking Start in the window writes vd_run.sbp - which ShopBot then runs here.",
             "'Written by vd_install.bat - run it again if the VirtualDowel folder moves.",
             "'Empty vd_run.sbp first - if the window cannot start nothing old runs.",
             f'OPEN "{run_file}" FOR OUTPUT AS #1',
             "CLOSE #1",
             f'&vd_pyexe = "{py}"',
             f'&vd_script = "{script}"',
             '&vd_cmd = &vd_pyexe & " " & &vd_script & " prepare"',
             "SHELL, &vd_cmd, 1, SYNC",
             f"FP,{run_file}"]
    return "\r\n".join(lines) + "\r\n"


def install_custom_cut(text: str, custom_dir: Optional[str]) -> str:
    """Put the start file into ShopBot's custom cut folder as Custom80.sbc (or the next free number
    up to 89). Never overwrites a custom cut that is not ours. Returns a line for the installer output."""
    if not custom_dir or not os.path.isdir(custom_dir):
        return (f"custom cut not installed: ShopBot's folder {custom_dir or '(none)'} was not found - start with "
                "FP vd_start.sbp instead")
    def ours(path):
        try:
            with open(path, "r", encoding="latin-1") as fh:
                return fh.readline().strip() == CUSTOM_CUT_MARK
        except OSError:
            return False

    mine = [n for n in CUSTOM_CUT_NUMBERS if os.path.exists(os.path.join(custom_dir, f"Custom{n}.sbc"))
            and ours(os.path.join(custom_dir, f"Custom{n}.sbc"))]
    order = mine[:1] + [n for n in CUSTOM_CUT_NUMBERS if n not in mine[:1]]   # keep the number in use
    for n in order:
        path = os.path.join(custom_dir, f"Custom{n}.sbc")
        if os.path.exists(path) and not ours(path):
            continue
        try:
            with open(path, "w", encoding="latin-1", newline="") as fh:
                fh.write(text)
        except OSError as e:
            return f"custom cut not installed ({e}) - start with FP vd_start.sbp instead"
        for m in CUSTOM_CUT_NUMBERS:              # an older copy under another number is removed
            old = os.path.join(custom_dir, f"Custom{m}.sbc")
            if m != n and os.path.exists(old):
                try:
                    with open(old, "r", encoding="latin-1") as fh:
                        if fh.readline().strip() == CUSTOM_CUT_MARK:
                            os.remove(old)
                except OSError:
                    pass
        return f"custom cut C{n}: type C{n} in ShopBot to start Virtual Dowel ({path})"
    return (f"custom cut not installed: Custom80 to Custom89 are all used by other files in {custom_dir} - "
            "start with FP vd_start.sbp instead")


def install_paths(folder: str = HERE, make_venv: bool = True, custom_dir: Optional[str] = None) -> List[str]:
    """Rewrite the path lines of vd_probe.sbp and vd_selftest.sbp so they point at `folder`, write
    vd_start.sbp, an empty vd_run.sbp and the custom cut. Returns a list of human-readable changes.
    Raises VDError when the path cannot work in SB3."""
    if custom_dir is None and os.name == "nt":
        custom_dir = r"C:\SbParts\Custom"
    d = _long_path(os.path.abspath(folder)).rstrip("\\/")
    if not shell_safe_path(d):
        raise VDError(f"the folder path {d} contains a space or a character other than letters, digits, "
                      "_ and . - ShopBot changes those when it starts the script. Move the VirtualDowel "
                      "folder to C:\\SbParts\\VirtualDowel and run vd_install.bat again")
    sep = "\\" if "\\" in d or os.name == "nt" else "/"
    j = lambda name: d + sep + name   # noqa: E731
    py, notes = shopbot_python(d, make_venv)
    plan = {
        "vd_probe.sbp": [
            (r"^&vd_out\s*=.*$", f'&vd_out = "{j("vd_probe_data.csv")}"'),
            (r"^&vd_pyexe\s*=.*$", f'&vd_pyexe = "{py}"'),
            (r"^&vd_script\s*=.*$", f'&vd_script = "{j("vd_align.py")}"'),
        ],
        "vd_selftest.sbp": [
            (r'^OPEN "[^"]*vd_selftest_literal\.txt" FOR OUTPUT AS #2\s*$',
             f'OPEN "{j("vd_selftest_literal.txt")}" FOR OUTPUT AS #2'),
            (r"^&st_out\s*=.*$", f'&st_out = "{j("vd_selftest_variable.txt")}"'),
            (r"^&st_pyexe\s*=.*$", f'&st_pyexe = "{py}"'),
            (r"^&st_script\s*=.*$", f'&st_script = "{j("vd_align.py")}"'),
        ],
    }
    done = []
    for fname, edits in plan.items():
        path = os.path.join(folder, fname)
        with open(path, "rb") as fh:
            text = fh.read().decode("latin-1")
        nl = "\r\n" if "\r\n" in text else "\n"
        lines = split_lines(text)
        for pat, new in edits:
            hits = [i for i, ln in enumerate(lines) if re.match(pat, ln)]
            if len(hits) != 1:
                raise VDError(f"{fname}: expected exactly one line matching {pat!r}, found {len(hits)}")
            lines[hits[0]] = new
            done.append(f"{fname}: {new}")
        with open(path, "wb") as fh:
            fh.write((nl.join(lines) + nl).encode("latin-1"))
    start = start_file_text(py, j("vd_align.py"), j("vd_run.sbp"))
    with open(os.path.join(folder, "vd_start.sbp"), "w", encoding="latin-1", newline="") as fh:
        fh.write(start)
    done.append(f"vd_start.sbp: opens the window, then runs {j('vd_run.sbp')}")
    write_run_stub(path=os.path.join(folder, "vd_run.sbp"))
    done.append(install_custom_cut(start_file_text(py, j("vd_align.py"), j("vd_run.sbp"), custom=True),
                                   custom_dir))
    return done + notes


# --------------------------------------------------------------------------------------
# probe settings, the generated probing file and the session (Virtual Dowel window, step 1)
# --------------------------------------------------------------------------------------
# key: (type, default, min, max, label) - each key is the SBP variable &vd_<key> in vd_probe.sbp.
# Distances in inches, speeds in in/s; the routine converts them when SB3 is set to mm.
PROBE_FIELDS: Dict[str, Tuple[str, float, float, float, str]] = {
    "pin":     ("int",   1,     1,     8,   "Probe input"),
    "trig":    ("int",   0,     0,     1,   "Input reads when deflected"),
    "tip":     ("float", 0.125, 0.01,  0.5, "Ball diameter"),
    "passes":  ("int",   2,     1,     20,  "Fine passes per hole"),
    "spin180": ("bool",  0,     0,     1,   "Turn the spindle 180 deg per hole"),
    "test":    ("bool",  1,     0,     1,   "Finger test before moving"),
    "c7":      ("bool",  1,     0,     1,   "Run C7 (spindle off) first"),
    "depth":   ("float", 0.30,  0.05,  2.0, "Lower the tip below the park height by"),
    "margin":  ("float", 0.08,  0.01,  0.5, "Search past the expected wall by"),
    "backoff": ("float", 0.03,  0.005, 0.2, "Back off before the slow touch by"),
    "fast":    ("float", 0.40,  0.05,  2.0, "Fast speed (in/s)"),
    "slow":    ("float", 0.08,  0.01,  1.0, "Slow touch speed (in/s)"),
    "zspd":    ("float", 0.30,  0.05,  2.0, "Z speed (in/s)"),
}
BASIC_FIELDS = ("pin", "trig", "tip", "passes", "spin180", "test")


def _sbp_value_lines(text: str) -> Dict[str, str]:
    """First assignment of every &vd_<name> in the settings part of an SBP file."""
    out: Dict[str, str] = {}
    for ln in split_lines(text):
        m = re.match(r"^&vd_(\w+)\s*=\s*([^'\r\n]*?)\s*(?:'.*)?$", ln)
        if m and m.group(1).lower() not in out:
            out[m.group(1).lower()] = m.group(2).strip()
    return out


def load_machine(template: str = None, machine_file: str = None) -> Dict[str, float]:
    """Probe settings: the values in vd_probe.sbp (so earlier hand edits carry over), then the ones
    saved by the window. Unknown or out-of-range values fall back to the defaults."""
    template = template or TEMPLATE_FILE
    machine_file = machine_file or MACHINE_FILE
    vals: Dict[str, float] = {k: f[1] for k, f in PROBE_FIELDS.items()}
    try:
        with open(template, "r", encoding="latin-1", newline="") as fh:
            in_file = _sbp_value_lines(fh.read())
        for k in PROBE_FIELDS:
            if k in in_file:
                try:
                    vals[k] = float(in_file[k])
                except ValueError:
                    pass
    except OSError:
        pass
    try:
        with open(machine_file, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        for k, v in saved.get("probe", {}).items():
            if k in PROBE_FIELDS:
                vals[k] = float(v)
    except (OSError, ValueError, AttributeError):
        pass
    for k, (kind, default, lo, hi, _) in PROBE_FIELDS.items():
        v = vals[k]
        if not (lo <= v <= hi) or (kind != "float" and v != int(v)):
            vals[k] = default
    return vals


def machine_errors(m: Dict[str, float]) -> List[str]:
    errs = []
    for k, (kind, _, lo, hi, label) in PROBE_FIELDS.items():
        v = m.get(k)
        if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
            errs.append(f"{label}: missing")
        elif not lo <= v <= hi:
            errs.append(f"{label}: {v:g} is outside {lo:g} .. {hi:g}")
        elif kind != "float" and v != int(v):
            errs.append(f"{label}: must be a whole number")
    if not errs and m["slow"] > m["fast"]:
        errs.append("the slow touch speed must not be faster than the fast speed")
    return errs


def read_machine_file(machine_file: str = None) -> dict:
    try:
        with open(machine_file or MACHINE_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_machine(m: Dict[str, float], calibration: Optional[dict] = None, machine_file: str = None) -> None:
    path = machine_file or MACHINE_FILE
    d = read_machine_file(path)
    d["probe"] = {k: (int(v) if PROBE_FIELDS[k][0] != "float" else float(v)) for k, v in m.items()
                  if k in PROBE_FIELDS}
    if calibration is not None:
        d["calibration"] = calibration
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)
    sync_template(m)


def sync_template(m: Dict[str, float], template: str = None) -> None:
    """Keep vd_probe.sbp's own settings lines equal to the window's, for anyone who FPs it directly."""
    template = template or TEMPLATE_FILE
    try:
        with open(template, "r", encoding="latin-1", newline="") as fh:
            text = fh.read()
        nl = "\r\n" if "\r\n" in text else "\n"
        body = render_run(text, {k: m[k] for k in PROBE_FIELDS if k in m}).split(nl, 1)[1]   # drop the header
        if body != text:
            with open(template, "w", encoding="latin-1", newline="") as fh:
                fh.write(body)
    except (OSError, VDError):
        pass


def _sbp_num(v: float) -> str:
    return str(int(v)) if float(v) == int(v) else f"{v:.4f}".rstrip("0")


def render_run(template_text: str, values: Dict[str, object], note: str = "") -> str:
    """vd_probe.sbp with the given &vd_<key> values filled in (strings are quoted). Keeps the line
    endings and every comment; refuses keys the template does not have."""
    nl = "\r\n" if "\r\n" in template_text else "\n"
    lines = split_lines(template_text)
    done = set()
    for i, ln in enumerate(lines):
        m = re.match(r"^(&vd_(\w+)[ \t]*=[ \t]*)([^'\r\n]*?)([ \t]*'.*)?$", ln)
        if not m:
            continue
        key = m.group(2).lower()
        if key in values and key not in done:
            v = values[key]
            txt = f'"{v}"' if isinstance(v, str) else _sbp_num(float(v))
            lines[i] = m.group(1) + txt + (m.group(4) or "")
            done.add(key)
    missing = set(values) - done
    if missing:
        raise VDError(f"vd_probe.sbp has no setting line for: {', '.join(sorted(missing))} - "
                      "it is from an older version; copy the new vd_probe.sbp and run vd_install.bat")
    head = ["'==== GENERATED by the Virtual Dowel window " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            + " - do not edit: change the settings in the window ===="]
    if note:
        head.append("' " + note)
    return nl.join(head + lines) + nl


def write_run_stub(message: str = "", path: str = None) -> None:
    """vd_run.sbp when nothing is prepared: the window closed without starting, or could not open."""
    lines = ["'Virtual Dowel - nothing prepared. Type the Virtual Dowel custom cut (C80) to open the window."]
    if message:
        lines.append(f"MSGBOX({msg_safe(message)},64,Virtual Dowel)")
    lines.append("END")
    with open(path or RUN_FILE, "w", encoding="latin-1", newline="") as fh:
        fh.write("\r\n".join(lines) + "\r\n")


def save_session(sess: dict) -> None:
    st = _load_state()
    st["session"] = sess
    _save_state(st)


def load_session() -> dict:
    s = _load_state().get("session")
    return s if isinstance(s, dict) else {}


def _identity_fit() -> Fit:
    return Fit(0.0, 0.0, 0.0, [])


def job_units(job: str) -> Optional[int]:
    """0 = inches, 1 = mm, None = unknown (also when the file cannot be read)."""
    try:
        lines, _ = read_text(job)
        tr = SbpTransformer(_identity_fit())
        tr.transform(lines)
        return tr.stats.file_units if tr.stats.file_units is not None else detect_units_comment(lines)
    except (OSError, VDError):
        return None


def synthetic_probe_text(design: Sequence[DesignHole], tip: float, units: int = 0) -> str:
    """Probe data exactly at the design positions, for checking jobs and holes before probing."""
    nominal = max(d[2] for d in design)
    now = datetime.datetime.now()
    rows = [f"VD,1,{units},1,{tip:.4f},{nominal:.4f},{len(design)},1,0,1,0,1",
            "CFG,0.3,0.08,0.03,0.4,0.08,0.3",
            "DT," + now.strftime("%A %B %d %Y %H:%M:%S")]
    for h, (x, y, d) in enumerate(design, 1):
        r = (d - tip) / 2.0
        for p in (0, 1):
            for ax, sg, tx, ty in ((1, 1, x + r, y), (1, -1, x - r, y), (2, 1, x, y + r), (2, -1, x, y - r)):
                rows.append(f"T,{h},0,{p},{ax},{sg},{tx:.4f},{ty:.4f},-0.2,0,0")
    rows.append(f"END,{len(design)}")
    return "\n".join(rows) + "\n"


def precheck_jobs(jobs: Sequence[str], design: List[DesignHole], stock: Optional[Box], tip: float,
                  th: Optional[Dict[str, float]] = None) -> List[Tuple[str, Result]]:
    """Every check that does not need the probe: run each job through the alignment with made-up
    probe data that matches the design exactly. Catches a wrong hole layout, a stock box that does
    not fit, side-A/side-B ambiguity and job files the script cannot rewrite - before probing."""
    import tempfile
    th = th or load_settings()
    out: List[Tuple[str, Result]] = []
    tmpdir = tempfile.mkdtemp(prefix="vd_check_")
    try:
        for job in jobs:
            u = job_units(job) or 0
            data = os.path.join(tmpdir, "check.csv")
            with open(data, "w", encoding="latin-1") as fh:
                fh.write(synthetic_probe_text(design, tip * (25.4 if u else 1.0), u))
            try:
                res = run_alignment(data, job, design, out_path=os.path.join(tmpdir, "out.sbp"), th=th,
                                    stock=stock, dry=True)
                res.warns = [w for w in res.warns if not w.startswith("SIMULATED")]
            except (OSError, VDError) as e:
                res = Result(ok=False, report=str(e), out_path=None, fails=[str(e)])
            out.append((job, res))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return out


def hole_errors(design: Sequence[DesignHole], tip: float, cal: bool = False, scale: float = 1.0) -> List[str]:
    """tip and design in the same units; scale = 25.4 when those are mm."""
    errs = []
    if not cal and not 2 <= len(design) <= 6:
        errs.append("enter 2 to 6 reference holes")
    if any(d[2] is None for d in design):
        errs.append("every hole needs a diameter (the probing distance and the hole check use it)")
    elif design and max(d[2] for d in design) < tip + 0.04 * scale:
        errs.append(f"the largest hole must be at least {0.04 * scale:g} larger than the probe ball ({tip:.4g})")
    return errs


@dataclass
class Prepared:
    ok: bool
    errors: List[str]
    checks: List[Tuple[str, Result]]
    run_path: Optional[str] = None


def prepare_probing(jobs: Sequence[str], design: List[DesignHole], stock: Optional[Box],
                    machine: Dict[str, float], sim: bool = False, template: str = None,
                    run_path: str = None) -> Prepared:
    """Step 1 of a job: check everything that can be checked without the probe, then write
    vd_run.sbp (the probing routine with these settings) for ShopBot to run next."""
    errs = machine_errors(machine)
    if not 2 <= len(design) <= 6:
        errs.append("enter 2 to 6 reference holes")
    if any(d[2] is None for d in design):
        errs.append("every hole needs a diameter (the probing distance and the hole check use it)")
    if not jobs:
        errs.append("add the side-B job files")
    missing = [j for j in jobs if not os.path.isfile(j)]
    if missing:
        errs.append("file not found: " + "; ".join(missing))
    if errs:
        return Prepared(False, errs, [])
    units = {job_units(j) for j in jobs} - {None}
    if len(units) > 1:
        return Prepared(False, ["the side-B files mix inches and mm - align the inch and mm files separately"], [])
    to_in = 1 / 25.4 if units == {1} else 1.0          # the routine's settings are in inches
    errs = hole_errors(design, machine["tip"] / to_in, scale=1 / to_in)
    if errs:
        return Prepared(False, errs, [])
    checks = precheck_jobs(jobs, design, stock, machine["tip"])
    if not all(r.ok for _, r in checks):
        return Prepared(False, ["fix the problems listed for the files marked FAIL, or remove those files"], checks)
    with open(template or TEMPLATE_FILE, "r", encoding="latin-1", newline="") as fh:
        tpl = fh.read()
    vals: Dict[str, object] = {k: machine[k] for k in PROBE_FIELDS}
    vals.update(nholes=len(design), hole=round(max(d[2] for d in design) * to_in, 5), sim=int(sim), cal=0,
                calref=0, detect=0, launched=1, after="align")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d at %H.%M")
    text = render_run(tpl, vals, note=f"probing {len(design)} holes for: " +
                      ", ".join(os.path.basename(j) for j in jobs))
    # the first message names this preparation, so an old vd_run.sbp is recognisable
    text = text.replace("MSGBOX(VIRTUAL DOWEL probing. ",
                        f"MSGBOX(VIRTUAL DOWEL probing - prepared {stamp} for {len(design)} holes. ", 1)
    # SB3 files are Latin-1. Encode before anything is deleted, so a file name in the note line
    # (e.g. with an en dash) cannot stop Start half way.
    data = text.encode("latin-1", errors="replace")
    for j in jobs:
        write_sidecar(j, design, stock)
    save_machine(machine)
    if not sim:
        new_session()                    # aligned files from an earlier board position go now
    try:
        os.remove(DEFAULT_DATA)          # probe data of the earlier board position is no longer valid
    except OSError:
        pass
    save_session({"kind": "probe", "jobs": list(jobs), "holes": [list(d) for d in design],
                  "stock": list(stock) if stock else None, "sim": bool(sim), "prepared": time.time()})
    path = run_path or RUN_FILE
    with open(path, "wb") as fh:
        fh.write(data)
    return Prepared(True, [], checks, path)


def prepare_calibration(machine: Dict[str, float], ref_dia: float, passes: int = 10, spin180: bool = True,
                        detect: bool = True, sim: bool = False, template: str = None,
                        run_path: str = None) -> Prepared:
    """A calibration run: one reference hole of known size, many passes, optionally both spindle
    orientations and automatic detection of the probe input. Never touches the job data or files."""
    m = dict(machine)
    m["passes"] = passes
    m["spin180"] = int(bool(spin180))
    errs = machine_errors(m)
    if not ref_dia or ref_dia <= 0:
        errs.append("enter the measured diameter of the reference hole")
    else:
        errs += hole_errors([(0.0, 0.0, ref_dia)], m["tip"], cal=True)
    if errs:
        return Prepared(False, errs, [])
    with open(template or TEMPLATE_FILE, "r", encoding="latin-1", newline="") as fh:
        tpl = fh.read()
    cur = _sbp_value_lines(tpl).get("out", "").strip('"')
    sep = "\\" if "\\" in cur else "/"
    cal_out = (cur.rsplit(sep, 1)[0] + sep + os.path.basename(CAL_DATA)) if sep in cur else CAL_DATA
    vals: Dict[str, object] = {k: m[k] for k in PROBE_FIELDS}
    vals.update(nholes=1, hole=ref_dia, sim=int(sim), cal=1, calref=ref_dia, detect=int(bool(detect) and not sim),
                launched=1, after="calresult", out=cal_out)
    if detect:
        vals["test"] = 0                 # holding the tip over to find the input is the test
    text = render_run(tpl, vals, note=f"probe calibration on a {ref_dia:g} reference hole")
    prev = load_session()
    keep = prev if prev.get("kind") == "probe" else prev.get("keep")
    save_session({"kind": "cal", "ref": ref_dia, "sim": bool(sim), "prepared": time.time(), "keep": keep})
    path = run_path or RUN_FILE
    with open(path, "w", encoding="latin-1", newline="") as fh:
        fh.write(text)
    return Prepared(True, [], [], path)


# --------------------------------------------------------------------------------------
# probe calibration results
# --------------------------------------------------------------------------------------
@dataclass
class Calibration:
    ref: float
    passes: int
    repeat: float                       # worst distance of one pass's centre from the mean centre
    eff_tip: float                      # ball diameter as the probe "sees" it (incl. trigger travel)
    nominal_tip: float
    roundness: float                    # |measured X diameter - Y diameter|
    ecc: Optional[Tuple[float, float]]  # stylus offset from the spindle axis (spin-180 runs)
    detected: Optional[Tuple[int, int]]
    sim: bool
    units: str
    advice: List[str]

    def as_record(self) -> dict:
        return {"date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "ref": self.ref,
                "passes": self.passes, "repeat": round(self.repeat, 5), "eff_tip": round(self.eff_tip, 5),
                "ecc": [round(v, 5) for v in self.ecc] if self.ecc else None,
                "detected": list(self.detected) if self.detected else None}


def analyze_calibration(pd: ProbeData) -> Calibration:
    if pd.cal_ref is None:
        raise VDError("this probe data is not from a calibration run")
    holes, _, _ = compute_holes(pd)
    h = holes[0]
    span = h.dia - pd.tip                       # wall-to-wall travel of the ball centre
    eff = pd.cal_ref - span
    s = pd.scale
    adv: List[str] = []
    if h.repeat <= 0.001 * s:
        adv.append("Repeatability is good.")
    elif h.repeat <= 0.002 * s:
        adv.append("Repeatability is acceptable. Use 2 or more fine passes for jobs.")
    else:
        adv.append("Repeatability is poor - check that the probe is tight in the collet, slow the touch "
                   "speed, and that the hole wall is clean.")
    if h.repeat > 0.002 * s or abs(h.dia_x - h.dia_y) > 0.005 * s:
        adv.append("The effective ball is not saved from this run - repeatability or roundness is too poor.")
    if not 0.5 * pd.tip <= eff <= 1.2 * pd.tip:
        adv.append(f"The effective ball diameter ({eff:.4f}) is far from the setting ({pd.tip:g}) - check the "
                   "reference diameter you entered.")
    if h.ecc is not None:
        e = math.hypot(*h.ecc)
        if e <= 0.001 * s:
            adv.append("The ball sits on the spindle axis - spin-180 is not needed for jobs.")
        else:
            adv.append(f"The ball is {e:.4f} off the spindle axis. Without spin-180 every job is shifted by "
                       "about that much. Turn on 'Turn the spindle 180 deg per hole', or re-seat / straighten "
                       "the probe and calibrate again.")
    else:
        adv.append("The ball offset from the spindle axis was not measured (spin-180 was off).")
    if pd.sim:
        adv.insert(0, "SIMULATED calibration - the numbers only show that the steps work.")
    return Calibration(ref=pd.cal_ref, passes=pd.passes, repeat=h.repeat, eff_tip=eff, nominal_tip=pd.tip,
                       roundness=abs(h.dia_x - h.dia_y), ecc=h.ecc, detected=pd.detected, sim=pd.sim,
                       units=pd.unit_name, advice=adv)


def calibration_report(c: Calibration) -> str:
    u = c.units
    L = [f"PROBE CALIBRATION  -  reference hole {c.ref:g} {u}, {c.passes} passes"
         + ("  ** SIMULATED **" if c.sim else ""), ""]
    if c.detected:
        L.append(f"Probe input        : {c.detected[0]}   (reads {c.detected[1]} when deflected)")
    L.append(f"Repeatability      : {c.repeat:.4f} {u}   (worst pass centre from the mean)")
    L.append(f"Effective ball     : {c.eff_tip:.4f} {u}   (setting was {c.nominal_tip:g})")
    L.append(f"Roundness X vs Y   : {c.roundness:.4f} {u}")
    if c.ecc:
        L.append(f"Ball off spindle   : {math.hypot(*c.ecc):.4f} {u}   (X {c.ecc[0]:+.4f}  Y {c.ecc[1]:+.4f})")
    L += [""] + c.advice
    return "\n".join(L)


def calibration_usable(c: Calibration) -> Tuple[bool, bool]:
    """(input usable, effective ball usable). Nothing from a simulated run; the ball only when the
    measurement is consistent - a poor run must not change the settings."""
    if c.sim:
        return False, False
    s = 25.4 if c.units == "mm" else 1.0
    ball_ok = (0.5 * c.nominal_tip <= c.eff_tip <= 1.2 * c.nominal_tip and c.repeat <= 0.002 * s
               and c.roundness <= 0.005 * s)
    return bool(c.detected), ball_ok


def apply_calibration(machine: Dict[str, float], c: Calibration) -> Dict[str, float]:
    m = dict(machine)
    use_input, use_ball = calibration_usable(c)
    if use_input:
        m["pin"], m["trig"] = c.detected
    if use_ball:
        m["tip"] = round(c.eff_tip / (25.4 if c.units == "mm" else 1.0), 4)    # settings are in inches
    return m


# --------------------------------------------------------------------------------------
# several side-B files from one probing run (one file per tool)
# --------------------------------------------------------------------------------------
def batch_defaults(jobs: Sequence[str], nholes: Optional[int]) -> Tuple[Optional[List[DesignHole]], Optional[Box], List[str]]:
    """Hole and stock values remembered for any of the selected files, to pre-fill the prompts.
    All side-B files of one job share the same holes, so a disagreement is pointed out."""
    holes: Optional[List[DesignHole]] = None
    stock: Optional[Box] = None
    notes: List[str] = []
    for job in jobs:
        try:
            h, b = read_sidecar_full(job)
        except VDError:
            continue
        if h and (nholes is None or len(h) == nholes):
            if holes is None:
                holes = h
            elif h != holes:
                notes.append(f"{os.path.basename(job)} remembered different hole values - the ones you enter "
                             "now are used for every selected file")
        if b is not None and stock is None:
            stock = b
    return holes, stock, notes


def needs_stock(jobs: Sequence[str]) -> Tuple[bool, Optional[Box]]:
    """True if any selected file lacks a material box (Fusion); also the combined toolpath extents."""
    need, ext = False, None
    for job in jobs:
        try:
            lines, _ = read_text(job)
        except (OSError, VDError):
            continue                                    # reported when that file is aligned
        if material_box(lines) is None:
            need = True
            tp = toolpath_extents(lines)
            if tp:
                ext = tp if ext is None else (min(ext[0], tp[0]), min(ext[1], tp[1]),
                                              max(ext[2], tp[2]), max(ext[3], tp[3]))
    return need, ext


def align_batch(data_path: str, jobs: Sequence[str], design: List[DesignHole], stock: Optional[Box] = None,
                th: Optional[Dict[str, float]] = None, allow_old_data: bool = False) -> List[Tuple[str, Result]]:
    """Align every selected file with the same probe data and hole values. One file failing never
    stops the others; each gets its own _VD file (PASS) or nothing (FAIL) and its own report."""
    th = th or load_settings()
    out: List[Tuple[str, Result]] = []
    for job in sorted(dict.fromkeys(jobs), key=lambda p: os.path.basename(p).lower()):
        try:
            write_sidecar(job, design, stock)
            res = run_alignment(data_path, job, design, th=th, allow_old_data=allow_old_data, stock=stock)
        except (OSError, VDError) as e:
            res = Result(ok=False, report=f"VIRTUAL DOWEL ALIGNMENT  -  FAIL\nJob file   : {job}\n\n"
                                           f"Could not align this file:\n  {e}", out_path=None)
        out.append((job, res))
    return out


@dataclass
class RoughData:
    """Slot openings the operator jogged to with the cutter (half-slot mode, side B, before the bay cut)."""
    units: int
    sim: bool
    mode: int
    date: str
    points: List[Tuple[float, float]]      # in the listed slot order
    base: List[Tuple[float, float]]        # %(6)/%(7) at each jog
    path: str = ""

    @property
    def scale(self) -> float:
        return 25.4 if self.units == 1 else 1.0


def parse_rough_data(text: str, path: str = "") -> RoughData:
    """VR,1,units,sim,n,mode / DT,... / R,i,x,y,bx,by (one per opening) / END,n"""
    header = None
    date = ""
    pts: Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]] = {}
    end = None
    for n, raw in enumerate(split_lines(text), 1):
        s = raw.replace('"', "").strip()
        if not s:
            continue
        tag, _, rest = s.partition(",")
        tag = tag.strip().upper()
        if tag == "DT":
            date = rest.strip()
            continue
        f = rest.split(",") if rest else []
        if tag == "VR":
            if len(f) != 5 or _i(f[0], n) != 1:
                raise ProbeDataError(f"rough locate data line {n}: unsupported header (different Virtual Dowel version?)")
            header = dict(units=_i(f[1], n), sim=_i(f[2], n), n=_i(f[3], n), mode=_i(f[4], n))
        elif tag == "R":
            if header is None or len(f) != 5:
                raise ProbeDataError(f"rough locate data line {n}: bad opening record")
            i = _i(f[0], n)
            if not 1 <= i <= header["n"] or i in pts:
                raise ProbeDataError(f"rough locate data line {n}: opening {i} out of range or repeated")
            pts[i] = ((_f(f[1], n), _f(f[2], n)), (_f(f[3], n), _f(f[4], n)))
        elif tag == "END":
            end = _i(f[0], n) if f else -1
        else:
            raise ProbeDataError(f"rough locate data line {n}: unknown record {tag!r}")
    if header is None or header["units"] not in (0, 1):
        raise ProbeDataError("rough locate data has no valid header - the rough locate was cancelled")
    if end != header["n"] or len(pts) != header["n"]:
        raise ProbeDataError("rough locate data is INCOMPLETE - jog to the slot openings again")
    order = [pts[i] for i in range(1, header["n"] + 1)]
    return RoughData(units=header["units"], sim=bool(header["sim"]), mode=header["mode"], date=date,
                     points=[p for p, _ in order], base=[b for _, b in order], path=path)


def default_rough_path(bay_path: str, sim: bool) -> str:
    stem, _ = os.path.splitext(bay_path)
    return stem + ("_VDROUGHSIM.sbp" if sim else "_VDROUGH.sbp")


def run_rough(data_path: str, bay_path: str, design: List[DesignHole], stock: Optional[Box] = None,
              out_path: Optional[str] = None, th: Optional[Dict[str, float]] = None,
              allow_old_data: bool = False) -> Result:
    """Half-slot mode, side B, before anything can be probed: place the bay cut file (made in Fusion)
    from the slot openings the operator jogged to. Each opening is where a slot axis meets the stock
    edge, so two of them fix the rotation and both shifts. This rough fit is good to about 0.2 in, so it
    is only ever applied to the bay file: a file that cuts away from the slots (a part file) is refused,
    and the output is named _VDROUGH. The probing that follows starts a new session, which deletes it."""
    th = th or load_settings()
    with open(data_path, "r", encoding="latin-1", newline="") as fh:
        rd = parse_rough_data(fh.read(), data_path)
    s = rd.scale
    if out_path is None:
        out_path = default_rough_path(bay_path, rd.sim)
    real = lambda p: os.path.normcase(os.path.realpath(p))   # noqa: E731
    if real(out_path) in (real(bay_path), real(data_path)):
        raise VDError("the output file must not be the bay file or the rough locate data")
    if os.path.exists(out_path) and not is_aligned_file(out_path):
        raise VDError(f"{out_path} exists and was not written by Virtual Dowel - rename or move it, then try again")
    if len(design) != len(rd.points):
        raise VDError(f"{len(rd.points)} slot openings were jogged but {len(design)} slots are listed")
    lines, nl = read_text(bay_path)
    box = material_box(lines) or stock
    if box is None:
        raise VDError("the bay cut needs the stock box in side-B coordinates: it tells where each slot opens")
    retired = [] if rd.sim else retire_other_runs(data_path)
    if os.path.exists(out_path):
        os.remove(out_path)                                # ours (checked above): never leave an old one

    fails: List[str] = []
    warns: List[str] = []
    notes: List[str] = []
    age = data_age_minutes(data_path)
    if age > th["max_data_age_min"] and not allow_old_data:
        fails.append(f"the rough locate data is {age:.0f} minutes old - jog to the slot openings again")
    if not rd.sim and rd.mode != 0:
        fails.append(f"rough locate data was recorded with ShopBot mode {rd.mode} (expected 0 = MOVE)")
    openings = [slot_opening(d, box) for d in design]
    fit = fit_rigid(openings, rd.points)
    spacing = _spacing_errors(openings, rd.points)
    worst = max(abs(e) for *_, e in spacing)
    if worst > th["rough_spacing_fail"] * s:
        fails.append(f"the distance between the jogged openings differs from the design by {worst:.3f} "
                     f"{'mm' if rd.units else 'in'} (spacing error) - jog to the slot openings again; if the stock "
                     "does not match the stock box at the slots, use the key notch")
    if abs(fit.theta) > math.radians(th["max_rotation_deg"]):
        fails.append(f"rotation {math.degrees(fit.theta):+.2f} deg exceeds the {th['max_rotation_deg']:.1f} deg limit")
    bx = [b[0] for b in rd.base]
    by = [b[1] for b in rd.base]
    offset = (sorted(bx)[len(bx) // 2], sorted(by)[len(by) // 2])
    if max(max(bx) - min(bx), max(by) - min(by)) > th["offset_consistency"] * s and not rd.sim:
        fails.append("X/Y zero was changed during the rough locate - jog to the slot openings again")
        offset = None

    tr = SbpTransformer(fit)
    body = None
    try:
        body = tr.transform(lines)
    except TransformError as e:
        fails.append(f"bay file not supported - {e}")
    warns += tr.stats.warnings
    reach = th["bay_reach"] * s
    far = [p for p in tr.stats.cut_points if min(math.dist(p, d[:2]) for d in design) > reach]
    if body is not None and not tr.stats.cut_points:
        fails.append("the bay file has no cutting moves")
    elif far:
        p = max(far, key=lambda q: min(math.dist(q, d[:2]) for d in design))
        fails.append(f"this does not look like the bay file: {len(far)} cutting moves are more than {reach:g} from the "
                     f"slots (e.g. X {p[0]:.3f} Y {p[1]:.3f}). The rough fit is only good to about 0.2 in and must "
                     "never place a part file")
    fu = tr.stats.file_units if tr.stats.file_units is not None else detect_units_comment(lines)
    if fu is not None and fu != rd.units:
        fails.append(f"bay file is in {'mm' if fu else 'inches'} but the rough locate data is in "
                     f"{'mm' if rd.units else 'in'}")

    written = None
    if body is not None and not fails:
        pd = ProbeData(units=rd.units, sim=rd.sim, tip=0.0, nominal=0.0, nholes=len(design), passes=1,
                       spin180=False, pin=0, trig=0, mode=rd.mode, date=rd.date, cfg=[], touches=[], path=data_path)
        summary = "ROUGH ALIGNMENT FOR THE BAY CUT ONLY - from the jogged slot openings - never for part files"
        probed_at = datetime.datetime.fromtimestamp(os.path.getmtime(data_path)).strftime("%Y-%m-%d at %H.%M")
        out_lines = build_output(body, pd, fit, bay_path, offset, th, summary, probed_at,
                                 confirm="ROUGH bay cut only - not a part file - {info}. Bay cutter installed - Z "
                                         "zeroed - X and Y NOT re-zeroed since the rough locate?")
        with open(out_path, "wb") as fh:
            fh.write((nl.join(out_lines) + nl).encode("latin-1", errors="replace"))
        written = out_path
        record_output(written, data_path)
    if retired:
        notes.append("Removed aligned files from an earlier run: " + "; ".join(os.path.basename(p) for p in retired))
    u = "mm" if rd.units else "in"
    L = [f"VIRTUAL DOWEL ROUGH FIT (bay cut)  -  {'FAIL' if fails else 'PASS'}",
         f"Rough locate data: {data_path}  ({rd.date})" + ("  ** SIMULATED **" if rd.sim else ""),
         f"Bay file  : {bay_path}", f"Output    : {written or '(none written)'}"]
    if fails:
        L += ["", "FAILED CHECKS:"] + ["  - " + f for f in fails]
    if warns:
        L += ["", "WARNINGS:"] + ["  - " + w for w in warns]
    L.append("")
    for i, (o, p) in enumerate(zip(openings, rd.points), 1):
        L.append(f"Slot {i} opening: design {o[0]:.3f},{o[1]:.3f} -> jogged {p[0]:.3f},{p[1]:.3f}")
    L.append(f"Spacing error {worst:+.3f} {u}   Rotation {math.degrees(fit.theta):+.3f} deg   "
             f"Translation X {fit.tx:+.3f} Y {fit.ty:+.3f} {u}")
    L += notes
    report = "\n".join(L)
    try:
        with open(os.path.splitext(out_path)[0] + "_report.txt", "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
    except OSError:
        pass
    return Result(ok=written is not None, report=report, out_path=written, fails=fails, warns=warns,
                  rotation_deg=math.degrees(fit.theta), spacing_err=worst, shift=(fit.tx, fit.ty))


def batch_summary(results: Sequence[Tuple[str, Result]]) -> str:
    n_ok = sum(1 for _, r in results if r.ok)
    lines = [f"{n_ok} of {len(results)} file(s) aligned", ""]
    for job, r in results:
        lines.append(f"  {'PASS' if r.ok else 'FAIL'}  {os.path.basename(job)}"
                     + (f"  ->  {os.path.basename(r.out_path)}" if r.ok and r.out_path else "  (nothing written)"))
    return "\n".join(lines)


def _log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(msg.rstrip("\n") + "\n")
    except OSError:
        pass


def _raw_command_line() -> str:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.GetCommandLineW.restype = ctypes.c_wchar_p
            return ctypes.windll.kernel32.GetCommandLineW()
        except Exception:
            pass
    return " ".join(sys.argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point: runs the command and records the outcome in vd_launch.log."""
    args = list(sys.argv[1:] if argv is None else argv)
    _log(f"python {sys.version.split()[0]} at {sys.executable}  args {args}")
    try:
        rc = _main(args)
    except Exception:
        tb = traceback.format_exc()
        print(tb)
        _log("CRASH\n" + tb)
        rc = 1
    _log(f"vd_align.py finished, exit code {rc}")
    if rc not in (0, 3) and "--nogui" not in [x.lower() for x in args] and not os.environ.get("VD_FROM_BAT"):
        try:
            if sys.stdin is not None and sys.stdin.isatty():
                input("\nSomething went wrong - see above (also in vd_launch.log). Press Enter to close.")
        except (EOFError, OSError):
            pass
    return rc


def _window(mode: str, data: Optional[str] = None) -> int:
    try:
        sys.path.insert(0, HERE)
        import vd_app
    except ImportError as e:
        msg = (f"The Virtual Dowel window cannot open ({e}). Python needs tkinter (reinstall Python with "
               "the Tcl/Tk option); vd_app.py must be next to vd_align.py.")
        print(msg)
        _log(msg)
        if mode == "prepare":
            write_run_stub(msg)
        return 2
    return vd_app.run(mode, data)


def _main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Virtual Dowel flip-alignment for ShopBot OpenSBP files")
    ap.add_argument("data_pos", nargs="?", help="probe data file (default: vd_probe_data.csv next to this script)")
    ap.add_argument("--data", help="probe data written by vd_probe.sbp")
    ap.add_argument("--job", action="append", help="side-B job file (.sbp); repeat for several files")
    ap.add_argument("--holes", help='hole centers + diameters, e.g. "12,1,0.5;12,29,0.625" '
                                    "(default: <job>.vdholes.txt)")
    ap.add_argument("--out", help="output file (default: <job>_VD.sbp)")
    ap.add_argument("--stock", help='stock box when the job header has none: "W,H" or "xmin,ymin,xmax,ymax" '
                                    "(default: from <job>.vdholes.txt)")
    ap.add_argument("--allow-old-data", action="store_true", help="accept probe data older than the limit")
    ap.add_argument("--nogui", action="store_true", help="never open dialogs")
    ap.add_argument("--new-session", action="store_true",
                    help="delete aligned files written by earlier runs (vd_probe.sbp calls this first)")
    ap.add_argument("--install", action="store_true",
                    help="point vd_probe.sbp and vd_selftest.sbp at this folder and set up the Python "
                         "that ShopBot starts (vd_install.bat runs this)")
    # ShopBot upper-cases strings, so accept --FLAGS in any case
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = [x.lower() if x.startswith("--") else x for x in argv]
    # vd_probe.sbp uses plain words: SB3's SHELL alters "-", "/" and "~" on the way
    if argv and argv[0].lower() == "newsession":
        argv = ["--new-session"] + argv[1:]
    # the window: before probing (custom cut), after probing, after a calibration run
    if argv and argv[0].lower() in ("prepare", "align", "calresult"):
        word = argv[0].lower()
        return _window(word, argv[1] if len(argv) > 1 else None)
    if argv and argv[0].lower() == "shelltest":
        _log("shelltest: command line as received: " + _raw_command_line())
        _log("shelltest: arguments: " + repr(argv[1:]))
        return 0
    a = ap.parse_args(argv)
    if a.new_session:
        for p in new_session():
            print("removed old aligned file:", p)
        return 0
    if a.install:
        try:
            for line in install_paths():
                print(line)
        except (OSError, VDError) as e:
            print(f"ERROR: {e}")
            return 1
        print("\nDone. In ShopBot, type the custom cut shown above (normally C80) to start Virtual Dowel.")
        print("If no custom cut was installed, use FP and pick vd_start.sbp in", HERE)
        return 0
    data = a.data or a.data_pos or DEFAULT_DATA

    if a.job is None and not a.nogui:
        return _window("align", data)
    if a.job is None:
        print("--job is required with --nogui")
        return 2
    if a.out and len(a.job) > 1:
        print("--out can only be used with a single --job")
        return 2
    rc = 0
    for job in a.job:
        try:
            side_holes, side_stock = read_sidecar_full(job)
            design = parse_holes_arg(a.holes) if a.holes else side_holes
            stock = parse_stock(a.stock) if a.stock else side_stock
            if not design:
                print(f"{job}: no hole coordinates: pass --holes or create " + sidecar_path(job))
                rc = 2
                continue
            res = run_alignment(data, job, design, a.out, allow_old_data=a.allow_old_data, stock=stock)
        except (OSError, VDError) as e:
            print(f"ERROR ({job}): {e}")
            rc = max(rc, 1)
            continue
        print(res.report + "\n")
        if not res.ok:
            rc = max(rc, 1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
