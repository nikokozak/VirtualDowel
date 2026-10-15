"""
Virtual Dowel - Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

Minimal OpenSBP interpreter + simulated ShopBot/probe, used only by the tests to
execute vd_probe.sbp end to end.

It implements just the statements vd_probe.sbp uses, with the semantics from the
ShopBot Programming Handbook (SBG00314), and it is deliberately strict: anything
it does not recognise raises, so typos and unsupported syntax surface in tests.

Simulated physics
  * surface at Z = 0, holes are vertical through-cylinders
  * probe = ball of radius rt whose center is offset from the spindle axis by `ecc`
  * the probe input switches after `pretravel` of deflection (+ random jitter)
  * after the switch, motion continues for speed * latency before stopping
  * ON INP fires only on a CHANGE into the armed state (as documented)
  * any deflection beyond `overtravel` is recorded as a collision
  * optional `solid` (SlotStock): thick stock with half slots and bays instead of a holed surface;
    then the stylus stem and the probe body above the ball must never touch anything either
"""
import math
import random
import re


class VMError(Exception):
    pass


def strip_comment(line):
    out, q = [], False
    for ch in line:
        if ch == '"':
            q = not q
        if ch == "'" and not q:
            break
        out.append(ch)
    return "".join(out)


# ------------------------------------------------------------------ expressions
TOK = re.compile(r"""\s*(?:
    (?P<num>\d+\.?\d*|\.\d+)|
    (?P<str>"[^"]*")|
    (?P<var>&[A-Za-z_][A-Za-z0-9_]*)|
    (?P<sys>%)|
    (?P<op>[-+*/()=<>&])
)""", re.X)


def tokenize(s):
    pos, out = 0, []
    s = s.rstrip()
    while pos < len(s):
        m = TOK.match(s, pos)
        if not m or m.end() == pos:
            raise VMError(f"cannot tokenize expression {s!r} at {pos}")
        pos = m.end()
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
    return out


class Expr:
    def __init__(self, vm, text):
        self.vm = vm
        self.t = tokenize(text)
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        v = self.cmp()
        if self.i != len(self.t):
            raise VMError(f"trailing tokens in expression: {self.t[self.i:]}")
        return v

    def cmp(self):
        a = self.concat()
        k, v = self.peek()
        if k == "op" and v in "=<>":
            self.take()
            b = self.concat()
            if isinstance(a, str) or isinstance(b, str):
                a2, b2 = str(a).upper(), str(b).upper()
            else:
                a2, b2 = a, b
            return {"=": a2 == b2, "<": a2 < b2, ">": a2 > b2}[v]
        return a

    def concat(self):
        a = self.add()
        while self.peek() == ("op", "&"):
            self.take()
            b = self.add()
            a = self.vm.fmt(a) + self.vm.fmt(b)
        return a

    def add(self):
        a = self.mul()
        while self.peek()[0] == "op" and self.peek()[1] in "+-":
            op = self.take()[1]
            b = self.mul()
            a = a + b if op == "+" else a - b
        return a

    def mul(self):
        a = self.unary()
        while self.peek()[0] == "op" and self.peek()[1] in "*/":
            op = self.take()[1]
            b = self.unary()
            a = a * b if op == "*" else a / b
        return a

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            return -self.unary()
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.prim()

    def prim(self):
        k, v = self.take()
        if k == "num":
            return float(v)
        if k == "str":
            return v[1:-1]
        if k == "var":
            key = v.upper()
            if key not in self.vm.vars:
                raise VMError(f"variable {v} used before assignment")
            return self.vm.vars[key]
        if k == "sys":
            if self.take() != ("op", "("):
                raise VMError("expected ( after %")
            n = self.cmp()
            if self.take() != ("op", ")"):
                raise VMError("expected ) after %(...")
            return self.vm.machine.sysvar(int(round(n)))
        if (k, v) == ("op", "("):
            r = self.cmp()
            if self.take() != ("op", ")"):
                raise VMError("missing )")
            return r
        raise VMError(f"unexpected token {v!r}")


# ------------------------------------------------------------------ thick stock with half slots
def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


