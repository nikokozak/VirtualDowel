"""
Virtual Dowel - Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

Independent OpenSBP toolpath sampler used only by the tests.

It does NOT share code with vd_align.py. It expands the motion commands of a
part file into a list of sampled tool positions so an original file and its
aligned copy can be compared point by point:  aligned == R * original + t.
"""
import math
import re

N_ARC = 48


def _strip(line):
    out, q = [], False
    for ch in line:
        if ch == '"':
            q = not q
        if ch == "'" and not q:
            break
        out.append(ch)
    return "".join(out).strip()


def _params(rest):
    if not rest:
        return []
    if rest[0] == ",":
        rest = rest[1:]
    else:
        rest = rest.lstrip()
    return [t.strip() for t in rest.split(",")]


def _val(p, i):
    """float, None for blank, or the raw text for an expression (compared literally)."""
    if i < len(p) and p[i] != "":
        try:
            return float(p[i])
        except ValueError:
            return p[i]
    return None


def _arc_points(sx, sy, ex, ey, cx, cy, cw, z):
    r = math.hypot(sx - cx, sy - cy)
    r2 = math.hypot(ex - cx, ey - cy)
    if abs(r - r2) > 5e-4:          # posts round to 4 decimals
        raise ValueError(f"inconsistent arc radii {r} vs {r2}")
    a0 = math.atan2(sy - cy, sx - cx)
    a1 = math.atan2(ey - cy, ex - cx)
    full = math.hypot(ex - sx, ey - sy) < 1e-9
    if cw:
        while a1 >= a0 - 1e-12:
            a1 -= 2 * math.pi
        if full:
            a1 = a0 - 2 * math.pi
    else:
        while a1 <= a0 + 1e-12:
            a1 += 2 * math.pi
        if full:
            a1 = a0 + 2 * math.pi
    pts = []
    for k in range(1, N_ARC + 1):
        a = a0 + (a1 - a0) * k / N_ARC
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a), z))
    return pts


def sample(text):
    """Return a list of (kind, points, xy_known) with points = [(x, y, z), ...].
    xy_known is False until the first absolute move that sets both X and Y: before that the
    machine is wherever the operator left it, so those samples cannot be compared."""
    x = y = z = 0.0
    known = False
    absolute = True
    ij = None
    out = []
    for raw in text.splitlines():
        s = _strip(raw)
        if not s or s.endswith(":") or s.startswith("&"):
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9#])(?=$|[\s,])(.*)$", s)
        if not m:
            continue
        cmd, p = m.group(1).upper(), _params(m.group(2))
        if cmd == "SA":
            absolute = True
            continue
        if cmd == "SR":
            absolute = False
            continue
        if cmd in ("JH", "MH"):
            # goes to the machine's X0,Y0 - not part of the aligned geometry, so not compared
            out.append((cmd, [(x, y, z), (0.0, 0.0, z)], False))
            x, y = 0.0, 0.0
            known = False
            continue
        if cmd in ("M2", "M3", "M4", "M5", "J2", "J3", "J4", "J5", "MX", "MY", "JX", "JY", "MZ", "JZ"):
            if cmd in ("MX", "JX"):
                vx, vy, vz = _val(p, 0), None, None
            elif cmd in ("MY", "JY"):
                vx, vy, vz = None, _val(p, 0), None
            elif cmd in ("MZ", "JZ"):
                vx, vy, vz = None, None, _val(p, 0)
            else:
                vx, vy, vz = _val(p, 0), _val(p, 1), _val(p, 2)
            if absolute:
                nx = x if vx is None else vx
                ny = y if vy is None else vy
                nz = z if vz is None else vz
            else:
                nx = x + (vx or 0.0)
                ny = y + (vy or 0.0)
                nz = z + (vz or 0.0)
            if absolute and vx is not None and vy is not None:
                known = True
            out.append((cmd, [(x, y, z), (nx, ny, nz)], known))
            x, y, z = nx, ny, nz
            continue
        if cmd == "CG":
            dia = _val(p, 0)
            ex = _val(p, 1)
            ey = _val(p, 2)
            ex = x if ex is None else ex
            ey = y if ey is None else ey
            cw = (_val(p, 6) if _val(p, 6) is not None else 1) > 0
            plunge = _val(p, 7) or 0.0
            reps = int(_val(p, 8) or 1)
            if dia is None:
                i = _val(p, 3)
                j = _val(p, 4)
                if i is None:
                    i = ij[0]
                if j is None:
                    j = ij[1]
                ij = (i, j)
                cx, cy = x + i, y + j
            else:
                r = dia / 2.0
                mx, my = (x + ex) / 2.0, (y + ey) / 2.0
                L = math.hypot(ex - x, ey - y)
                h = math.sqrt(max(r * r - (L / 2.0) ** 2, 0.0))
                ux, uy = (ex - x) / L, (ey - y) / L
                # right-hand normal of travel direction
                rx, ry = uy, -ux
                side = 1.0 if cw else -1.0
                cx, cy = mx + side * h * rx, my + side * h * ry
            z0 = z
            pts = []
            for _ in range(reps):
                z = z + plunge
                pts += _arc_points(x, y, ex, ey, cx, cy, cw, z)
            if plunge != 0.0 and not (_val(p, 12) == 1):
                z = z0
            out.append(("CG", [(x, y, z0)] + pts, known))
            x, y = ex, ey
            continue
    return out


def compare(orig_text, aligned_text, theta, tx, ty, tol=2e-5):
    """Max deviation between R*orig+t and aligned, plus structural checks."""
    a = sample(orig_text)
    b = sample(aligned_text)
    if len(a) != len(b):
        raise AssertionError(f"segment count differs: {len(a)} vs {len(b)}")
    c, s = math.cos(theta), math.sin(theta)
    worst = 0.0
    for (ka, pa, known_a), (kb, pb, known_b) in zip(a, b):
        if len(pa) != len(pb) or known_a != known_b:
            raise AssertionError(f"sample structure differs for {ka}/{kb}")
        if not known_a:
            continue
        for (x, y, z), (X, Y, Z) in zip(pa[1:], pb[1:]):   # [0] is the (possibly unknown) start
            ex = c * x - s * y + tx
            ey = s * x + c * y + ty
            if isinstance(z, str) or isinstance(Z, str):
                dz = 0.0 if z == Z else math.inf
            else:
                dz = abs(z - Z)
            worst = max(worst, math.hypot(ex - X, ey - Y), dz)
    if worst > tol:
        raise AssertionError(f"aligned toolpath deviates by {worst:.2e}")
    return worst
