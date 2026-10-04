"""Virtual Dowel - Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

Scratch check: side-A/side-B registration error of the corner+edge method.
Stock frame S: corner C at origin, edge E along +x, stock in +y. End-over-end flip about x."""
import math

def rot(a, p):
    c, s = math.cos(a), math.sin(a)
    return (c * p[0] - s * p[1], s * p[0] + c * p[1])

def add(p, q): return (p[0] + q[0], p[1] + q[1])
def sub(p, q): return (p[0] - q[0], p[1] - q[1])
def mul(k, p): return (k * p[0], k * p[1])
def dot(p, q): return p[0] * q[0] + p[1] * q[1]
def norm(p):
    L = math.hypot(*p); return (p[0] / L, p[1] / L)

def intersect(p1, d1, p2, d2):
    den = d1[0] * d2[1] - d1[1] * d2[0]
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
    return add(p1, mul(t, d1))

W, H, T = 30.0, 20.0, 3.0

def stock(gamma, tilt):
    """Parallelogram corners in S (C, E-end, far, F-end) and per-edge tilt (rad, + = leans out going up)."""
    f = (math.cos(math.radians(90 + gamma)), math.sin(math.radians(90 + gamma)))
    C = (0.0, 0.0); P2 = (W, 0.0); P4 = mul(H, f); P3 = add(P2, P4)
    return [C, P2, P3, P4], tilt   # edges: E=C-P2 (front), R=P2-P3, B=P3-P4, F=P4-C (left)

def edge_line_at(corners, tilt, k, h):
    """Edge k as (point, unit dir, outward normal) at height h above the side-A BOTTOM face."""
    a, b = corners[k], corners[(k + 1) % 4]
    d = norm(sub(b, a)); n = (d[1], -d[0])          # CCW polygon: outward = right of direction
    off = (h - T / 2) * math.tan(tilt[k])
    return add(a, mul(off, n)), d, n

def probe_side(side, corners, tilt, place, depth, err):
    """Return measured machine-frame corner C and E direction for the physical corner C, edge E.
    place = (rho, t, mirror). err: r_true, r_set, push (isotropic inward reading), ecc (machine frame)."""
    rho, t, mir = place
    to_m = lambda p: add(rot(rho, (p[0], -p[1]) if mir else p), t)
    h = T - depth if side == "A" else depth
    meas = {}
    for k in (0, 3):                                   # edge E (front) and F (left) meet at C
        p, d, n = edge_line_at(corners, tilt, k, h)
        L = math.dist(corners[k], corners[(k + 1) % 4])
        pts = []
        for s in (1.0, L - 1.0):                       # stations 1 in from each end
            q = add(p, mul(s, d))
            ball = add(q, mul(err["r_true"], n))       # ball centre at contact (S frame)
            ball = add(ball, mul(-err["push"], n))     # reading shifted inward (pretravel, backlash, latency)
            spindle = sub(to_m(ball), err["ecc"])      # SB3 reports the spindle axis
            pts.append(spindle)
        dm = norm(sub(pts[1], pts[0]))
        nm = (dm[1], -dm[0]) if not mir else (-dm[1], dm[0])   # outward normal in machine frame
        meas[k] = (add(pts[0], mul(-err["r_set"], nm)), dm)
    Cm = intersect(meas[0][0], meas[0][1], meas[3][0], meas[3][1])
    e = meas[0][1]
    if e[0] < 0: e = mul(-1, e)                        # X axis points +X
    return Cm, math.atan2(e[1], e[0]), to_m, rho, t, mir

def physical(design, frame):
    Cm, th, to_m, rho, t, mir = frame
    m = add(Cm, rot(th, design))
    q = rot(-rho, sub(m, t))
    return (q[0], -q[1]) if mir else q

def registration(gamma=0.0, tilt=(0, 0, 0, 0), dA=0.5, dB=0.5, err=None, eccB=None):
    err = dict(dict(r_true=0.0625, r_set=0.0625, push=0.0, ecc=(0.0, 0.0)), **(err or {}))
    corners, tl = stock(gamma, tilt)
    A = probe_side("A", corners, tl, (math.radians(1.3), (5.0, 7.0), False), dA, err)
    errB = dict(err, ecc=eccB if eccB is not None else err["ecc"])
    B = probe_side("B", corners, tl, (math.radians(-2.1), (8.0, 40.0), True), dB, errB)
    worst = 0.0
    for u, v in ((2, 2), (28, 3), (15, 10), (3, 18), (27, 17)):
        pa = physical((u, v), A)          # side-A design (Setup 1: origin C, X along E, stock +Y)
        pb = physical((u, -v), B)         # side-B design (Setup 2: same corner, Y inverted)
        worst = max(worst, math.dist(pa, pb))
    return worst

if __name__ == "__main__":
    tilt05 = tuple(math.radians(x) for x in (0.5, 0.5, 0.5, 0.5))
    rows = [
        ("perfect probe, square stock", registration()),
        ("rhomboid 1.5 deg", registration(gamma=1.5)),
        ("ball setting 0.005 too small", registration(err=dict(r_set=0.0575))),
        ("isotropic pretravel/backlash 0.004", registration(err=dict(push=0.004))),
        ("stylus runout 0.002, same both sides", registration(err=dict(ecc=(0.0014, 0.0014)))),
        ("stylus runout, spindle turned between sides", registration(err=dict(ecc=(0.002, 0.0)), eccB=(-0.002, 0.0))),
        ("edges tilted 0.5 deg, depth 0.5 both sides", registration(tilt=tilt05)),
        ("edges tilted 0.5 deg, depth T/2 both sides", registration(tilt=tilt05, dA=1.5, dB=1.5)),
        ("tilt 0.5 deg on E only, depth 0.5", registration(tilt=(math.radians(0.5), 0, 0, 0))),
        ("tilt 0.1 deg, depth 0.5", registration(tilt=tuple(math.radians(0.1) for _ in range(4)))),
    ]
    for name, v in rows:
        print(f"{name:48s} {v:.5f} in")
    assert rows[0][1] < 1e-9 and rows[1][1] < 1e-9 and rows[2][1] < 1e-9 and rows[3][1] < 1e-9
    assert rows[7][1] < 1e-9 and rows[6][1] > 0.01