class SlotStock:
    """Side B of the half-slot mode. Geometry in DESIGN coordinates (the side-B job's), placed on the
    table turned by theta and shifted by t; z = height above the spoilboard. Half slots were cut from
    face A (now on the spoilboard) up to slot_top; bays from the top T down to bay_floor.
    slots: [(x, y, width, (axis, sign))] - round-end centre, width, the side the slot opens to.
    bays: [(x0, y0, x1, y1)] axis-aligned boxes in design coordinates (placed by the rough fit).
    probe: stem radius, free stem length (ball centre to the body), body radius and length, nut radius."""

    def __init__(self, W, H, T, theta, t, slots, slot_top, bays=(), bay_floor=None,
                 probe=None, chips=()):
        self.W, self.H, self.T = W, H, T
        self.c, self.s, self.t = math.cos(theta), math.sin(theta), t
        self.slot_top = slot_top
        self.bay_floor = slot_top if bay_floor is None else bay_floor
        self.bays = list(bays)
        self.probe = probe or dict(stem=0.04, free=1.0, body=0.4, body_len=2.0, nut=0.9)
        far = 10.0 * max(W, H, 1.0)
        self.caps = [((x + sg * far, y) if ax == 1 else (x, y + sg * far), (x, y), w / 2.0)
                     for x, y, w, (ax, sg) in slots]
        self.chips = list(chips)              # [(x, y, r)] small bits of debris standing in a slot

    def to_design(self, x, y):
        dx, dy = x - self.t[0], y - self.t[1]
        return (self.c * dx + self.s * dy, -self.s * dx + self.c * dy)

    def free(self, x, y, z):
        """Clearance from (x, y) at height z to the nearest material (negative inside). Never larger
        than the true clearance, so a move may safely step by it."""
        if z < 0.0:
            return z                          # spoilboard
        if z > self.T:
            return 1e9                        # nothing above the stock (no clamps modelled)
        d = -min(x, self.W - x, y, self.H - y)
        if z <= self.slot_top:
            for a, c, r in self.caps:
                d = max(d, r - _seg_dist(x, y, a[0], a[1], c[0], c[1]))
        if z >= self.bay_floor:
            for x0, y0, x1, y1 in self.bays:
                d = max(d, min(x - x0, x1 - x, y - y0, y1 - y))
        for cx, cy, r in self.chips:
            if z <= self.slot_top:
                d = min(d, math.hypot(x - cx, y - cy) - r)
        return d

    def ball_pen(self, mx, my, z, rt):
        x, y = self.to_design(mx, my)
        if all(abs(z - lv) > rt for lv in (0.0, self.slot_top, self.bay_floor, self.T)):
            return rt - self.free(x, y, z)
        best = -1e9                           # near a floor/ceiling: look at slices of the ball
        for k in (-1.0, -0.5, 0.0, 0.5, 1.0):
            h = 0.999 * k * rt
            best = max(best, math.sqrt(rt * rt - h * h) - self.free(x, y, z + h))
        return best

    def body_hit(self, mx, my, z, rt):
        """What touches besides the ball: ('stem'|'body'|'nut', height) or None."""
        x, y = self.to_design(mx, my)
        p = self.probe
        zb = z + p["free"]
        for name, r, z0, z1 in (("stem", p["stem"], z + rt, zb), ("body", p["body"], zb, zb + p["body_len"]),
                                ("nut", p["nut"], zb + p["body_len"], zb + p["body_len"] + 0.6)):
            n = max(1, int((z1 - z0) / 0.05))
            for i in range(n + 1):
                zz = z0 + (z1 - z0) * i / n
                if self.free(x, y, zz) < r - 1e-9:
                    return (name, round(zz, 3))
        return None


