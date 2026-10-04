"""Virtual Dowel - Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

Rough fit from jogged slot openings: each opening only fixes its slot's axis line (point-on-line)."""
import math, random

def rot(a, p): c, s = math.cos(a), math.sin(a); return (c*p[0]-s*p[1], s*p[0]+c*p[1])

def solve(lines, pts, iters=20):
    """lines: [(a, n)] design point on the line and unit normal; pts: machine points.
    Find theta, t with n . (R^-1 (m - t) - a) = 0 for each line (least squares, Gauss-Newton)."""
    th, tx, ty = 0.0, 0.0, 0.0
    for _ in range(iters):
        J, r = [], []
        for (a, n), m in zip(lines, pts):
            q = rot(-th, (m[0]-tx, m[1]-ty))
            r.append(n[0]*(q[0]-a[0]) + n[1]*(q[1]-a[1]))
            dq = rot(-th, (m[1]-ty, -(m[0]-tx)))            # d q / d theta
            dt = rot(-th, (-1, 0)), rot(-th, (0, -1))
            J.append([n[0]*dq[0]+n[1]*dq[1], n[0]*dt[0][0]+n[1]*dt[0][1], n[0]*dt[1][0]+n[1]*dt[1][1]])
        # normal equations 3x3
        A = [[sum(J[k][i]*J[k][j] for k in range(len(J))) for j in range(3)] for i in range(3)]
        b = [-sum(J[k][i]*r[k] for k in range(len(J))) for i in range(3)]
        for i in range(3):                                   # Gaussian elimination
            for j in range(i+1, 3):
                f = A[j][i]/A[i][i]; A[j] = [A[j][k]-f*A[i][k] for k in range(3)]; b[j] -= f*b[i]
        d = [0, 0, 0]
        for i in (2, 1, 0):
            d[i] = (b[i]-sum(A[i][k]*d[k] for k in range(i+1, 3)))/A[i][i]
        th, tx, ty = th+d[0], tx+d[1], ty+d[2]
    return th, tx, ty

W, H = 30.0, 20.0
lines = [((0, H/2), (0, 1)), ((W, H/2), (0, 1)), ((15, 0), (1, 0))]   # two flip-axis slots, key notch at x=15
ends = [(1.5, H/2), (W-1.5, H/2)]                                      # slot round ends (design)
rng = random.Random(1); worst = 0.0
for _ in range(2000):
    th0, t0 = math.radians(rng.uniform(-5, 5)), (rng.uniform(-3, 3), rng.uniform(-3, 3))
    to_m = lambda p: (rot(th0, p)[0]+t0[0], rot(th0, p)[1]+t0[1])
    gaps = (rng.uniform(0, 1.0), rng.uniform(0, 1.0), rng.uniform(0, 1.0))   # irregular edge: opening up to 1 in inside the box
    opens = [(gaps[0], H/2), (W-gaps[1], H/2), (15, gaps[2])]
    jog = []
    for p in opens:
        m = to_m(p); a = rng.uniform(0, 2*math.pi)
        jog.append((m[0]+0.1*math.cos(a), m[1]+0.1*math.sin(a)))       # 0.1 in hand-jog error
    th, tx, ty = solve(lines, jog)
    for e in ends:
        est = rot(th, e); est = (est[0]+tx, est[1]+ty)
        worst = max(worst, math.dist(est, to_m(e)))
print(f"worst bay placement error at the slot ends: {worst:.3f} in (jog error 0.1 in, edges up to 1 in inside the box)")