# ------------------------------------------------------------------ machine
class Machine:
    def __init__(self, holes, tip=0.125, ecc=(0.0, 0.0), pretravel=0.004, latency=0.012,
                 jitter=0.0002, idle=1, overtravel=0.12, base_offset=(3.25, -1.5), units=0,
                 start=(0.0, 0.0, 1.0), seed=1, mode=0, work_decimals=3, probe_pin=1, other_inputs=None,
                 solid=None, hold=0.0):
        self.holes = holes              # list of (cx, cy, diameter) in working coords
        self.probe_pin = probe_pin      # input the probe is wired to
        self.other_inputs = dict(other_inputs or {})   # {pin: fixed state} for everything else
        self.rt = tip / 2.0
        self.ecc = ecc
        self.pretravel = pretravel
        self.latency = latency
        self.jitter = jitter
        self.idle = idle
        self.overtravel = overtravel
        self.base_offset = base_offset
        self.units = units
        self.x, self.y, self.z = start
        self.vxy, self.vz = 1.0, 0.5
        self.time = 0.0
        self.finger = []               # (t0, t1) windows where a finger deflects the tip
        self.unplugged = False
        self.collisions = []
        self.max_pen = 0.0
        self.rng = random.Random(seed)
        self.mode = mode                # %(22): 0 = MOVE, 1 = PREVIEW
        self.work_decimals = work_decimals   # SB3 returns %(1)-%(3) rounded (user's selftest: 3)
        self.spurious_arms = 0          # fire this many ON INP handlers right after arming
        self.file_offset = (0.0, 0.0)   # %(61) %(62)
        self.file_prop = (1.0, 1.0)     # %(66) %(67)
        self.cur_pre = pretravel
        self.moves = 0
        self.solid = solid              # SlotStock: thick stock instead of the holed surface
        # a switch at its threshold: it trips in motion after `pretravel`, but once the machine has
        # stopped it stays tripped only if the deflection is `hold` beyond that (user's probe, 2026-10-15:
        # "fires briefly and returns to clear, a tiny more force gives a full switch")
        self.hold = hold
        self.moving = False

    # geometry ---------------------------------------------------------------------
    def penetration(self, x, y, z):
        """How far the probe ball is pushed into material (<=0: free)."""
        if self.solid is not None:
            return self.solid.ball_pen(x + self.ecc[0], y + self.ecc[1], z, self.rt)
        if z >= 0:
            return -1.0
        # material = everything below Z0 except the hole cylinders. The ball is pushed in by the
        # smaller of: its depth below the surface, or its overlap with the nearest hole wall.
        tx, ty = x + self.ecc[0], y + self.ecc[1]
        pen = -z
        for cx, cy, d in self.holes:
            pen = min(pen, math.hypot(tx - cx, ty - cy) + self.rt - d / 2.0)
        return pen

    def deflected_at(self, x, y, z):
        return self.penetration(x, y, z) > self.cur_pre + (0.0 if self.moving else self.hold)

    def input_state(self, pin):
        if pin != self.probe_pin:
            return self.other_inputs.get(pin, self.idle)   # other switches: fixed, unchanging
        if self.unplugged:
            return 1 - self.idle
        if any(t0 <= self.time <= t1 for t0, t1 in self.finger):
            return 1 - self.idle
        return (1 - self.idle) if self.deflected_at(self.x, self.y, self.z) else self.idle

    def sysvar(self, n):
        bx, by = self.base_offset
        r = (lambda v: round(v, self.work_decimals)) if self.work_decimals is not None else (lambda v: v)
        # %(6)/%(7): table position of the X/Y zero - constant while the tool moves (user's SB3, real run)
        table = {1: r(self.x), 2: r(self.y), 3: r(self.z), 6: bx, 7: by, 25: self.units,
                 22: self.mode, 61: self.file_offset[0], 62: self.file_offset[1],
                 66: self.file_prop[0], 67: self.file_prop[1],
                 71: 1.5, 73: 0.5, 146: "09-25-2026", 147: "10:31:07"}
        if n in table:
            return table[n]
        if 51 <= n <= 58:
            return self.input_state(n - 50)
        raise VMError(f"system variable %({n}) not modelled")

    # motion -------------------------------------------------------------------------
    def move(self, tx, ty, tz, armed):
        """Straight move with optional input interrupt. armed = {pin: state}.
        Returns the pin that fired or None."""
        self.moves += 1
        x0, y0, z0 = self.x, self.y, self.z
        L = math.dist((x0, y0, z0), (tx, ty, tz))
        xy = math.hypot(tx - x0, ty - y0)
        speed = self.vxy if xy > 1e-12 else self.vz
        if L < 1e-12:
            return None
        self.cur_pre = self.pretravel + self.rng.gauss(0.0, self.jitter)
        start_state = {p: self.input_state(p) for p in armed}
        self.moving = True
        step = 2e-5
        n = max(1, int(L / step))
        fired = None
        stop_s = 1.0
        s = 0.0
        last_check = 0.0
        pen = self.penetration(x0, y0, z0) if self.solid is not None else 0.0
        while s < 1.0:
            if self.solid is not None and fired is None and pen < -0.003:
                # far from anything: step by half the clearance (free() never overstates it)
                s = min(1.0, s + max(1.0 / n, 0.5 * (-pen - 0.002) / L))
            else:
                s = min(1.0, (int(round(s * n)) + 1) / n)
            x = x0 + (tx - x0) * s
            y = y0 + (ty - y0) * s
            z = z0 + (tz - z0) * s
            pen = self.penetration(x, y, z)
            self.max_pen = max(self.max_pen, pen)
            if pen > self.overtravel:
                self.collisions.append((x, y, z, pen))
                stop_s = s
                break
            if self.solid is not None and ((s - last_check) * L >= 0.01 or s >= 1.0):
                last_check = s
                hit = self.solid.body_hit(x + self.ecc[0], y + self.ecc[1], z, self.rt)
                if hit:
                    self.collisions.append((x, y, z, hit))
                    stop_s = s
                    break
            if fired is None:
                self.x, self.y, self.z = x, y, z
                for p, st in armed.items():
                    now = self.input_state(p)
                    if now == st and start_state[p] != st:
                        fired = p
                        overshoot = speed * self.latency
                        stop_s = min(1.0, s + overshoot / L)
                        break
                if fired is not None and stop_s <= s:
                    break
            elif s >= stop_s:
                break
        self.moving = False
        self.x = x0 + (tx - x0) * stop_s
        self.y = y0 + (ty - y0) * stop_s
        self.z = z0 + (tz - z0) * stop_s
        self.time += L * stop_s / speed
        return fired


# ------------------------------------------------------------------ interpreter
class UI:
    """Scripted operator. Override hooks in tests."""

    def __init__(self, parks=None, sim_inputs=None, answers=None):
        self.parks = list(parks or [])          # [(x, y, z)] where the operator parks the probe
        self.sim_inputs = list(sim_inputs or [])
        # forced MSGBOX answers: {substring of the message: [answer, answer, ...]}
        self.answers = {k: list(v) for k, v in (answers or {}).items()}
        self.messages = []
        self.on_spin = None

    def msgbox(self, vm, body, btype, title):
        self.messages.append(body)
        if "Probe test" in body or "Finding the probe input" in body:
            # the operator holds the tip deflected for about 2.5 s (the routine checks once a second)
            vm.machine.finger.append((vm.machine.time + 1.0, vm.machine.time + 3.5))
        if "turn the spindle" in body and self.on_spin:
            self.on_spin(vm.machine)
        for key, queue in self.answers.items():
            if key in body and queue:
                return queue.pop(0)
        bt = btype.strip().upper()
        if bt in ("OKCANCEL", "1"):
            return "OK"
        if bt in ("YESNOCANCEL", "3", "YESNO", "4"):
            return "Yes"
        return "OK"

    def keypad(self, vm):
        if not self.parks:
            raise VMError("operator has no more park positions")
        x, y, z = self.parks.pop(0)
        m = vm.machine
        m.x, m.y, m.z = x, y, z

    def input(self, vm, prompt, n):
        if not self.sim_inputs:
            raise VMError("no scripted INPUT left")
        vals = self.sim_inputs.pop(0)
        if len(vals) != n:
            raise VMError(f"INPUT expects {n} values")
        return vals


class VM:
    def __init__(self, text, machine, ui, numfmt=None, max_steps=400000):
        self.src = text.splitlines()
        self.machine = machine
        self.ui = ui
        self.vars = {}
        self.labels = {}
        self.stack = []
        self.armed = {}          # pin -> (state, action, label)
        self.files = {}
        self.closed = {}
        self.shell = []
        self.fp = []
        self.printed = []
        self.commands = []
        self.ended = None
        self.max_steps = max_steps
        self.numfmt = numfmt or (lambda v: repr(v))
        for i, ln in enumerate(self.src):
            code = strip_comment(ln).strip()
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):$", code)
            if m:
                k = m.group(1).upper()
                if k in self.labels:
                    raise VMError(f"duplicate label {k}")
                self.labels[k] = i

    def fmt(self, v):
        if isinstance(v, str):
            return v
        if isinstance(v, bool):
            return "-1" if v else "0"
        return self.numfmt(v)

    def ev(self, text):
        return Expr(self, text).parse()

    def jump(self, label):
        k = label.strip().upper()
        if k not in self.labels:
            raise VMError(f"unknown label {label!r}")
        return self.labels[k] + 1

    def run(self):
        pc, steps = 0, 0
        while pc < len(self.src) and self.ended is None:
            steps += 1
            if steps > self.max_steps:
                raise VMError("runaway program")
            pc = self.step(pc)
        return self

    # -------------------------------------------------------------------------------
    def step(self, pc):
        raw = self.src[pc]
        code = strip_comment(raw).strip()
        if not code or re.match(r"^[A-Za-z_][A-Za-z0-9_]*:$", code):
            return pc + 1
        try:
            return self.statement(code, pc)
        except VMError as e:
            raise VMError(f"line {pc + 1}: {e}\n  >> {raw}") from None

    def statement(self, code, pc):
        if code.startswith("&"):
            m = re.match(r"^(&[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", code)
            if not m:
                raise VMError("bad assignment")
            self.vars[m.group(1).upper()] = self.ev(m.group(2))
            return pc + 1
        word = (re.match(r"^([A-Za-z_]+)", code) or [None, ""])[1].upper()
        if word == "IF":
            m = re.match(r"^IF\s+(.*?)\s+THEN\s+(.*)$", code, re.I)
            if not m:
                raise VMError("bad IF")
            if self.ev(m.group(1)):
                return self.clause(m.group(2).strip(), pc)
            return pc + 1
        if word in ("GOTO", "GOSUB", "RETURN", "END", "ENDALL"):
            return self.clause(code, pc)
        if word == "PAUSE":
            arg = code[5:].strip()
            if arg:
                secs = float(self.ev(arg))
                if secs < 1:
                    # on the user's SB3 (Move mode) a PAUSE under 1 s stopped the file until the
                    # operator continued it, although the handbook allows decimals
                    raise VMError(f"PAUSE {arg}: under 1 second - stops the file on the user's SB3")
                self.machine.time += secs
            return pc + 1
        if word == "MSGBOX":
            m = re.match(r"^MSGBOX\s*\((.*)\)$", code)
            if not m:
                raise VMError("bad MSGBOX (apostrophe or missing paren?)")
            parts = m.group(1).split(",")
            if len(parts) != 3:
                raise VMError(f"MSGBOX must have exactly 3 parts, got {len(parts)} (comma in body?)")
            body = parts[0]
            if "(" in body or ")" in body:
                raise VMError("parenthesis in MSGBOX body")
            self.vars["&MSGANSWER"] = self.ui.msgbox(self, body, parts[1], parts[2])
            return pc + 1
        if word == "INPUT":
            m = re.match(r'^INPUT\s+"([^"]*)"\s*(&.*)$', code)
            if not m:
                raise VMError("bad INPUT")
            if "," in m.group(1):
                raise VMError("comma in INPUT prompt")
            names = [n.strip().upper() for n in m.group(2).split(",")]
            vals = self.ui.input(self, m.group(1), len(names))
            for n, v in zip(names, vals):
                self.vars[n] = float(v)
            return pc + 1
        if word == "PRINT":
            items = [x for x in re.split(r";", code[5:])]
            self.printed.append("".join(self.fmt(self.ev(x)) for x in items if x.strip()))
            return pc + 1
        if word == "OPEN":
            m = re.match(r"^OPEN\s+(.+?)\s+FOR\s+OUTPUT\s+AS\s+#(\d)$", code, re.I)
            if not m:
                raise VMError("bad OPEN")
            self.files[int(m.group(2))] = (self.ev(m.group(1)), [])
            return pc + 1
        if word == "WRITE":
            m = re.match(r"^WRITE\s+#(\d)\s*;\s*(.*)$", code, re.I)
            if not m:
                raise VMError("bad WRITE")
            n = int(m.group(1))
            if n not in self.files:
                raise VMError("WRITE to a file that is not open")
            items = split_top(m.group(2), ";")
            self.files[n][1].append("".join(self.fmt(self.ev(x)) for x in items))
            return pc + 1
        if word == "CLOSE":
            m = re.match(r"^CLOSE\s*#(\d)$", code, re.I)
            if not m:
                raise VMError("bad CLOSE")
            n = int(m.group(1))
            self.closed[n] = self.files.pop(n)
            return pc + 1
        if word == "FP":
            # run another part file: recorded; the test runs that file itself if it needs to
            self.fp.append(code[2:].lstrip(" ,").strip())
            return pc + 1
        if word == "SHELL":
            m = re.match(r"^SHELL\s*,\s*(.+)$", code, re.I)
            parts = split_top(m.group(1), ",")
            self.shell.append((self.ev(parts[0]), [p.strip() for p in parts[1:]]))
            return pc + 1
        if word == "ON":
            m = re.match(r"^ON\s+INP\s*\((.+?),(.+?)\)\s*(.*)$", code, re.I)
            if not m:
                raise VMError("bad ON INP")
            pin = int(round(self.ev(m.group(1))))
            state = int(round(self.ev(m.group(2))))
            act = m.group(3).strip()
            if not act:
                self.armed.pop(pin, None)
            else:
                ma = re.match(r"^(GOSUB|GOTO)\s+([A-Za-z_][A-Za-z0-9_]*)$", act, re.I)
                if not ma:
                    raise VMError("unsupported ON INP action")
                self.jump(ma.group(2))
                self.armed[pin] = (state, ma.group(1).upper(), ma.group(2))
                if self.machine.spurious_arms > 0 and ma.group(1).upper() == "GOSUB":
                    # switch bounce right after arming: the handler runs BEFORE the next line
                    # (the move), which then executes unguarded
                    self.machine.spurious_arms -= 1
                    self.armed.pop(pin)
                    self.stack.append(pc + 1)
                    return self.jump(ma.group(2))
            return pc + 1
        return self.command(code, pc)

    def clause(self, c, pc):
        if c.startswith("&"):
            return self.statement(c, pc)
        w = c.split()[0].upper()
        if w == "GOTO":
            return self.jump(c.split(None, 1)[1])
        if w == "GOSUB":
            self.stack.append(pc + 1)
            return self.jump(c.split(None, 1)[1])
        if w == "RETURN":
            if not self.stack:
                raise VMError("RETURN without GOSUB")
            return self.stack.pop()
        if w == "END":
            self.ended = "END"
            return pc + 1
        if w == "ENDALL":
            self.ended = "ENDALL"
            return pc + 1
        return self.command(c, pc)

    def command(self, code, pc):
        m = re.match(r"^([A-Za-z][A-Za-z0-9])(?:$|[\s,]+(.*)$)", code)
        if not m:
            raise VMError(f"unrecognised statement {code!r}")
        cmd = m.group(1).upper()
        args = [a.strip() for a in (m.group(2) or "").split(",")] if m.group(2) else []
        vals = [self.ev(a) if a else None for a in args]
        self.commands.append(cmd)
        mc = self.machine
        if cmd == "SA":
            return pc + 1
        if cmd == "MS":
            if vals[0] is not None:
                mc.vxy = vals[0]
            if len(vals) > 1 and vals[1] is not None:
                mc.vz = vals[1]
            return pc + 1
        if cmd == "C7":
            return pc + 1
        if cmd == "SK":
            self.ui.keypad(self)
            return pc + 1
        if cmd in ("M2", "MZ"):
            if cmd == "M2":
                tx = mc.x if vals[0] is None else vals[0]
                ty = mc.y if len(vals) < 2 or vals[1] is None else vals[1]
                tz = mc.z
            else:
                tx, ty, tz = mc.x, mc.y, vals[0]
            armed = {p: st for p, (st, _, _) in self.armed.items()}
            fired = mc.move(tx, ty, tz, armed)
            if fired is not None:
                st, act, label = self.armed.pop(fired)       # auto-cleared after firing
                if act == "GOSUB":
                    self.stack.append(pc + 1)
                return self.jump(label)
            return pc + 1
        raise VMError(f"command {cmd} not modelled")


def split_top(s, sep):
    out, cur, q, depth = [], [], False, 0
    for ch in s:
        if ch == '"':
            q = not q
        if not q:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        if ch == sep and not q and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [x.strip() for x in out]


def sb3_number(v):
    """How the user's SB3 writes numbers (selftest 2026-09-28): up to ~15-18 significant digits,
    no leading zero before the point for positive values below 1 ('.333'), '-0.000123' for negatives."""
    s = f"{v:.15g}"
    if "e" in s:
        s = f"{v:.15f}".rstrip("0").rstrip(".")
    if s.startswith("0."):
        s = s[1:]
    return s


def vb_single(v):
    """Rough emulation of a VB6 Single printed by Str(): 7 significant digits,
    leading space for non-negatives, exponent for tiny values."""
    if v == 0:
        return " 0"
    s = f"{v:.7G}"
    if "E" in s:
        mant, exp = s.split("E")
        s = f"{mant}E{int(exp):+03d}"
    return (" " + s) if v > 0 else s
