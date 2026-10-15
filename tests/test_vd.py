"""
Virtual Dowel - Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

Tests for Virtual Dowel.  Run from the VirtualDowel folder:  python -m unittest discover -s tests -v
"""
import math
import os
import re
import shutil
import subprocess
import zipfile
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import vd_align as va                      # noqa: E402
import sim_sbp                             # noqa: E402
from sbp_vm import VM, Machine, UI, SlotStock, vb_single, sb3_number, strip_comment   # noqa: E402

with open(os.path.join(ROOT, "vd_probe.sbp"), encoding="latin-1") as _fh:
    PROBE = _fh.read()
JOB = os.path.join(HERE, "fixtures", "sideB_vcarve.sbp")
with open(JOB, encoding="latin-1") as _fh:
    JOB_TEXT = _fh.read()
# Fusion 360 ShopBot-post style: no stock size in the header, ON INPUT message, JZ + JH at the end
FUSION = os.path.join(HERE, "fixtures", "sideB_fusion.sbp")
with open(FUSION, encoding="latin-1") as _fh:
    FUSION_TEXT = _fh.read()


def configure(text, **kw):
    for k, v in kw.items():
        pat = re.compile(r"^(&vd_%s\s*=\s*)([^'\r\n]*?)(\s*'.*)?$" % k, re.M)
        text, n = pat.subn(lambda m: m.group(1) + str(v) + (m.group(3) or ""), text, count=1)
        assert n == 1, k
    return text


def T(theta_deg, tx, ty, p):
    th = math.radians(theta_deg)
    c, s = math.cos(th), math.sin(th)
    return (c * p[0] - s * p[1] + tx, s * p[0] + c * p[1] + ty)


class TrackingVM(VM):
    """Records the deepest GOSUB nesting reached."""
    max_depth = 0

    def clause(self, c, pc):
        r = super().clause(c, pc)
        self.max_depth = max(self.max_depth, len(self.stack))
        return r

    def command(self, code, pc):
        r = super().command(code, pc)
        self.max_depth = max(self.max_depth, len(self.stack))
        return r


def run_probe(true_holes, parks, machine_kw=None, ui_kw=None, numfmt=vb_single, **cfg):
    text = configure(PROBE, **cfg)
    m = Machine(holes=true_holes, **(machine_kw or {}))
    ui = UI(parks=parks, **(ui_kw or {}))
    vm = TrackingVM(text, m, ui, numfmt=numfmt).run()
    return vm, m, ui


def align_calls(vm):
    """SHELL calls that run the alignment (the first SHELL of every run is the session cleanup)."""
    return [c for c in vm.shell if not c[0].endswith(" newsession")]


# What SB3's SHELL did to a command on the user's machine (echo %cmdcmdline% in the window it
# opened): spaces around every "/" and every "~" removed. Other operator characters are
# suspected too, so launcher commands may only use letters, digits, _ . : \ and spaces.
def sb3_shell_observed(cmd):
    return cmd.replace("~", "").replace("/", " / ")


SHELL_SUSPECT = set("-/~+*=()<>^&%,;'\"")


# On the user's SB3, `&m = "a-b+c*d=e/f~g"` stopped with "Undefined variable: A": quoted text
# holding operator characters is worked out as arithmetic. MSGBOX, PRINT and INPUT prompts with
# " - " ran fine there, so only other code lines are restricted.
def risky_string_lines(text):
    bad = []
    for ln in text.splitlines():
        code = ln.strip()
        if not code or code.startswith("'") or re.match(r"^(MSGBOX|PRINT|INPUT)\b", code, re.I):
            continue
        if any(re.search(r"[-+*/=~^]", m) for m in re.findall(r'"([^"]*)"', code)):
            bad.append(ln)
    return bad


def assert_shell_proof(tc, cmd):
    tc.assertEqual(sb3_shell_observed(cmd), cmd)
    tc.assertFalse(SHELL_SUSPECT & set(cmd), cmd)


def data_text(vm):
    if 1 in vm.closed:
        return "\n".join(vm.closed[1][1]) + "\n"
    return "\n".join(vm.files[1][1]) + "\n"


def fit_of(theta_deg, tx, ty):
    return va.Fit(math.radians(theta_deg), tx, ty, [(0.0, 0.0)])


def pdata(**kw):
    base = dict(units=0, sim=False, tip=0.125, nominal=0.625, nholes=2, passes=2, spin180=False, pin=1,
                trig=0, mode=0, date="d", cfg=[], touches=[])
    base.update(kw)
    return va.ProbeData(**base)


_STATE_DIR = tempfile.mkdtemp()


def setUpModule():
    # keep the tests from touching the real vd_state.json next to the script
    va.STATE_FILE = os.path.join(_STATE_DIR, "vd_state.json")
    va.LOG_FILE = os.path.join(_STATE_DIR, "vd_launch.log")


def tearDownModule():
    shutil.rmtree(_STATE_DIR, ignore_errors=True)


# different sizes so the probe can tell the holes apart (see layout_is_symmetric)
TRUE = [(10.20, 3.10, 0.5), (10.55, 27.05, 0.625)]
PARKS = [(10.24, 3.05, 0.1), (10.51, 27.09, 0.1)]      # within 0.06 in of center
DESIGN = [(12.0, 1.0, 0.5), (12.0, 29.0, 0.625)]        # on the X centerline of the 24 x 30 fixture


# =====================================================================================
class TestProbeRoutineStatic(unittest.TestCase):
    """Lint vd_probe.sbp against the documented OpenSBP rules (all lines, not just executed ones)."""

    def setUp(self):
        self.lines = PROBE.splitlines()
        self.vm = VM(PROBE, Machine(holes=[]), UI())   # builds the label table, rejects duplicates

    def test_jump_targets_exist(self):
        for ln in self.lines:
            code = strip_comment(ln)
            for m in re.finditer(r"\b(GOTO|GOSUB)\s+([A-Za-z_][A-Za-z0-9_]*)", code, re.I):
                self.assertIn(m.group(2).upper(), self.vm.labels, ln)

    def test_msgbox_and_input_text_rules(self):
        for ln in self.lines:
            code = strip_comment(ln).strip()
            if code.upper().startswith("MSGBOX"):
                m = re.match(r"^MSGBOX\s*\((.*)\)$", code)
                self.assertIsNotNone(m, f"MSGBOX line broken (apostrophe?): {ln}")
                parts = m.group(1).split(",")
                self.assertEqual(len(parts), 3, f"comma inside MSGBOX body: {ln}")
                self.assertNotRegex(parts[0], r"[()'\":]", ln)
            if code.upper().startswith("INPUT"):
                prompt = re.match(r'^INPUT\s+"([^"]*)"', code).group(1)
                self.assertNotIn(",", prompt)

    def test_only_documented_then_actions(self):
        for ln in self.lines:
            code = strip_comment(ln).strip()
            m = re.match(r"^IF\s+(.*?)\s+THEN\s+(.*)$", code, re.I)
            if m:
                act = m.group(2).split()[0].upper()
                self.assertTrue(act in ("GOTO", "GOSUB") or act.startswith("&") or act == "C7",
                                f"undocumented THEN action: {ln}")
                self.assertNotRegex(m.group(1), r"(?i)\b(AND|OR)\b", "one test per IF: " + ln)

    def test_every_variable_is_assigned(self):
        assigned = {"&MSGANSWER"}
        for ln in self.lines:
            code = strip_comment(ln)
            for m in re.finditer(r"(&[A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)", code):
                assigned.add(m.group(1).upper())
            mi = re.match(r'^\s*INPUT\s+"[^"]*"\s*(.*)$', code)
            if mi:
                assigned.update(v.strip().upper() for v in mi.group(1).split(","))
        for ln in self.lines:
            for m in re.finditer(r"&[A-Za-z_][A-Za-z0-9_]*", strip_comment(ln)):
                self.assertIn(m.group(0).upper(), assigned, f"never assigned: {m.group(0)} in {ln}")

    def test_every_guarded_move_is_preceded_by_an_idle_check(self):
        # ON INP only fires on a CHANGE, so each arm must follow a 'reads idle' check
        for i, ln in enumerate(self.lines):
            if re.match(r"^\s*ON INP\(.*\)\s*GOSUB", ln):
                if self.lines[i + 1].strip() == "IF &vd_hit = 0 THEN GOTO vd_touch_go":
                    # the graze re-arm: the input was just read idle (a held trip would have gone to
                    # vd_touch_held), and the move runs only if nothing fired meanwhile
                    self.assertIn("IF %(&vd_idx) = &vd_trig THEN GOTO vd_touch_held",
                                  [x.strip() for x in self.lines[i - 6:i]])
                    continue
                self.assertRegex(self.lines[i - 1], r"IF %\(&vd_idx\) = &vd_trig THEN GOTO vd_err_pretrig")
                # wait a (whole) second only if the probe does not read idle yet
                self.assertRegex(self.lines[i - 2], r"^vd_\w+_arm:$")
                self.assertEqual(self.lines[i - 3].strip(), "PAUSE 1")
                self.assertRegex(self.lines[i - 4], r"IF %\(&vd_idx\) = &vd_idle THEN GOTO vd_\w+_arm")
                # a trigger between arming and the move must stop BEFORE the move runs unguarded
                self.assertRegex(self.lines[i + 1], r"IF &vd_hit = 1 THEN GOTO vd_err_arm")
                nxt = [x for x in self.lines[i + 2:i + 5] if not re.match(r"^(vd_\w+:|&vd_graze = 0)\s*$", x)][0]
                self.assertRegex(nxt, r"^(M2|MZ),")                  # a label or the graze counter may sit between


# =====================================================================================
class TestSelftestFile(unittest.TestCase):
    """vd_selftest.sbp: offline check the user runs in SB3 Preview mode."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "vd_selftest.sbp"), encoding="latin-1") as fh:
            cls.text = fh.read()

    def test_lint(self):
        vm = VM(self.text, Machine(holes=[]), UI())
        assigned = {"&MSGANSWER"}
        for ln in self.text.splitlines():
            code = strip_comment(ln).strip()
            for m in re.finditer(r"\b(GOTO|GOSUB)\s+([A-Za-z_][A-Za-z0-9_]*)", code, re.I):
                self.assertIn(m.group(2).upper(), vm.labels, ln)
            if code.upper().startswith("MSGBOX"):
                m = re.match(r"^MSGBOX\s*\((.*)\)$", code)
                self.assertIsNotNone(m, ln)
                parts = m.group(1).split(",")
                self.assertEqual(len(parts), 3, ln)
                self.assertNotRegex(parts[0], r"[()'\":]", ln)
            for m in re.finditer(r"(&[A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)", code):
                assigned.add(m.group(1).upper())
            mi = re.match(r'^INPUT\s+"([^"]*)"\s*(.*)$', code)
            if mi:
                self.assertNotIn(",", mi.group(1))
                assigned.update(v.strip().upper() for v in mi.group(2).split(","))
            self.assertNotRegex(code, r"^(M[2-5XYZ]|J[2-5XYZ]|CG|CR|CC|CP|MH|JH|SK|C\d)\b", "no motion: " + ln)
        for ln in self.text.splitlines():
            for m in re.finditer(r"&[A-Za-z_][A-Za-z0-9_]*", strip_comment(ln)):
                self.assertIn(m.group(0).upper(), assigned, ln)

    def test_runs_and_records_everything(self):
        m = Machine(holes=[], mode=1)
        ui = UI(sim_inputs=[(1.5, -2.0, 0.6)],
                answers={"click No": ["No"], "PREVIEW mode right now": ["Yes"]})
        vm = TrackingVM(self.text, m, ui, numfmt=vb_single).run()
        self.assertEqual(vm.ended, "END")
        self.assertEqual(m.moves, 0)
        out = vm.closed[1][1]
        keys = [ln.split(",")[0] for ln in out]
        for k in ("OPEN_RELATIVE", "OPEN_LITERAL_ABSOLUTE", "OPEN_VARIABLE_ABSOLUTE", "SV1", "SV22", "SV61", "SV62", "SV66", "SV67", "SV146", "SV147", "STR", "NUM", "IND", "IF",
                  "IFGOSUB", "DEPTH", "ONINP", "MB1", "MB2", "PREVIEW_ANSWER", "INPUT", "PAUSE", "DX",
                  "MODE22_IS_1", "PROP66_GT", "SHELL_A", "END"):
            self.assertIn(k, keys)
        self.assertIn("DEPTH, 6", out)
        self.assertIn("MB2,No, 1", out)
        self.assertIn("IFGOSUB, 1", out)
        self.assertEqual(vm.max_depth, 6)
        # 11: the launcher exactly as vd_probe.sbp starts it
        self.assertEqual([c[0] for c in vm.shell], ["C:\\SbParts\\VirtualDowel\\venv\\Scripts\\python.exe C:\\SbParts\\VirtualDowel\\vd_align.py newsession"])
        assert_shell_proof(self, vm.shell[0][0])
        self.assertTrue(all(c[1] == ["1", "SYNC"] for c in vm.shell), vm.shell)
        self.assertIn("SHELL_A,C:\\SbParts\\VirtualDowel\\venv\\Scripts\\python.exe C:\\SbParts\\VirtualDowel\\vd_align.py newsession", out)
        self.assertEqual(vm.closed[1][0], "vd_selftest_result.txt")
        self.assertEqual(sorted(vm.closed), [1, 2, 3])

    def test_every_pause_is_whole_seconds(self):
        # a PAUSE under 1 s stopped the file on the user's SB3 until it was continued by hand
        for f in ("vd_probe.sbp", "vd_selftest.sbp"):
            with open(os.path.join(ROOT, f)) as fh:
                for ln in fh.read().splitlines():
                    m = re.match(r"^\s*PAUSE\b\s*(.*)$", strip_comment(ln), re.I)
                    if m and m.group(1).strip():
                        v = m.group(1).strip()
                        self.assertRegex(v, r"^[1-9]\d*$", f"{f}: {ln}")

    def test_installer_variables_are_not_reused(self):
        for f, names in (("vd_probe.sbp", ("vd_out", "vd_pyexe", "vd_script")),
                         ("vd_selftest.sbp", ("st_out", "st_pyexe", "st_script"))):
            text = open(os.path.join(ROOT, f)).read()
            for n in names:
                self.assertEqual(len(re.findall(rf"^\s*&{n}\s*=", text, re.M | re.I)), 1, (f, n))

    def test_no_operator_characters_in_quoted_text(self):
        for f in ("vd_probe.sbp", "vd_selftest.sbp"):
            with open(os.path.join(ROOT, f)) as fh:
                self.assertEqual(risky_string_lines(fh.read()), [], f)

    def test_open_line_numbers_match_the_header(self):
        lines = self.text.splitlines()
        self.assertTrue(lines[18].startswith('OPEN "vd_selftest_result.txt"'))
        self.assertTrue(lines[21].startswith('OPEN "C:\\SbParts\\VirtualDowel\\vd_selftest_literal.txt"'))
        self.assertTrue(lines[26].startswith("OPEN &st_out"))


# =====================================================================================
class TestDoubleClick(unittest.TestCase):
    """Double-clicking vd_app.py on the shop PC must open the prepare window, not flash a console and quit.
    It runs vd_align.py's entry; --help proves the hand-over without opening a window."""

    def test_vd_app_run_as_a_script_hands_over_to_vd_align(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "vd_app.py"), "--help"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Virtual Dowel", r.stdout)


class TestShopZip(unittest.TestCase):
    """make_zip.py: INSTALL.bat, README, LICENSE and docs at the top, the program in app/, nothing else."""

    def test_zip_layout_and_install_bat(self):
        import make_zip
        out = os.path.join(tempfile.mkdtemp(), "vd.zip")
        make_zip.build(out)
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            with open(os.path.join(ROOT, "vd_probe.sbp"), "rb") as fh:
                self.assertEqual(z.read("VirtualDowel/app/vd_probe.sbp"), fh.read())     # byte for byte
        for n in ("VirtualDowel/INSTALL.bat", "VirtualDowel/README.md", "VirtualDowel/LICENSE.txt",
                  "VirtualDowel/docs/guide.html", "VirtualDowel/docs/example/sideB_example.sbp",
                  "VirtualDowel/app/vd_align.py", "VirtualDowel/app/vd_app.py", "VirtualDowel/app/vd_probe.sbp",
                  "VirtualDowel/app/vd_selftest.sbp", "VirtualDowel/app/vd_align.bat"):
            self.assertIn(n, names)
        self.assertEqual(len(names), 11)
        self.assertFalse([n for n in names if "/tests/" in n or "AGENTS" in n or "thick-stock-plan" in n])
        with open(os.path.join(ROOT, "INSTALL.bat"), "rb") as fh:
            text = fh.read()
        self.assertIn(b'call "%APP%\\vd_align.bat" --install', text)
        self.assertIn(b"C80", text)
        self.assertEqual(text.count(b"\r\n"), text.count(b"\n"))                 # CRLF, SB3 style

    def test_guide_is_next_to_the_code_or_one_level_up(self):
        self.assertEqual(va.guide_path(), os.path.join(ROOT, "docs", "guide.html"))
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "docs"))
        os.makedirs(os.path.join(tmp, "app"))
        open(os.path.join(tmp, "docs", "guide.html"), "w").close()
        here = va.HERE
        try:
            va.HERE = os.path.join(tmp, "app")                    # the shop zip layout
            self.assertEqual(va.guide_path(), os.path.join(tmp, "docs", "guide.html"))
            va.HERE = os.path.join(tmp, "else", "where")           # no docs here or one level up
            self.assertIsNone(va.guide_path())
        finally:
            va.HERE = here


class TestInstall(unittest.TestCase):
    """INSTALL.bat -> app\\vd_align.bat --install -> vd_align.py --install: point the SB3 files at wherever the folder lives."""

    def copy_to(self, folder):
        os.makedirs(folder, exist_ok=True)
        for f in ("vd_probe.sbp", "vd_selftest.sbp"):
            shutil.copy(os.path.join(ROOT, f), folder)

    def test_paths_are_rewritten_in_place(self):
        tmp = tempfile.mkdtemp()
        try:
            folder = os.path.join(tmp, "Shop", "VirtualDowel")
            self.copy_to(folder)
            before = {f: open(os.path.join(folder, f), newline="").read() for f in ("vd_probe.sbp", "vd_selftest.sbp")}
            shutil.copy(os.path.join(ROOT, "vd_align.py"), folder)
            custom = os.path.join(tmp, "SbParts", "Custom")
            os.makedirs(custom)
            with open(os.path.join(custom, "Custom80.sbc"), "w", newline="") as fh:   # someone else's custom cut
                fh.write("'my own C80\r\nJZ,1\r\n")
            changes = va.install_paths(folder, custom_dir=custom)
            self.assertEqual(len([c for c in changes if c.startswith(("vd_probe.sbp:", "vd_selftest.sbp:"))]), 7)
            for f, old in before.items():
                new = open(os.path.join(folder, f), newline="").read()
                self.assertEqual(new.count("\r\n"), old.count("\r\n"))          # same lines, still CRLF
                self.assertIn(folder, new)
                code = [l for l in new.splitlines() if not l.startswith("'")]
                self.assertFalse([l for l in code if "SbParts" in l], f)       # no hard-coded path left
            probe = open(os.path.join(folder, "vd_probe.sbp")).read()
            self.assertIn(f'&vd_out = "{folder}/vd_probe_data.csv"', probe)
            py = f"{folder}/venv/bin/python"                                 # Scripts\\python.exe on Windows
            self.assertIn(f'&vd_pyexe = "{py}"', probe)
            self.assertIn(f'&vd_script = "{folder}/vd_align.py"', probe)
            st = open(os.path.join(folder, "vd_selftest.sbp")).read().splitlines()
            self.assertEqual(st[21], f'OPEN "{folder}/vd_selftest_literal.txt" FOR OUTPUT AS #2')
            self.assertTrue(st[18].startswith('OPEN "vd_selftest_result.txt"'))
            self.assertTrue(st[26].startswith("OPEN &st_out"))
            # the patched routine still runs end to end and SHELLs the patched launcher
            m = Machine(holes=TRUE)
            vm = VM(probe, m, UI(parks=list(PARKS)), numfmt=vb_single).run()
            self.assertEqual([c[0] for c in vm.shell], [f"{py} {folder}/vd_align.py newsession", f"{py} {folder}/vd_align.py align"])
            # run the exact command SB3 would run: the new-session call must reach Python and leave vd_state.json
            r = subprocess.run(vm.shell[0][0].split(" "), capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.path.exists(os.path.join(folder, "vd_state.json")))
            self.assertIn("args ['newsession']", open(os.path.join(folder, "vd_launch.log")).read())
            vm = TrackingVM("\n".join(st), Machine(holes=[], mode=1),
                            UI(sim_inputs=[(1.5, -2.0, 0.6)], answers={"click No": ["No"], "PREVIEW mode right now": ["Yes"]}),
                            numfmt=vb_single).run()
            self.assertEqual(vm.shell[0][0], f"{py} {folder}/vd_align.py newsession")
            # the start file and the custom cut: open the window, then run what it prepared
            start = open(os.path.join(folder, "vd_start.sbp"), newline="").read()
            self.assertEqual(start.count("\r\n"), len(start.splitlines()))
            self.assertIn(f'&vd_pyexe = "{py}"', start)
            self.assertIn('&vd_cmd = &vd_pyexe & " " & &vd_script & " prepare"', start)
            self.assertTrue(start.rstrip().endswith(f"FP,{folder}/vd_run.sbp"))
            self.assertEqual(risky_string_lines(start.replace(folder, "C:\\VD").replace("/", "\\")), [])  # Linux paths have /
            self.assertIn("END", open(os.path.join(folder, "vd_run.sbp")).read())    # nothing prepared yet
            self.assertEqual(open(os.path.join(custom, "Custom80.sbc"), newline="").read(), "'my own C80\r\nJZ,1\r\n")
            cc = open(os.path.join(custom, "Custom81.sbc"), newline="").read()
            self.assertTrue(cc.startswith(va.CUSTOM_CUT_MARK))
            self.assertIn(f"FP,{folder}/vd_run.sbp", cc)
            self.assertTrue(any("custom cut C81" in c for c in changes), changes)
            vm = VM(start, Machine(holes=[]), UI(), numfmt=vb_single)
            vm.run()
            self.assertEqual(vm.shell[0][0], f"{py} {folder}/vd_align.py prepare")
            os.remove(os.path.join(custom, "Custom80.sbc"))                   # C80 is free now
            changes = va.install_paths(folder, custom_dir=custom)            # running twice is harmless
            self.assertTrue(any("custom cut C81" in c for c in changes), changes)   # and keeps the number in use
            self.assertFalse(os.path.exists(os.path.join(custom, "Custom80.sbc")))
            os.rename(os.path.join(custom, "Custom81.sbc"), os.path.join(custom, "Custom83.sbc"))
            line = va.install_custom_cut(va.start_file_text("p", "s", "r", custom=True), custom)
            self.assertIn("custom cut C83", line)
        finally:
            shutil.rmtree(tmp)

    def test_start_file_empties_the_run_file_before_starting_python(self):
        # If SB3 cannot start Python (or the window dies before it writes anything), the FP that
        # follows must not run the probing file prepared for an earlier job.
        run = "C:\\VD\\vd_run.sbp"
        for custom in (False, True):
            text = va.start_file_text("C:\\VD\\venv\\Scripts\\pythonw.exe", "C:\\VD\\vd_align.py", run, custom)
            lines = text.splitlines()
            self.assertLess(lines.index(f'OPEN "{run}" FOR OUTPUT AS #1'), lines.index("SHELL, &vd_cmd, 1, SYNC"))
            vm = VM(text, Machine(holes=[]), UI(), numfmt=vb_single).run()
            self.assertEqual(vm.closed[1], (run, []))          # truncated; nothing written into it
            self.assertEqual(vm.fp, [run])
            self.assertEqual(risky_string_lines(text), [])
            self.assertEqual(text.count("\r\n"), len(lines))

    def test_paths_shopbot_cannot_handle_are_refused(self):
        tmp = tempfile.mkdtemp()
        try:
            for bad in ("Virtual Dowel", "Nik's stuff", "a&b", "VIRTUA~1", "Virtual-Dowel", "a+b", "x(1)"):
                folder = os.path.join(tmp, bad)
                self.copy_to(folder)
                with self.assertRaises(va.VDError):
                    va.install_paths(folder)
        finally:
            shutil.rmtree(tmp)

    def test_windows_path_rule(self):
        # the user's install: the 8.3 short path lost its "~" in SB3's SHELL
        self.assertFalse(va.shell_safe_path("C:\\Users\\ITPSTU~1\\DOWNLO~1\\VIRTUA~1"))
        self.assertTrue(va.shell_safe_path("C:\\SbParts\\VirtualDowel"))
        self.assertTrue(va.shell_safe_path("C:\\Users\\ITPStudent\\Downloads\\VirtualDowel"))
        for bad in ("C:\\Program Files\\VD", "C:\\Users\\a-b\\VD", "C:\\x/y", "C:\\a+b"):
            self.assertFalse(va.shell_safe_path(bad), bad)

    def test_data_file_lives_next_to_the_script(self):
        # vd_probe.sbp starts the script without a data argument; the script reads its own folder
        text = open(os.path.join(ROOT, "vd_probe.sbp")).read()
        out = re.search(r'^&vd_out = "(.*)"', text, re.M).group(1)
        script = re.search(r'^&vd_script = "(.*)"', text, re.M).group(1)
        self.assertEqual(out.rsplit("\\", 1)[0], script.rsplit("\\", 1)[0])
        self.assertEqual(out.rsplit("\\", 1)[1], os.path.basename(va.DEFAULT_DATA))


# =====================================================================================
class TestProbeRoutineRuns(unittest.TestCase):

    def check_centers(self, text, truth, tol, ecc=(0.0, 0.0)):
        pd = va.parse_probe_data(text)
        holes, offset, _ = va.compute_holes(pd)
        for h, (cx, cy, _) in zip(holes, truth):
            self.assertAlmostEqual(h.center[0], cx - ecc[0], delta=tol)
            self.assertAlmostEqual(h.center[1], cy - ecc[1], delta=tol)
        return pd, holes, offset

    def test_two_holes_complete_run(self):
        vm, m, ui = run_probe(TRUE, list(PARKS))
        self.assertEqual(vm.ended, "END")
        self.assertEqual(len(vm.shell), 2)
        self.assertEqual(vm.shell[0][0], "C:\\SbParts\\VirtualDowel\\venv\\Scripts\\python.exe C:\\SbParts\\VirtualDowel\\vd_align.py newsession")
        self.assertEqual(len(align_calls(vm)), 1)
        # no arguments: the script reads vd_probe_data.csv from its own folder
        self.assertEqual(vm.shell[-1][0], "C:\\SbParts\\VirtualDowel\\venv\\Scripts\\python.exe C:\\SbParts\\VirtualDowel\\vd_align.py align")
        for c in vm.shell:
            assert_shell_proof(self, c[0])
        self.assertEqual(vm.shell[-1][1][-1].upper(), "SYNC")
        self.assertEqual(m.collisions, [])
        self.assertLess(m.max_pen, 0.02)
        self.assertLessEqual(vm.max_depth, 5)
        pd, holes, offset = self.check_centers(data_text(vm), TRUE, 0.0006)
        self.assertEqual(pd.mode, 0)
        # %(1)/%(2) are rounded to 0.001 by SB3; the mean offset averages that out
        self.assertAlmostEqual(offset[0], 3.25, delta=0.0002)
        self.assertAlmostEqual(offset[1], -1.5, delta=0.0002)
        for h, (_, _, d) in zip(holes, TRUE):
            self.assertAlmostEqual(h.dia_x, d, delta=0.015)
            self.assertLess(h.repeat, 0.001)
        self.assertGreater(m.z, 0)

    def test_sb3_rounded_working_location_does_not_limit_precision(self):
        # User's SB3 (selftest 2026-09-28): %(1)/%(2) come back rounded to 0.001, %(6)/%(7) full precision.
        # Centres are computed from %(6)/%(7) + the constant offset, so they stay exact.
        m = Machine(holes=TRUE, jitter=0.0)
        vm = VM(configure(PROBE), m, UI(parks=list(PARKS)), numfmt=sb3_number).run()
        text = data_text(vm)
        pd = va.parse_probe_data(text)
        self.assertTrue(all(abs(t.x * 1000 - round(t.x * 1000)) < 1e-6 for t in pd.touches))  # rounded
        holes, offset, spread = va.compute_holes(pd)
        for h, (cx, cy, _) in zip(holes, TRUE):
            self.assertLess(math.hypot(h.center[0] - cx, h.center[1] - cy), 0.0001)
        self.assertAlmostEqual(offset[0], 3.25, delta=0.0002)
        fails, warns = va.hole_checks(pd, holes, spread, va.DEFAULTS)
        self.assertEqual(fails, [])
        self.assertFalse(any("offset changed" in w for w in warns), warns)
        # SB3 style numbers parse: '.5', '-0.000123', '22.460'
        self.assertIn(".", text)

    def test_plain_number_format(self):
        vm, m, _ = run_probe(TRUE, list(PARKS), numfmt=repr)
        self.check_centers(data_text(vm), TRUE, 0.0006)

    def test_spin180_cancels_probe_eccentricity(self):
        ecc = (0.004, -0.003)
        m = Machine(holes=TRUE, ecc=ecc)
        ui = UI(parks=list(PARKS))
        ui.on_spin = lambda mc: setattr(mc, "ecc", (-mc.ecc[0], -mc.ecc[1]))
        vm = VM(configure(PROBE, spin180=1), m, ui, numfmt=vb_single).run()
        self.assertEqual(m.collisions, [])
        pd, holes, _ = self.check_centers(data_text(vm), TRUE, 0.0006)
        for h in holes:
            # orientation 0 had +ecc for EVERY hole: the label follows the physical spindle state
            self.assertAlmostEqual(h.ecc[0], ecc[0], delta=0.0005)
            self.assertAlmostEqual(h.ecc[1], ecc[1], delta=0.0005)
        fails, warns = va.hole_checks(pd, holes, (0.0, 0.0), va.DEFAULTS)
        self.assertEqual(fails, [])
        self.assertFalse(any("disagrees" in w for w in warns), warns)
        # without the 180 run the same eccentricity shifts every center by ecc
        m2 = Machine(holes=TRUE, ecc=ecc)
        vm2 = VM(configure(PROBE), m2, UI(parks=list(PARKS)), numfmt=vb_single).run()
        self.check_centers(data_text(vm2), TRUE, 0.0006, ecc=ecc)

    def test_unplugged_probe_stops_before_any_motion(self):
        m = Machine(holes=TRUE)
        m.unplugged = True
        ui = UI(parks=list(PARKS))
        vm = VM(configure(PROBE), m, ui).run()
        self.assertEqual(m.moves, 0)
        self.assertTrue(any("unplugged" in s for s in ui.messages))
        with self.assertRaises(va.ProbeDataError):        # old data was truncated, new data incomplete
            va.parse_probe_data(data_text(vm))

    def test_finger_test_fails_on_wrong_input(self):
        m = Machine(holes=TRUE)
        ui = UI(parks=list(PARKS))
        VM(configure(PROBE, pin=2), m, ui).run()
        self.assertEqual(m.moves, 0)
        self.assertTrue(any("did not trigger" in s for s in ui.messages))
        self.assertGreaterEqual(m.time, 19.0)

    def test_parked_off_the_hole_recovers(self):
        parks = [(10.9, 3.1, 0.1)] + list(PARKS)      # first park is on solid material
        vm, m, ui = run_probe(TRUE, parks)
        self.assertEqual(m.collisions, [])
        self.assertTrue(any("touched something while lowering" in s for s in ui.messages))
        self.check_centers(data_text(vm), TRUE, 0.0006)
        self.assertEqual(len(align_calls(vm)), 1)

    def test_hole_larger_than_setting_aborts_and_lifts_out(self):
        big = [(10.20, 3.10, 1.0), (10.55, 27.05, 1.0)]
        vm, m, ui = run_probe(big, list(PARKS))
        self.assertEqual(m.collisions, [])
        self.assertTrue(any("No contact" in s for s in ui.messages))
        self.assertEqual(align_calls(vm), [])
        self.assertAlmostEqual(m.z, 0.1, places=6)       # lifted back to the park height
        with self.assertRaises(va.ProbeDataError):
            va.parse_probe_data(data_text(vm))

    def test_a_trip_that_clears_again_is_a_graze_and_the_search_goes_on(self):
        # the user's probe (2026-10-15): the switch opens at a graze and closes again once the machine
        # stops; a little more push gives a full switch. v2.6 pushes on instead of aborting.
        vm, m, ui = run_probe(TRUE, list(PARKS), machine_kw={"hold": 0.0025})
        self.assertEqual(vm.ended, "END")
        self.assertEqual(len(align_calls(vm)), 1)
        self.assertEqual(m.collisions, [])
        self.assertLess(m.max_pen, 0.02)
        pd, holes, _ = self.check_centers(data_text(vm), TRUE, 0.0015)
        for h in holes:
            self.assertLess(h.repeat, 0.002)
        self.assertNotIn("ERR,", data_text(vm))
        # a switch that never holds (a pulse output): five grazes on one wall, then its own message
        vm, m, ui = run_probe(TRUE, list(PARKS), machine_kw={"hold": 1.0})
        self.assertTrue(any("without staying tripped" in s for s in ui.messages), ui.messages)
        self.assertEqual(align_calls(vm), [])
        self.assertGreater(m.z, 0.0)                                         # lifted out
        text = data_text(vm)
        self.assertRegex(text, r"ERR, ?5, ?1, ?1, ?1,")
        with self.assertRaises(va.ProbeDataError) as cm:
            va.parse_probe_data(text)
        self.assertIn("tripped 5 times", str(cm.exception))
        self.assertIn("hole 1, X+", str(cm.exception))

    def test_a_trip_at_the_very_end_of_the_search_has_its_own_message(self):
        # hole 2 (0.625, the largest: r + margin = 0.33) parked 0.075 in off centre: the far wall sits in
        # the last 0.002 in of the search
        far = (TRUE[1][0] - 0.075, TRUE[1][1], 0.1)
        vm, m, ui = run_probe(TRUE, [PARKS[0], far])
        self.assertTrue(any("only at the very end of the search" in s for s in ui.messages), ui.messages)
        self.assertEqual(m.collisions, [])
        text = data_text(vm)
        self.assertRegex(text, r"ERR, ?6, ?2, ?1, ?1,")
        with self.assertRaises(va.ProbeDataError) as cm:
            va.parse_probe_data(text)
        self.assertIn("very end", str(cm.exception))
        # the plain miss writes its line too
        vm, m, ui = run_probe(TRUE, [PARKS[0], (TRUE[1][0] - 0.1, TRUE[1][1], 0.1)])
        self.assertTrue(any("No contact within the search distance" in s for s in ui.messages), ui.messages)
        self.assertRegex(data_text(vm), r"ERR, ?1, ?2, ?1, ?1,")
        self.assertIn("no contact within the search distance", str(va.err_text(["1", "1", "1", "1", ".2675", ".2675"])))

    def test_false_trigger_is_not_accepted_as_a_touch(self):
        # the switch bounces as the 3rd guarded move is armed: that move then runs unguarded
        for n_ok in (0, 1, 2, 5, 9):
            m = Machine(holes=TRUE)
            ui = UI(parks=list(PARKS))
            text = configure(PROBE)
            vm = VM(text, m, ui, numfmt=vb_single)
            orig = vm.statement
            count = {"arms": 0}

            def statement(code, pc, orig=orig, count=count, m=m, n_ok=n_ok):
                if re.match(r"^ON INP\(.*\)\s*GOSUB", code):
                    count["arms"] += 1
                    if count["arms"] == n_ok + 1:
                        m.spurious_arms = 1
                return orig(code, pc)
            vm.statement = statement
            vm.run()
            self.assertTrue(any("changed the moment the search was armed" in s for s in ui.messages), (n_ok, ui.messages))
            self.assertEqual(m.collisions, [])
            self.assertLess(m.max_pen, 0.02)          # nothing ran unguarded
            self.assertEqual(align_calls(vm), [])
            self.assertGreater(m.z, 0.0)              # lifted out (or never went in)
            with self.assertRaises(va.ProbeDataError):
                va.parse_probe_data(data_text(vm))

    def test_cancel_at_start_leaves_no_usable_data(self):
        m = Machine(holes=TRUE)
        ui = UI(parks=list(PARKS), answers={"VIRTUAL DOWEL probing": ["Cancel"]})
        vm = VM(configure(PROBE), m, ui).run()
        self.assertEqual(m.moves, 0)
        self.assertEqual(vm.shell, [])                        # cancelling does not delete aligned files
        self.assertIn(1, vm.closed)
        with self.assertRaises(va.ProbeDataError):
            va.parse_probe_data(data_text(vm))

    def test_no_then_yes_rejogs_and_cancel_aborts(self):
        parks = [(10.9, 3.1, 0.1)] + list(PARKS)
        vm, m, ui = run_probe(TRUE, parks, ui_kw={"answers": {"Is the probe tip over": ["No"]}})
        self.assertEqual(len(align_calls(vm)), 1)
        self.check_centers(data_text(vm), TRUE, 0.0006)
        vm, m, ui = run_probe(TRUE, list(PARKS), ui_kw={"answers": {"Is the probe tip over": ["Yes", "Cancel"]}})
        self.assertEqual(align_calls(vm), [])
        self.assertTrue(any("aborted" in s for s in ui.messages))
        self.assertIn(1, vm.closed)

    def test_preview_mode_refuses_real_probing(self):
        m = Machine(holes=TRUE, mode=1)
        ui = UI(parks=list(PARKS))
        vm = VM(configure(PROBE), m, ui).run()
        self.assertEqual(m.moves, 0)
        self.assertEqual(vm.files, {})
        self.assertEqual(vm.shell, [])
        self.assertTrue(any("PREVIEW" in s for s in ui.messages))

    def test_simulation_never_moves_the_machine(self):
        for mode in (0, 1):
            text = configure(PROBE, sim=1)
            m = Machine(holes=[], mode=mode)
            ui = UI(sim_inputs=[(10.0, 3.0, 0.5), (10.5, 27.0, 0.625)])
            vm = VM(text, m, ui, numfmt=vb_single).run()
            self.assertEqual(m.moves, 0)
            self.assertEqual(len(vm.shell), 1)                # no session cleanup in SIM mode
            self.assertEqual(len(align_calls(vm)), 1)
            pd = va.parse_probe_data(data_text(vm))
            self.assertTrue(pd.sim)
            holes, _, _ = va.compute_holes(pd)
            self.assertAlmostEqual(holes[0].center[0], 10.02, delta=1e-5)
            self.assertAlmostEqual(holes[0].center[1], 2.988, delta=1e-5)
            self.assertAlmostEqual(holes[1].dia_x, 0.625, delta=1e-5)

    def test_parking_off_centre_in_y_is_tolerated(self):
        # the coarse pass keeps the full search distance, so parking 0.08-0.1 in off in Y on the
        # smaller hole still works (the X chord measured off-centre is short and must not shrink it)
        for dy in (0.08, 0.10):
            parks = [(10.20, 3.10 + dy, 0.1), (10.55, 27.05, 0.1)]
            vm, m, ui = run_probe(TRUE, parks)
            self.assertEqual(len(align_calls(vm)), 1, (dy, ui.messages[-2:]))
            self.assertEqual(m.collisions, [])
            self.check_centers(data_text(vm), TRUE, 0.0006)

    def test_metric_machine(self):
        mm = [(x * 25.4, y * 25.4, d * 25.4) for x, y, d in TRUE]
        parks = [(x * 25.4, y * 25.4, 2.5) for x, y, _ in PARKS]
        vm, m, _ = run_probe(mm, parks, machine_kw=dict(units=1, tip=3.175, pretravel=0.1, jitter=0.005,
                                                        overtravel=3.0, base_offset=(80.0, -40.0)))
        self.assertEqual(m.collisions, [])
        pd, holes, _ = self.check_centers(data_text(vm), mm, 0.015)
        self.assertEqual(pd.units, 1)


# =====================================================================================
class TestFitMath(unittest.TestCase):

    def _holes(self, pts, dias=None):
        dias = dias or [0.5] * len(pts)
        return [va.HoleResult(i + 1, p, {0: p}, d, d, 0.0, 2) for i, (p, d) in enumerate(zip(pts, dias))]

    def test_fit_recovers_rigid_transform(self):
        design = [(1.0, 2.0), (20.0, 3.5), (11.0, 28.0), (2.0, 25.0)]
        for deg in (-3.0, -0.2, 0.0, 0.7, 4.0):
            meas = [T(deg, 5.1, -2.3, p) for p in design]
            f = va.fit_rigid(design, meas)
            self.assertAlmostEqual(math.degrees(f.theta), deg, places=9)
            self.assertAlmostEqual(f.tx, 5.1, places=9)
            self.assertAlmostEqual(f.ty, -2.3, places=9)
            self.assertLess(f.max_resid, 1e-9)

    def test_swapped_probe_order_is_matched_by_size(self):
        meas = [T(0.4, 3.0, 2.0, d[:2]) for d in DESIGN][::-1]
        al = va.align(DESIGN, self._holes(meas, [0.64, 0.515]), va.DEFAULTS, 1.0, 0.625)
        self.assertEqual(al.failures, [])
        self.assertEqual(al.perm, (1, 0))
        self.assertAlmostEqual(math.degrees(al.fit.theta), 0.4, places=6)
        self.assertAlmostEqual(al.size_bias, 0.015, places=6)

    def test_wrong_flip_direction_is_caught_by_hole_size(self):
        # board turned end-over-end: the 0.500 hole ends up where the 0.625 hole should be
        meas = [T(0.2, 1.0, 1.0, (12.0, 29.0)), T(0.2, 1.0, 1.0, (12.0, 1.0))]
        al = va.align(DESIGN, self._holes(meas, [0.51, 0.635]), va.DEFAULTS, 1.0, 0.625)
        self.assertTrue(any("rotation" in f for f in al.failures), al.failures)

    def test_symmetric_layout_fails_unless_allowed(self):
        same = [(12.0, 1.0, None), (12.0, 29.0, None)]
        meas = [T(0.2, 1.0, 1.0, d[:2]) for d in same]
        al = va.align(same, self._holes(meas, [0.635, 0.635]), va.DEFAULTS, 1.0, 0.625)
        self.assertTrue(any("half turn" in f for f in al.failures))
        th = dict(va.DEFAULTS, allow_symmetric_layout=1)
        al = va.align(same, self._holes(meas, [0.635, 0.635]), th, 1.0, 0.625)
        self.assertEqual(al.failures, [])
        self.assertTrue(any("half turn" in w for w in al.warnings))

    def test_symmetry_detection(self):
        self.assertTrue(va.layout_is_symmetric([(0, 0, None), (5, 7, None)], 0.04, 0.03))
        self.assertFalse(va.layout_is_symmetric([(0, 0, 0.5), (5, 7, 0.625)], 0.04, 0.03))
        rect = [(0, 0, None), (20, 0, None), (20, 10, None), (0, 10, None)]
        self.assertTrue(va.layout_is_symmetric(rect, 0.04, 0.03))
        self.assertFalse(va.layout_is_symmetric([(12, 1, None), (12, 29, None), (12, 4, None)], 0.04, 0.03))

    def test_four_hole_rectangle_noise_free_does_not_crash(self):
        rect = [(0.0, 0.0, 0.5), (20.0, 0.0, 0.625), (20.0, 10.0, 0.75), (0.0, 10.0, 0.875)]
        meas = [T(0.1, 2.0, 3.0, d[:2]) for d in rect]
        al = va.align(rect, self._holes(meas, [d[2] for d in rect]), va.DEFAULTS, 1.0, 0.875)
        self.assertEqual(al.failures, [])
        same = [(x, y, None) for x, y, _ in rect]
        al = va.align(same, self._holes(meas, [0.875] * 4), va.DEFAULTS, 1.0, 0.875)
        self.assertTrue(al.failures)

    def test_mirrored_coordinates_fail_with_three_holes(self):
        design = [(2.0, 1.0, None), (22.0, 1.0, None), (5.0, 20.0, None)]
        mirrored = [(24.0 - x, y) for x, y, _ in design]
        meas = [T(0.3, 1.0, 1.0, p) for p in mirrored]
        al = va.align(design, self._holes(meas, [0.63] * 3), va.DEFAULTS, 1.0, 0.625)
        self.assertTrue(al.failures)

    def test_spacing_error_fails(self):
        meas = [T(0.4, 3.0, 2.0, (12.0, 1.0)), T(0.4, 3.0, 2.0, (12.0, 29.05))]
        al = va.align(DESIGN, self._holes(meas, [0.5, 0.625]), va.DEFAULTS, 1.0, 0.625)
        self.assertTrue(any("spacing" in f for f in al.failures))

    def test_centre_fit_places_the_midpoint_and_reports_the_shrink(self):
        # 3D print: two equal bores 4 in apart, printed 1 percent small, turned 1.5 deg, moved by (5, 7)
        design = [(-2.0, 0.0, 0.5), (2.0, 0.0, 0.5)]
        meas = [T(1.5, 5.0, 7.0, (-1.98, 0.0)), T(1.5, 5.0, 7.0, (1.98, 0.0))]
        holes = self._holes(meas, [0.49, 0.49])
        al = va.align(design, holes, va.DEFAULTS, 1.0, 0.5, centre=True)
        self.assertEqual(al.failures, [])
        self.assertAlmostEqual(math.degrees(al.fit.theta), 1.5, places=6)
        self.assertAlmostEqual(al.fit.tx, 5.0, places=6)      # the design midpoint (0, 0) lands on the measured one
        self.assertAlmostEqual(al.fit.ty, 7.0, places=6)
        self.assertTrue(any("1.00 percent" in w and "closer" in w for w in al.warnings), al.warnings)
        self.assertTrue(any("same size" in w for w in al.warnings))
        # the same data FAILS the flip-milling fit: the spacing is 0.04 off
        rigid = va.align(design, holes, dict(va.DEFAULTS, allow_symmetric_layout=1), 1.0, 0.5)
        self.assertTrue(any("spacing" in f for f in rigid.failures))

    def test_centre_fit_tells_equal_references_apart_by_position(self):
        design = [(-2.0, 0.0, 0.5), (2.0, 0.0, 0.5)]
        meas = [T(1.0, 5.0, 7.0, (1.98, 0.0)), T(1.0, 5.0, 7.0, (-1.98, 0.0))]       # jogged to 2 first
        al = va.align(design, self._holes(meas, [0.5, 0.5]), va.DEFAULTS, 1.0, 0.5, centre=True)
        self.assertEqual(al.failures, [])
        self.assertEqual(al.perm, (1, 0))
        self.assertAlmostEqual(math.degrees(al.fit.theta), 1.0, places=6)
        self.assertTrue(any("half a turn" in w for w in al.warnings), al.warnings)
        # the blind spot: a print placed half a turn round looks the same - the warning says so
        meas = [T(179.0, 5.0, 7.0, (-1.98, 0.0)), T(179.0, 5.0, 7.0, (1.98, 0.0))]
        al = va.align(design, self._holes(meas, [0.5, 0.5]), va.DEFAULTS, 1.0, 0.5, centre=True)
        self.assertEqual((al.failures, al.perm), ([], (1, 0)))
        # two different sizes catch it
        al = va.align([(-2.0, 0.0, 0.5), (2.0, 0.0, 0.625)], self._holes(meas, [0.5, 0.625]), va.DEFAULTS, 1.0,
                      0.625, centre=True)
        self.assertTrue(any("rotation" in f for f in al.failures), al.failures)

    def test_centre_fit_needs_exactly_two_references(self):
        with self.assertRaises(va.VDError):
            va.align([(0, 0, 0.5), (4, 0, 0.5), (2, 3, 0.5)], self._holes([(0, 0), (4, 0), (2, 3)]), va.DEFAULTS,
                     1.0, 0.5, centre=True)

    def test_rotation_limit(self):
        meas = [T(7.0, 3.0, 2.0, d[:2]) for d in DESIGN]
        al = va.align(DESIGN, self._holes(meas, [0.5, 0.625]), va.DEFAULTS, 1.0, 0.625)
        self.assertTrue(any("rotation" in f for f in al.failures))

    def test_sizes_that_match_nothing_fail(self):
        meas = [T(0.1, 0, 0, d[:2]) for d in DESIGN]
        al = va.align(DESIGN, self._holes(meas, [0.75, 0.75]), va.DEFAULTS, 1.0, 0.625)
        self.assertTrue(any("do not match" in f for f in al.failures))

    def test_nearly_equal_sizes_count_as_symmetric(self):
        design = [(12.0, 1.0, 0.500), (12.0, 29.0, 0.530)]
        meas = [T(0.2, 1.0, 1.0, d[:2]) for d in design]
        al = va.align(design, self._holes(meas, [0.51, 0.54]), va.DEFAULTS, 1.0, 0.53)
        self.assertTrue(any("half turn" in f for f in al.failures))

    def test_partial_diameters_rejected(self):
        with self.assertRaises(va.VDError):
            va.align([(0, 0, 0.5), (5, 5, None)], self._holes([(0, 0), (5, 5)]), va.DEFAULTS, 1.0, 0.5)


# =====================================================================================
class TestTransformer(unittest.TestCase):

    def tf(self, text, deg=0.37, tx=1.234, ty=-0.567):
        tr = va.SbpTransformer(fit_of(deg, tx, ty))
        return "\n".join(tr.transform(va.split_lines(text))), tr

    def test_fixture_is_transformed_exactly(self):
        for deg, tx, ty in ((0.37, 1.234, -0.567), (0.0, 0.0, 0.0), (-2.5, -10.0, 4.0), (30.0, 3.0, 3.0)):
            out, tr = self.tf(JOB_TEXT, deg, tx, ty)
            worst = sim_sbp.compare(JOB_TEXT, out, math.radians(deg), tx, ty)
            self.assertLess(worst, 2e-5)
        self.assertEqual(len(tr.stats.custom_cuts), 5)
        self.assertEqual(tr.stats.file_units, 0)
        self.assertEqual(out.count("GOSUB VDALIGN_CHECK"), 5)
        self.assertNotRegex(out, r"(?m)^MX|^MY|^JX")
        self.assertIn("&PWMaterial * -0.5", out)
        self.assertIn("JZ,&PWSafeZ", out)

    def test_unchanged_lines_are_byte_identical(self):
        out, _ = self.tf(JOB_TEXT)
        src = JOB_TEXT.splitlines()
        o = [ln for ln in out.splitlines() if ln.strip() != "GOSUB VDALIGN_CHECK"]
        self.assertEqual(len(src), len(o))
        motion = re.compile(r"^\s*(M[2-5XY]|J[2-5XY]|CG)\b", re.I)
        for a, b in zip(src, o):
            if not motion.match(a):
                self.assertEqual(a, b)

    def test_parameter_separator_rules(self):
        out, _ = self.tf("SA\nJ2,1,1\nM2,, 4.2\nM2 , 5\nM2 3, 4.2\nM2, 7", 0.0, 0.0, 0.0)
        pts = [p for _, seg, _ in sim_sbp.sample(out) for p in seg[1:]]
        self.assertEqual([(round(x, 6), round(y, 6)) for x, y, _ in pts],
                         [(1, 1), (1, 4.2), (1, 5), (3, 4.2), (7, 4.2)])

    def assertRejected(self, text, fragment):
        with self.assertRaises(va.TransformError) as cm:
            self.tf(text)
        self.assertIn(fragment, str(cm.exception))

    def test_rejections(self):
        R = self.assertRejected
        R("SA\nJ2,0,0\nCR,5,5", "axis-aligned")
        R("SA\nJ2,0,0\nCC,2", "CC")
        R("SA\nFP,other.sbp", "part file")
        R("SA\nVA,0,0", "VA")
        R("SA\nVA,,,,,,3", "VA")
        R("SA\nZX", "re-zeroes X")
        R("SA\nZ2", "re-zeroes")
        R("SA\nG01 X1 Y2", "G-code")
        R("G0 X1", "G-code")
        R("SA\nT1 M06", "")
        R("SA\nM2,&x,1", "plain number")
        R("SA\nM3,1,(2+1),0", "plain number")
        R("SA\nMX,1", "position is unknown")
        R("SA\nJ2,1,1\nLBL:\nMY,2", "position is unknown")
        R("SA\nJ2,1,1\nC9\nM2,,2", "position is unknown")
        R("SA\nJ2,1,1\nGOSUB X\nM2,,2", "position is unknown")
        R("SA\nJ2,0,0\nCG, ,1,1,1,0,T,1,0,1,2,1", "ellipse")
        R("SA\nJ2,0,0\nCG, ,1,1, , ,T,1", "inherit")
        R("SA\nJ2,0,0\nCG, ,1,1,1,0,T,1\nL:\nCG, ,2,2, , ,T,1", "inherit")
        R("SA\nJ2,1,1\nJH\nM2,,2", "position is unknown")
        R("SA\nON INPUT(5,0) M2,1,1", "inside IF/ON")
        R("SR\nCG, ,1,1,1,0,T,1", "relative")
        R("SA\nIF &a = 1 THEN M2,1,2", "inside IF")
        R("SA\nIF &a = 1 THEN PAUSE", "operator prompts")
        R("SA\nON INP(1,1) GOSUB X", "ON INPUT")
        R("SA\nM2,1,2,3", "parameters")
        R("SA\nJ2,1,1\nMN", "nudge")
        R("SA\nVO,1", "offset")
        R("SA\nVD,,,1", "units")
        R("SA\nVU,1000", "calibration")
        R("SA\nJ2,1,1\nCG,2,1,1,,,T,1", "full circle")
        R("SA\nJ2,1,1\nCG,2, ,3,,,T,1", "explicit end point")
        R("SA\nJ2,0,0\nSR\nIF &a=1 THEN GOTO L2\nSA\nL2:\nM2,1,1", "relative mode")

    def test_sr_file_without_jumps_is_fine(self):
        out, _ = self.tf("SA\nJ2,0,0\nSR\nM2,1,0\nSA\nM2,5,5", 90.0, 0.0, 0.0)
        self.assertIn("M2,0.000000,1.000000", out)
        self.assertIn("M2,-5.000000,5.000000", out)

    def test_already_aligned_file_is_refused(self):
        out, _ = self.tf(JOB_TEXT)
        full = "\n".join(va.build_output(out.splitlines(), pdata(), fit_of(0.1, 0, 0), "x", (0.0, 0.0),
                                         va.DEFAULTS, "s"))
        with self.assertRaises(va.TransformError):
            self.tf(full)

    def test_allowed_non_motion_commands_and_check_insertion(self):
        txt = ("SA\nVA,,,0\nVA, ,,&z\nZZ\nMS,1,1\nVS,1,1\nTR,12000\nSO,1,1\nPAUSE 2\nIF %(25)=1 THEN GOTO E\n"
               "IF &x = 1 THEN C#,91\nC3\nJ2,1,1\nSK\nJ2,2,2\nPAUSE\n'change bit\nPAUSE UNTIL 5, 1\n"
               "MSGBOX(ok,0,t)\nINPUT \"n\" &n\nE:\nEND")
        out, tr = self.tf(txt)
        self.assertEqual(len(tr.stats.custom_cuts), 2)
        # custom cuts (2) + SK + PAUSE + PAUSE UNTIL + MSGBOX + INPUT
        self.assertEqual(out.count("GOSUB VDALIGN_CHECK"), 7)
        self.assertEqual(tr.stats.checks, 7)
        self.assertNotIn("PAUSE 2\nGOSUB", out)

    def test_relative_moves_need_a_known_position(self):
        # After JH the tool is at machine X0,Y0, not at the design origin's aligned position, so a
        # relative move from there would be cut offset by the translation. Same at the start of a
        # file, after SK, a custom cut or a label. A full circle with a blank end point is the same.
        R = self.assertRejected
        R("SR\nM2,1,0", "relative")
        R("SA\nJ2,5,5\nJH\nSR\nM2,1,0", "relative")
        R("SA\nJ2,5,5\nSK\nSR\nM2,1,0", "relative")
        R("SA\nJ2,5,5\nC9\nSR\nMX,1", "relative")
        R("SA\nJ2,0,0\nJH\nCG, , , ,1.5,0,T,1", "full circle")
        # relative moves from a known (aligned) position rotate without translation
        out, _ = self.tf("SA\nJ2,2,3\nSR\nM2,1,0", 90.0, 5.0, 5.0)
        self.assertIn("M2,0.000000,1.000000", out)
        out, _ = self.tf("SA\nJ2,2,3\nJH\nJ2,0,0\nCG, , , ,1.5,0,T,1", 90.0, 5.0, 5.0)
        self.assertIn("CG, , , ,0.000000,1.500000,T,1", out)

    def test_line_splitting_ignores_unicode_separators(self):
        text = "SA\n'Pocket\x85 J2 0,0\nJ2,1,1\n"
        self.assertEqual(len(va.split_lines(text)), 3)
        out, tr = self.tf(text)
        self.assertEqual(tr.stats.moves, 1)


# =====================================================================================
class TestRuntimeChecks(unittest.TestCase):
    """Execute the generated header/footer in the OpenSBP interpreter."""

    def run_header(self, pd, machine, offset=(3.25, -1.5), answers=None):
        lines = va.build_output(["'(job body)"], pd, fit_of(0.1, 0, 0), "job.sbp", offset,
                                va.DEFAULTS, "Hole 1", "2026-09-25 at 10.31")
        ui = UI(answers=answers)
        vm = VM("\n".join(lines), machine, ui).run()
        return vm, ui

    def test_passes_when_nothing_changed(self):
        vm, ui = self.run_header(pdata(), Machine(holes=[]))
        self.assertEqual(vm.ended, "END")
        self.assertFalse(any("Stopping" in s for s in ui.messages))

    def test_zero_changed(self):
        vm, ui = self.run_header(pdata(), Machine(holes=[], base_offset=(3.30, -1.5)))
        self.assertEqual(vm.ended, "ENDALL")
        self.assertTrue(any("zero has changed" in s for s in ui.messages))

    def test_units_changed(self):
        vm, ui = self.run_header(pdata(), Machine(holes=[], units=1))
        self.assertEqual(vm.ended, "ENDALL")
        self.assertTrue(any("Units changed" in s for s in ui.messages))

    def test_fp_proportion_or_offset(self):
        m = Machine(holes=[])
        m.file_prop = (2.0, 2.0)
        vm, ui = self.run_header(pdata(), m)
        self.assertEqual(vm.ended, "ENDALL")
        m = Machine(holes=[])
        m.file_offset = (5.0, 0.0)
        vm, ui = self.run_header(pdata(), m)
        self.assertEqual(vm.ended, "ENDALL")

    def test_preview_skips_zero_checks(self):
        vm, ui = self.run_header(pdata(mode=0), Machine(holes=[], base_offset=(9.0, 9.0), mode=1))
        self.assertEqual(vm.ended, "END")

    def test_cancel(self):
        vm, ui = self.run_header(pdata(), Machine(holes=[]), answers={"aligned file": ["Cancel"]})
        self.assertEqual(vm.ended, "END")

    def test_simulated_output_only_runs_in_preview(self):
        vm, ui = self.run_header(pdata(sim=True), Machine(holes=[], mode=0))
        self.assertTrue(any("only runs in PREVIEW" in s for s in ui.messages))
        self.assertFalse(any("Preview only" in s for s in ui.messages))
        vm, ui = self.run_header(pdata(sim=True), Machine(holes=[], mode=1))
        self.assertTrue(any("Preview only" in s for s in ui.messages))


# =====================================================================================
class TestFusion(unittest.TestCase):
    """Fusion 360 + Autodesk ShopBot post, Setup 2 with Z and Y inverted (board flipped end over end).
    With the WCS at the stock corner that sits front-left after the flip, holes on the line Y = H/2
    have the same coordinates in Setup 1 and Setup 2."""
    DESIGN = [(1.0, 15.0, 0.5), (23.0, 15.0, 0.625)]
    STOCK = (0.0, 0.0, 24.0, 30.0)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.job = os.path.join(self.tmp, "sideB_fusion.sbp")
        shutil.copy(FUSION, self.job)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def probe(self, deg, tx, ty, design=None):
        design = design or self.DESIGN
        truth = [T(deg, tx, ty, d[:2]) + (d[2],) for d in design]
        parks = [(x + 0.03, y - 0.04, 0.1) for x, y, _ in truth]
        vm, m, _ = run_probe(truth, parks)
        self.assertEqual(m.collisions, [])
        path = os.path.join(self.tmp, "vd_probe_data.csv")
        with open(path, "w") as fh:
            fh.write(data_text(vm))
        return path

    def test_fusion_file_is_transformed_exactly(self):
        for deg, tx, ty in ((0.37, 1.234, -0.567), (-3.0, 5.0, 2.0), (30.0, 0.0, 0.0)):
            tr = va.SbpTransformer(fit_of(deg, tx, ty))
            out = "\n".join(tr.transform(va.split_lines(FUSION_TEXT)))
            self.assertLess(sim_sbp.compare(FUSION_TEXT, out, math.radians(deg), tx, ty), 2e-5)
        self.assertIn("ON INPUT(5,0) MSGBOX(Spindle lockout switch is off., OkOnly, Spindle Lockout Warning)", out)
        self.assertIn("\nJH\n", out)
        self.assertIn("J3,,,0.2", out)                 # Z-only jog left alone
        self.assertEqual(tr.stats.arcs, 6)
        self.assertEqual(len(tr.stats.custom_cuts), 5)  # C9 C6 C9 C7 CN,91 - zero check after each
        self.assertTrue(any("JH" in w for w in tr.stats.warnings))
        self.assertEqual(tr.stats.file_units, 0)
        self.assertIsNone(va.material_box(va.split_lines(FUSION_TEXT)))

    def test_two_holes_need_the_stock_size(self):
        data = self.probe(0.4, 2.0, 1.5)
        res = va.run_alignment(data, self.job, self.DESIGN)
        self.assertFalse(res.ok)
        self.assertIn("Enter the stock size", res.report)
        res = va.run_alignment(data, self.job, self.DESIGN, stock=self.STOCK)
        self.assertTrue(res.ok, res.report)
        self.assertIn("entered by you", res.report)
        with open(res.out_path, encoding="latin-1") as fh:
            out = fh.read()
        sim_sbp.compare(FUSION_TEXT, out, math.radians(0.4), 2.0, 1.5, tol=0.0015)
        self.assertEqual(risky_string_lines(out), [])

    def test_side_a_numbers_for_off_axis_holes_fail_with_stock(self):
        design = [(1.0, 5.0, 0.5), (23.0, 5.0, 0.625)]            # NOT on Y = 15
        side_a = [(1.0, 25.0, 0.5), (23.0, 25.0, 0.625)]          # Setup 1 numbers: Y -> 30 - Y
        data = self.probe(0.4, 2.0, 1.5, design=design)
        res = va.run_alignment(data, self.job, side_a, stock=self.STOCK)
        self.assertFalse(res.ok)
        self.assertIn("end-over-end", res.report)

    def test_stock_from_sidecar_via_cli(self):
        data = self.probe(-0.2, 1.0, 1.0)
        va.write_sidecar(self.job, self.DESIGN, self.STOCK)
        self.assertEqual(va.read_sidecar_full(self.job), (self.DESIGN, self.STOCK))
        self.assertEqual(va.main([data, "--job", self.job, "--nogui"]), 0)
        self.assertEqual(va.main([data, "--job", self.job, "--nogui", "--stock", "24,30"]), 0)

    def test_stock_box_on_the_wrong_side_fails(self):
        data = self.probe(0.4, 2.0, 1.5)
        res = va.run_alignment(data, self.job, self.DESIGN, stock=(0.0, -30.0, 24.0, 0.0))
        self.assertFalse(res.ok)
        self.assertIn("does not cover the toolpaths", res.report)
        self.assertNotIn("side-A numbers fit", res.report)        # no misleading follow-on messages
        self.assertNotIn("outside the material", res.report)
        self.assertIsNone(res.out_path)

    def test_toolpath_extents(self):
        tp = va.toolpath_extents(va.split_lines(FUSION_TEXT))
        self.assertEqual(tp, (1.5, 2.0, 20.0, 22.0))
        self.assertAlmostEqual(va.box_coverage(tp, self.STOCK), 1.0)
        self.assertEqual(va.box_coverage(tp, (0, -30, 24, 0)), 0.0)

    def test_parse_stock(self):
        self.assertEqual(va.parse_stock("24, 30"), (0.0, 0.0, 24.0, 30.0))
        self.assertEqual(va.parse_stock("-12,-15,12,15"), (-12.0, -15.0, 12.0, 15.0))
        for bad in ("24", "0,0,-1,5", "a,b", "1,2,3"):
            with self.assertRaises(va.VDError):
                va.parse_stock(bad)

    def test_header_box_wins_over_entered_stock(self):
        # the VCarve fixture has a header box; a different entered stock is ignored with a warning
        shutil.copy(JOB, self.job)
        truth_design = DESIGN
        data = self.probe(0.2, 1.0, 1.0, design=truth_design)
        res = va.run_alignment(data, self.job, truth_design, stock=(0, 0, 20, 20))
        self.assertTrue(res.ok, res.report)
        self.assertIn("differs from the job header", res.report)


class TestEndToEnd(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.job = os.path.join(self.tmp, "sideB.sbp")
        shutil.copy(JOB, self.job)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def probe(self, deg, tx, ty, design=DESIGN, **cfg):
        truth = [T(deg, tx, ty, d[:2]) + (d[2] or 0.625,) for d in design]
        parks = [(x + 0.03, y - 0.04, 0.1) for x, y, _ in truth]
        vm, m, _ = run_probe(truth, parks, **cfg)
        self.assertEqual(m.collisions, [])
        path = os.path.join(self.tmp, "vd_probe_data.csv")
        with open(path, "w") as fh:
            fh.write(data_text(vm))
        return path

    def test_full_pipeline_recovers_true_placement(self):
        deg, tx, ty = 0.8, 3.4, 2.1
        data = self.probe(deg, tx, ty)
        res = va.run_alignment(data, self.job, DESIGN)
        self.assertTrue(res.ok, res.report)
        with open(res.out_path, encoding="latin-1") as fh:
            out = fh.read()
        sim_sbp.compare(JOB_TEXT, out, math.radians(deg), tx, ty, tol=0.0015)
        # %(6)/%(7) hold the table position of the X/Y zero (constant unless X/Y is re-zeroed)
        hi = float(re.search(r"IF %\(6\) > ([-0-9.]+) THEN GOTO VDALIGN_BADZERO", out).group(1))
        lo = float(re.search(r"IF %\(6\) < ([-0-9.]+) THEN GOTO VDALIGN_BADZERO", out).group(1))
        self.assertAlmostEqual(hi, 3.252, delta=0.0002)
        self.assertAlmostEqual(lo, 3.248, delta=0.0002)
        self.assertIn("IF %(25) = 1 THEN GOTO VDALIGN_BADUNITS", out)
        self.assertIn("IF %(22) = 0 THEN GOTO VDALIGN_CHECK_MOVE", out)
        self.assertTrue(os.path.exists(os.path.splitext(res.out_path)[0] + "_report.txt"))
        self.assertTrue(out.startswith("'==== VIRTUAL DOWEL ALIGNED FILE"))
        self.assertNotIn("centerline", res.report)
        self.assertEqual(risky_string_lines(out), [])          # SB3 would evaluate such text

    def test_rezero_between_holes_fails(self):
        data = self.probe(0.3, 1.0, 0.5)
        lines = open(data).read().splitlines()
        out = []
        for ln in lines:                       # hole 2 recorded with a different %(6): zero moved
            f = ln.split(",")
            if f[0] == "T" and f[1].strip() == "2":
                f[9] = str(float(f[9]) + 0.25)
            out.append(",".join(f))
        with open(data, "w") as fh:
            fh.write("\n".join(out) + "\n")
        res = va.run_alignment(data, self.job, DESIGN)
        self.assertFalse(res.ok)
        self.assertIn("X/Y zero was changed during probing", res.report)

    def test_three_holes_with_probe_runout_and_spin180(self):
        design = [(12.0, 1.0, 0.5), (12.0, 29.0, 0.625), (2.0, 15.0, 0.5)]
        deg, tx, ty = -1.1, 2.0, 1.5
        truth = [T(deg, tx, ty, d[:2]) + (d[2],) for d in design]
        parks = [(x - 0.05, y + 0.02, 0.12) for x, y, _ in truth]
        ecc = (0.004, -0.003)
        m = Machine(holes=truth, ecc=ecc)
        ui = UI(parks=parks)
        ui.on_spin = lambda mc: setattr(mc, "ecc", (-mc.ecc[0], -mc.ecc[1]))
        vm = VM(configure(PROBE, spin180=1, nholes=3), m, ui, numfmt=vb_single).run()
        self.assertEqual(m.collisions, [])
        data = os.path.join(self.tmp, "d.csv")
        with open(data, "w") as fh:
            fh.write(data_text(vm))
        res = va.run_alignment(data, self.job, design)
        self.assertTrue(res.ok, res.report)
        with open(res.out_path, encoding="latin-1") as fh:
            out = fh.read()
        sim_sbp.compare(JOB_TEXT, out, math.radians(deg), tx, ty, tol=0.0012)
        self.assertIn("Probe tip offset from spindle axis: X +0.004", res.report)

    def test_cli(self):
        data = self.probe(-0.3, 1.0, 0.5)
        rc = va.main([data, "--job", self.job, "--holes", "12,1,0.5;12,29,0.625", "--nogui"])
        self.assertEqual(rc, 0)
        va.write_sidecar(self.job, DESIGN)
        self.assertEqual(va.read_sidecar(self.job), DESIGN)
        rc = va.main(["--data", data, "--job", self.job, "--nogui", "--out", os.path.join(self.tmp, "o.sbp")])
        self.assertEqual(rc, 0)

    def test_failure_writes_nothing_and_removes_stale_output(self):
        data = self.probe(0.2, 1.0, 1.0)
        stale = os.path.join(self.tmp, "sideB_VD.sbp")
        with open(stale, "w") as fh:
            fh.write(f"'==== {va.MARKER} - vd_align.py v1.9 ====\nold aligned file\n")
        res = va.run_alignment(data, self.job, [(12.0, 1.0, 0.5), (12.0, 28.9, 0.625)])
        self.assertFalse(res.ok)
        self.assertIsNone(res.out_path)
        self.assertFalse(os.path.exists(stale))
        self.assertIn("FAIL", res.report)

    def test_files_virtual_dowel_did_not_write_are_never_deleted_or_overwritten(self):
        data = self.probe(0.2, 1.0, 1.0)
        mine = os.path.join(self.tmp, "sideB_VD.sbp")          # the user's own file with the output's name
        with open(mine, "w") as fh:
            fh.write("my own file\n")
        for design in (DESIGN, [(12.0, 1.0, 0.5), (12.0, 28.9, 0.625)]):     # would PASS / would FAIL
            with self.assertRaises(va.VDError) as cm:
                va.run_alignment(data, self.job, design)
            self.assertIn("not written by Virtual Dowel", str(cm.exception))
        self.assertEqual(open(mine).read(), "my own file\n")
        notes = os.path.join(self.tmp, "notes.txt")
        with open(notes, "w") as fh:
            fh.write("notes\n")
        with self.assertRaises(va.VDError):
            va.run_alignment(data, self.job, DESIGN, out_path=notes)
        self.assertEqual(open(notes).read(), "notes\n")
        # a foreign file with the other output name (_VDSIM) is left alone
        os.remove(mine)
        sim = os.path.join(self.tmp, "sideB_VDSIM.sbp")
        with open(sim, "w") as fh:
            fh.write("mine too\n")
        res = va.run_alignment(data, self.job, DESIGN)
        self.assertTrue(res.ok, res.report)
        self.assertEqual(open(sim).read(), "mine too\n")
        # a recorded output that the user replaced with their own file survives a new session
        with open(res.out_path, "w") as fh:
            fh.write("replaced by the user\n")
        va.new_session()
        self.assertEqual(open(res.out_path).read(), "replaced by the user\n")

    def test_units_mismatch(self):
        data = self.probe(0.2, 1.0, 1.0)
        with open(self.job, "w") as fh:
            fh.write(JOB_TEXT.replace("IF %(25)=1", "IF %(25)=0"))
        res = va.run_alignment(data, self.job, DESIGN)
        self.assertFalse(res.ok)
        self.assertIn("mm", res.report)

    def test_old_probe_data_refused_unless_confirmed(self):
        data = self.probe(0.2, 1.0, 1.0)
        old = time.time() - 3 * 3600
        os.utime(data, (old, old))
        res = va.run_alignment(data, self.job, DESIGN)
        self.assertFalse(res.ok)
        self.assertIn("minutes old", res.report)
        res = va.run_alignment(data, self.job, DESIGN, allow_old_data=True)
        self.assertTrue(res.ok, res.report)

    def test_side_a_coordinates_on_off_axis_holes_fail(self):
        design = [(2.0, 1.0, 0.5), (2.0, 29.0, 0.625)]
        side_a = [(22.0, 1.0, 0.5), (22.0, 29.0, 0.625)]            # what the operator typed by mistake
        data = self.probe(0.2, 1.0, 1.0, design=design)
        res = va.run_alignment(data, self.job, side_a)
        self.assertFalse(res.ok)
        self.assertIn("shifted by 20.00", res.report)
        res = va.run_alignment(data, self.job, design)              # even the RIGHT numbers are ambiguous
        self.assertFalse(res.ok)
        th = dict(va.DEFAULTS, allow_offaxis_ambiguity=1)
        res = va.run_alignment(data, self.job, design, th=th)
        self.assertTrue(res.ok, res.report)
        self.assertIn("double-check", res.report)
        # three holes NOT in a line: a mirror cannot be fitted, so no ambiguity
        design3 = [(2.0, 1.0, 0.5), (2.0, 29.0, 0.625), (20.0, 20.0, 0.5)]
        data = self.probe(0.2, 1.0, 1.0, design=design3, nholes=3)
        res = va.run_alignment(data, self.job, design3)
        self.assertTrue(res.ok, res.report)

    def test_small_offaxis_error_only_warns(self):
        design = [(12.03, 1.0, 0.5), (12.03, 29.0, 0.625)]          # 0.03 in off the centreline
        data = self.probe(0.2, 1.0, 1.0, design=design)
        res = va.run_alignment(data, self.job, design)
        self.assertTrue(res.ok, res.report)
        self.assertIn("shifted by 0.060", res.report)

    def test_no_material_size_in_header(self):
        body = "\n".join(ln for ln in JOB_TEXT.splitlines() if "Length of material" not in ln) + "\n"
        with open(self.job, "w") as fh:
            fh.write(body)
        design = [(2.0, 1.0, 0.5), (2.0, 29.0, 0.625)]
        data = self.probe(0.2, 1.0, 1.0, design=design)
        res = va.run_alignment(data, self.job, [(22.0, 1.0, 0.5), (22.0, 29.0, 0.625)])
        self.assertFalse(res.ok)
        self.assertIn("no material size", res.report)
        design3 = [(2.0, 1.0, 0.5), (2.0, 29.0, 0.625), (20.0, 20.0, 0.5)]
        data = self.probe(0.2, 1.0, 1.0, design=design3, nholes=3)
        res = va.run_alignment(data, self.job, design3)
        self.assertTrue(res.ok, res.report)

    def test_mirror_symmetry_detection(self):
        self.assertTrue(va.has_mirror_symmetry([(0, 0, 0.5), (5, 7, 0.625)], 0.04, 0.06))
        self.assertFalse(va.has_mirror_symmetry([(0, 0, None), (10, 0, None), (3, 8, None)], 0.04, 0.06))
        self.assertTrue(va.has_mirror_symmetry([(0, 0, None), (10, 0, None), (5, 8, None)], 0.04, 0.06))
        self.assertFalse(va.has_mirror_symmetry([(0, 0, 0.5), (10, 0, 0.625), (5, 8, 0.5)], 0.04, 0.06))

    def test_outside_holes_fail(self):
        design = [(26.0, 1.0, 0.5), (26.0, 29.0, 0.625)]
        data = self.probe(0.2, 1.0, 1.0, design=design)
        res = va.run_alignment(data, self.job, design)
        self.assertFalse(res.ok)
        self.assertIn("outside the material", res.report)

    def test_simulated_data_is_flagged(self):
        text = configure(PROBE, sim=1)
        ui = UI(sim_inputs=[(12.0, 1.0, 0.5), (12.0, 29.0, 0.625)])
        vm = VM(text, Machine(holes=[]), ui, numfmt=vb_single).run()
        path = os.path.join(self.tmp, "sim.csv")
        with open(path, "w") as fh:
            fh.write(data_text(vm))
        res = va.run_alignment(path, self.job, DESIGN)
        self.assertTrue(res.ok, res.report)
        self.assertTrue(res.out_path.endswith("_VDSIM.sbp"))
        with open(res.out_path) as fh:
            out = fh.read()
        self.assertIn("IF %(22) = 1 THEN GOTO VDALIGN_SIMOK", out)
        self.assertNotIn("&vdalign_dx", out)

    def test_non_latin1_path(self):
        job = os.path.join(self.tmp, "sideB_\u010d.sbp")
        shutil.copy(JOB, job)
        data = self.probe(0.2, 1.0, 1.0)
        res = va.run_alignment(data, job, DESIGN)
        self.assertTrue(res.ok, res.report)

    def test_line_ending_problems_are_refused(self):
        data = self.probe(0.2, 1.0, 1.0)
        for bad in (JOB_TEXT.replace("\n", "\r"), JOB_TEXT.replace("\n", "\r\n", 5)):
            with open(self.job, "wb") as fh:
                fh.write(bad.encode("latin-1"))
            with self.assertRaises(va.VDError):
                va.run_alignment(data, self.job, DESIGN)
        with open(self.job, "wb") as fh:                      # consistent CRLF is fine
            fh.write(JOB_TEXT.replace("\n", "\r\n").encode("latin-1"))
        res = va.run_alignment(data, self.job, DESIGN)
        self.assertTrue(res.ok, res.report)
        with open(res.out_path, "rb") as fh:
            out = fh.read()
        self.assertNotIn(b"\n", out.replace(b"\r\n", b""))

    def test_job_without_moves_fails(self):
        data = self.probe(0.2, 1.0, 1.0)
        with open(self.job, "w") as fh:
            fh.write("'UNITS:Inches\n'Length of material in X = 24\n'Length of material in Y = 30\n"
                     "'Home Position Information = Bottom Left Corner\nSA\nJZ,0.2\nEND\n")
        res = va.run_alignment(data, self.job, DESIGN)
        self.assertFalse(res.ok)
        self.assertIn("no X/Y moves", res.report)

    def test_output_may_not_overwrite_inputs(self):
        data = self.probe(0.2, 1.0, 1.0)
        with self.assertRaises(va.VDError):
            va.run_alignment(data, self.job, DESIGN, out_path=self.job)
        self.assertTrue(os.path.exists(self.job))
        with self.assertRaises(va.VDError):
            va.run_alignment(data, self.job, DESIGN, out_path=data)

    def test_real_data_must_come_from_move_mode(self):
        data = self.probe(0.2, 1.0, 1.0)
        with open(data) as fh:
            txt = fh.read()
        first, rest = txt.split("\n", 1)
        with open(data, "w") as fh:
            fh.write(first.rsplit(",", 1)[0] + ",1\n" + rest)
        res = va.run_alignment(data, self.job, DESIGN)
        self.assertFalse(res.ok)
        self.assertIn("mode 1", res.report)

    def test_every_run_is_logged(self):
        open(va.LOG_FILE, "w").close()
        self.assertEqual(va.main(["--NEW-SESSION"]), 0)
        log = open(va.LOG_FILE).read()
        self.assertIn("args ['--NEW-SESSION']", log)
        self.assertIn("finished, exit code 0", log)
        self.assertTrue(os.path.exists(va.STATE_FILE))          # what the user checks for

    def test_plain_word_arguments(self):
        """SB3 alters "-" "/" "~" in SHELL text, so vd_probe.sbp passes plain words."""
        data = self.probe(0.2, 1.0, 1.0)
        res = va.run_alignment(data, self.job, DESIGN)
        self.assertTrue(os.path.exists(res.out_path))
        self.assertEqual(va.main(["NEWSESSION"]), 0)
        self.assertFalse(os.path.exists(res.out_path))
        open(va.LOG_FILE, "w").close()
        self.assertEqual(va.main(["shelltest", "a", "-", "b+c*d=e", "/", "f"]), 0)
        log = open(va.LOG_FILE).read()
        self.assertIn("shelltest: command line as received:", log)
        self.assertIn("'b+c*d=e', '/', 'f'", log)

    def test_new_session_removes_earlier_aligned_files(self):
        data = self.probe(0.2, 1.0, 1.0)
        res = va.run_alignment(data, self.job, DESIGN)
        self.assertTrue(os.path.exists(res.out_path))
        self.assertEqual(va.main(["--NEW-SESSION"]), 0)        # ShopBot upper-cases the string
        self.assertFalse(os.path.exists(res.out_path))
        self.assertTrue(os.path.exists(self.job))
        # relative output paths are stored absolute, so a later cleanup from another folder
        # cannot delete an unrelated file of the same name
        cwd = os.getcwd()
        other = tempfile.mkdtemp()
        try:
            os.chdir(self.tmp)
            va.run_alignment(data, self.job, DESIGN, out_path="rel_out.sbp")
            os.chdir(other)
            with open("rel_out.sbp", "w") as fh:
                fh.write("unrelated")
            va.main(["--new-session"])
            self.assertTrue(os.path.exists(os.path.join(other, "rel_out.sbp")))
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "rel_out.sbp")))
        finally:
            os.chdir(cwd)
            shutil.rmtree(other, ignore_errors=True)

    def test_alignment_retires_files_from_other_probing_runs(self):
        """If SHELL never ran --new-session, the next alignment still removes stale _VD files."""
        va.new_session()
        job2 = os.path.join(self.tmp, "sideB_second.sbp")
        shutil.copy(self.job, job2)
        data = self.probe(0.2, 1.0, 1.0)
        a = va.run_alignment(data, self.job, DESIGN)
        b = va.run_alignment(data, job2, DESIGN)                    # same probing run: both stay
        self.assertTrue(a.ok and b.ok)
        self.assertTrue(os.path.exists(a.out_path) and os.path.exists(b.out_path))
        self.assertNotIn("earlier probing run", b.report)
        data = self.probe(-0.5, 1.3, 0.8)                              # board moved, probed again
        c = va.run_alignment(data, job2, DESIGN)
        self.assertTrue(c.ok)
        self.assertFalse(os.path.exists(a.out_path))
        self.assertTrue(os.path.exists(c.out_path))
        self.assertIn("earlier probing run", c.report)
        self.assertIn(os.path.basename(a.out_path), c.report)
        # entries written by v1.3 (plain paths, probing run unknown) are treated as old
        legacy = os.path.join(self.tmp, "legacy_VD.sbp")
        with open(legacy, "w") as fh:
            fh.write(f"'==== {va.MARKER} - vd_align.py v1.3 ====\nold\n")
        st = va._load_state()
        st["outputs"].append(legacy)
        va._save_state(st)
        va.run_alignment(data, job2, DESIGN)
        self.assertFalse(os.path.exists(legacy))
        self.assertTrue(os.path.exists(c.out_path))

    def test_probe_data_errors(self):
        hdr = "VD,1,0,0,0.125,0.5,2,{p},0,1,0,0\n"
        with self.assertRaises(va.ProbeDataError):
            va.parse_probe_data(hdr.format(p=2) + "T,1,0,1,1,1,1,1,1,1,1\n")          # no END
        with self.assertRaises(va.ProbeDataError):
            va.parse_probe_data(hdr.format(p=2) + "T,1,0,1,1,1,1;5,1,1,1,1\nEND,2\n")
        with self.assertRaises(va.ProbeDataError):
            va.parse_probe_data("VD,2,0,0,0.125,0.5,2,2,0,1,0,0\nEND,2\n")
        with self.assertRaises(va.ProbeDataError):
            va.parse_probe_data("VD,1,0,0,0.125,0.5,2,2,0,1,0\nEND,2\n")           # old 10-field header
        with self.assertRaises(va.ProbeDataError):
            va.parse_probe_data(hdr.format(p=0) + "END,2\n")                          # zero passes
        with self.assertRaises(va.ProbeDataError):
            va.parse_probe_data("")
        pd = va.parse_probe_data(hdr.format(p=1) + "END,2\n")
        with self.assertRaises(va.ProbeDataError):
            va.compute_holes(pd)



# =====================================================================================
# ---- half slots: holes with one open side at the stock edge (thick stock) --------------------------
def _rot(th, v):
    c, s = math.cos(th), math.sin(th)
    return (c * v[0] - s * v[1], s * v[0] + c * v[1])


def slot_readings(c, R, theta, open_dir, r=0.0625, push=(0.0, 0.0), ecc=(0.0, 0.0), back=0.15, start_off=0.02,
                  passes=2, noise=0.0, rng=None, decimals=3):
    """Touch readings for one half slot, from exact geometry (independent of vd_align).
    c: centre of the round end (machine); R: half-width; theta: how far the slot is turned;
    open_dir: (axis, sign) of the opening in the design frame. The routine's pattern: two wall touches
    along the machine cross axis from a point `back` behind the end-circle centre, then the end touch along
    the machine axis from their midpoint. Reading = ball centre at contact + push[axis] in the direction
    of motion - ecc (SB3 reports the spindle axis), then noise and SB3's rounding."""
    rng = rng or __import__("random").Random(7)
    ao, so = open_dir
    ac = 3 - ao
    e_in = (-so, 0.0) if ao == 1 else (0.0, -so)                 # machine direction of the end touch
    e_c = (1.0, 0.0) if ac == 1 else (0.0, 1.0)                    # machine direction of the + wall touch
    u, v = _rot(theta, e_in), _rot(theta, e_c)                     # slot axis (inward) and its normal
    rho = R - r
    out = []

    def read(pt, e, axis):
        x = pt[0] + push[axis - 1] * e[0] - ecc[0] + rng.gauss(0, noise)
        y = pt[1] + push[axis - 1] * e[1] - ecc[1] + rng.gauss(0, noise)
        return (round(x, decimals), round(y, decimals)) if decimals is not None else (x, y)

    for k in range(1, passes + 1):
        s0 = (c[0] - back * u[0] + start_off * v[0], c[1] - back * u[1] + start_off * v[1])
        walls = {}
        for sg in (1, -1):
            e = (sg * e_c[0], sg * e_c[1])
            d0 = (s0[0] - c[0]) * v[0] + (s0[1] - c[1]) * v[1]
            ev = e[0] * v[0] + e[1] * v[1]
            kk = max((t - d0) / ev for t in (rho, -rho))
            p = (s0[0] + kk * e[0], s0[1] + kk * e[1])
            walls[sg] = read(p, e, ac)
            out.append((k, ac, sg, walls[sg]))
        m = ((walls[1][0] + walls[-1][0]) / 2, (walls[1][1] + walls[-1][1]) / 2)
        w = (c[0] - m[0], c[1] - m[1])
        we = w[0] * e_in[0] + w[1] * e_in[1]
        kk = we + math.sqrt(we * we - (w[0] ** 2 + w[1] ** 2) + rho * rho)
        p = (m[0] + kk * e_in[0], m[1] + kk * e_in[1])
        along = (p[0] - c[0]) * u[0] + (p[1] - c[1]) * u[1]
        assert along > 0, "end touch hit a wall before the round end"
        out.append((k, ao, -so, read(p, e_in, ao)))
    return out


def slot_data_text(slots, theta, tip=0.125, passes=2, units=0, spin_ecc=None, base=(3.25, -1.5), **kw):
    """slots: [(centre_machine, width, open_dir)]. spin_ecc: stylus offset; probes both spindle orientations."""
    orients = [0, 1] if spin_ecc is not None else [0]
    rows = [f"VD,1,{units},0,{tip},{max(w for _, w, _ in slots)},{len(slots)},{passes},{len(orients) - 1},1,0,0",
            "CFG,.3,.08,.03,.4,.08,.3", "DT,Monday October 05 2026 10:00:00"]
    for h, (c, wdt, od) in enumerate(slots, 1):
        rows.append(f"O,{h},{od[0]},{od[1]}")
        for o in orients:
            ecc = (0.0, 0.0) if spin_ecc is None else (spin_ecc if o == 0 else (-spin_ecc[0], -spin_ecc[1]))
            kw.setdefault("r", tip / 2)                    # true ball radius; `tip` is the setting
            for k, ax, sg, (x, y) in slot_readings(c, wdt / 2, theta, od, passes=passes, ecc=ecc, **kw):
                rows.append(f"T,{h},{o},{k},{ax},{sg},{x},{y},-0.4,{base[0]},{base[1]}")
    rows.append(f"END,{len(slots)}")
    return "\n".join(rows) + "\n"


# slots on the X centreline of the 24 x 30 VCarve fixture: bottom edge (opens -Y) and top edge (opens +Y)
SLOTS = [(12.0, 1.5, 0.5), (12.0, 28.5, 0.625)]
SLOT_OPEN = [(2, -1), (2, 1)]
STOCK24 = (0.0, 0.0, 24.0, 30.0)


class TestHalfSlots(unittest.TestCase):
    """Half slots (thick stock): the reference holes moved out to the stock edges, cut from one face only."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.job = os.path.join(self.tmp, "sideB.sbp")
        shutil.copy(JOB, self.job)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def placed(self, deg, tx, ty, design=SLOTS, opens=SLOT_OPEN):
        return [(T(deg, tx, ty, d[:2]), d[2], od) for d, od in zip(design, opens)]

    def data(self, deg, tx, ty, name="probe.csv", **kw):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            fh.write(slot_data_text(self.placed(deg, tx, ty), math.radians(deg), **kw))
        return path

    def test_centre_of_a_turned_slot_in_every_direction(self):
        for od in ((1, -1), (1, 1), (2, -1), (2, 1)):
            for deg in (0.0, 3.0, -5.0):
                th = math.radians(deg)
                slots = [((5.0, 7.0), 0.5, od), ((25.0, 9.0), 0.625, od)]
                pd = va.parse_probe_data(slot_data_text(slots, th, decimals=None))
                self.assertEqual(pd.open, {1: od, 2: od})
                holes = va.compute_holes(pd, th)[0]
                for h, (c, wdt, _) in zip(holes, slots):
                    self.assertLess(math.dist(h.center, c), 2e-6, (od, deg))
                    self.assertAlmostEqual(h.dia, wdt, delta=2e-6)
                    self.assertEqual(h.open, od)
                    self.assertGreater(h.wall_margin, 0.1)

    def test_errors_that_push_every_touch_cancel(self):
        # pretravel/latency/backlash the same on both axes, and a wrong ball setting: the wall pair measures
        # the same push as the end touch, so each centre is exact on its own
        th = math.radians(2.0)
        slots = [((5.0, 7.0), 0.5, (1, -1)), ((25.0, 9.0), 0.625, (1, 1))]
        pd = va.parse_probe_data(slot_data_text(slots, th, tip=0.110, push=(0.004, 0.004), r=0.0625, decimals=None))
        for h, (c, _, _) in zip(va.compute_holes(pd, th)[0], slots):
            self.assertLess(math.dist(h.center, c), 2e-5)

    def test_unequal_push_cancels_between_opposite_slots(self):
        # more push along X than across: each end centre is off along its slot, in opposite directions,
        # so the fit's position is unchanged and only the spacing reads short
        deg, tx, ty = 1.5, 2.0, -1.0
        design = [(1.5, 15.0, 0.5), (22.5, 15.0, 0.625)]
        opens = [(1, -1), (1, 1)]
        slots = [(T(deg, tx, ty, d[:2]), d[2], od) for d, od in zip(design, opens)]
        pd = va.parse_probe_data(slot_data_text(slots, math.radians(deg), push=(0.003, 0.0), decimals=None))
        holes = va.compute_holes(pd, math.radians(deg))[0]
        al = va.align(design, holes, va.DEFAULTS, 1.0, pd.nominal)
        self.assertEqual(al.failures, [])
        self.assertAlmostEqual(math.degrees(al.fit.theta), deg, delta=1e-4)
        for d in design:
            self.assertLess(math.dist(al.fit.point(*d[:2]), T(deg, tx, ty, d[:2])), 1e-4)
        self.assertAlmostEqual(al.spacing[0][3], -0.006, delta=2e-4)

    def test_spin180_cancels_stylus_runout(self):
        th = math.radians(-1.0)
        slots = [((5.0, 7.0), 0.5, (1, -1)), ((25.0, 9.0), 0.625, (1, 1))]
        pd = va.parse_probe_data(slot_data_text(slots, th, spin_ecc=(0.003, -0.002), decimals=None))
        for h, (c, _, _) in zip(va.compute_holes(pd, th)[0], slots):
            self.assertLess(math.dist(h.center, c), 2e-6)
            self.assertAlmostEqual(math.hypot(*h.ecc), math.hypot(0.003, 0.002), delta=1e-6)

    def test_full_alignment_of_a_job(self):
        deg, tx, ty = 0.8, 1.7, 0.6
        data = self.data(deg, tx, ty, noise=0.0002, passes=3)
        res = va.run_alignment(data, self.job, SLOTS)
        self.assertTrue(res.ok, res.report)
        self.assertAlmostEqual(res.rotation_deg, deg, delta=0.005)
        with open(res.out_path, encoding="latin-1") as fh:
            out = fh.read()
        sim_sbp.compare(JOB_TEXT, out, math.radians(deg), tx, ty, tol=0.002)

    def test_slot_must_open_where_the_design_says(self):
        path = os.path.join(self.tmp, "probe.csv")
        with open(path, "w") as fh:          # both probed as opening toward -Y: slot 2 opens toward +Y
            fh.write(slot_data_text(self.placed(0.5, 1.0, 1.0, opens=[(2, -1), (2, -1)]), math.radians(0.5)))
        res = va.run_alignment(path, self.job, SLOTS)
        self.assertFalse(res.ok)
        self.assertIn("opens toward", res.report)

    def test_wall_touches_inside_the_round_end_fail(self):
        res = va.run_alignment(self.data(0.5, 1.0, 1.0, back=-0.1), self.job, SLOTS)
        self.assertFalse(res.ok)
        self.assertIn("round end", res.report)

    def test_half_slots_need_the_stock_box(self):
        fusion = os.path.join(self.tmp, "fusion.sbp")
        shutil.copy(FUSION, fusion)
        design = [(1.5, 15.0, 0.5), (22.5, 15.0, 0.625)]        # on the flip axis Y = H/2
        opens = [(1, -1), (1, 1)]
        path = os.path.join(self.tmp, "probe.csv")
        with open(path, "w") as fh:
            fh.write(slot_data_text(self.placed(0.3, 1.0, 1.0, design, opens), math.radians(0.3)))
        res = va.run_alignment(path, fusion, design)
        self.assertFalse(res.ok)
        self.assertIn("stock box", res.report)
        res = va.run_alignment(path, fusion, design, stock=STOCK24)
        self.assertTrue(res.ok, res.report)

    def test_probe_data_rules_for_slots(self):
        good = slot_data_text(self.placed(0.0, 0.0, 0.0), 0.0)
        for bad, msg in ((good.replace("O,2,2,1", "O,2,3,1"), "open side"),
                         (good.replace("O,2,2,1", "O,3,2,1"), "out of range")):
            with self.assertRaises(va.ProbeDataError) as cm:
                va.compute_holes(va.parse_probe_data(bad))
            self.assertIn(msg, str(cm.exception))
        # a touch toward the open side means the data and the slot disagree
        extra = good.replace("END,2", "T,2,0,1,2,1,12.0,28.9,-0.4,3.25,-1.5\nEND,2")
        with self.assertRaises(va.ProbeDataError) as cm:
            va.compute_holes(va.parse_probe_data(extra))
        self.assertIn("open side", str(cm.exception))


# ---- the half-slot routine in vd_probe.sbp, run in the simulator on thick stock ------------------
# side B of a 24 x 30 x 3 in board: slots on the flip axis Y = H/2, cut from face A up to 1.75 in;
# bays from the top down to 1.6 in. Slots at least 0.6 in wide: the end search runs along a machine
# axis, so on a turned board it drifts sideways (about 0.17 in over 2 in at 5 deg).
SB_W, SB_H, SB_T, SB_TOP, SB_FLOOR = 24.0, 30.0, 3.0, 1.75, 1.6
SLOTS_B = [(1.5, 15.0, 0.625, (1, -1)), (22.5, 15.0, 0.75, (1, 1))]
PROBE_DIMS = dict(stem=0.04, free=1.0, body=0.4, body_len=2.0, nut=0.9)
OPEN_CODE = {(1, -1): 1, (1, 1): 2, (2, -1): 3, (2, 1): 4}


def bay_box(slot, rt=0.0625, body=0.4, tol=0.4, err=(0.0, 0.0), far=5.0, k=1.0, widest=0.75):
    """The bay the plan asks for: the probe body follows the ball, so the bay is wider than the widest
    slot's ball travel by the body radius on each side, and reaches past the round end by the body
    radius; plus `tol` (rough fit error and the wall check's search). `err` = where the rough fit
    really put it (design units)."""
    x, y, w, (ax, sg) = slot
    rho = widest * k / 2 - rt
    half, inner = rho + body + tol, rho + body + tol
    if ax == 1:
        x0, x1 = sorted((x + sg * far * k, x - sg * inner))
        y0, y1 = y - half, y + half
    else:
        y0, y1 = sorted((y + sg * far * k, y - sg * inner))
        x0, x1 = x - half, x + half
    return (x0 + err[0], y0 + err[1], x1 + err[0], y1 + err[1])


def slot_run(deg, t, h=0.8, bays=True, tol=0.4, park_err=((0.0, 0.04), (0.0, -0.03)), slots=SLOTS_B, k=1.0,
             machine_kw=None, ui_kw=None, bay_err=(0.1, -0.08), on_spin=None, text=None, **cfg):
    """k = 25.4 runs the same board on a machine set to mm."""
    sl = [(x * k, y * k, w * k, od) for x, y, w, od in slots]
    dims = {n: v * k for n, v in PROBE_DIMS.items()}
    rt = 0.0625 * k
    boxes = [bay_box(sv, rt=rt, body=dims["body"], tol=tol * k, err=(bay_err[0] * k, bay_err[1] * k), k=k)
             for sv in sl] if bays else []
    th = math.radians(deg)
    stock = SlotStock(SB_W * k, SB_H * k, SB_T * k, th, (t[0] * k, t[1] * k), sl, SB_TOP * k, boxes,
                      SB_FLOOR * k, probe=dims)
    parks = []
    for (x, y, w, (ax, sg)), e in zip(sl, park_err):
        edge = (0.0 if sg < 0 else SB_W * k) if ax == 1 else (0.0 if sg < 0 else SB_H * k)
        d = (edge + sg * 0.3 * k, y) if ax == 1 else (x, edge + sg * 0.3 * k)
        d = (d[0] + e[0] * k, d[1] + e[1] * k)
        mx, my = T(deg, t[0] * k, t[1] * k, d)
        parks.append((mx, my, h * k))
    c = dict(nholes=len(slots), hole=max(sv[2] for sv in slots))
    c.update({f"open{i}": OPEN_CODE[sv[3]] for i, sv in enumerate(slots, 1)})
    c.update(cfg)
    mk = dict(holes=[], solid=stock, start=parks[0], tip=0.125 * k)
    mk.update(machine_kw or {})
    m = Machine(**mk)
    ui = UI(parks=list(parks), **(ui_kw or {}))
    if on_spin:
        ui.on_spin = on_spin
    vm = TrackingVM(text if text is not None else configure(PROBE, **c), m, ui, numfmt=vb_single).run()
    return vm, m, ui, parks


class TestSlotRoutine(unittest.TestCase):
    """vd_probe.sbp on half slots: park outside the opening, find the round end, walls, end, back out."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fit(self, vm, scale=1.0):
        pd = va.parse_probe_data(data_text(vm))
        design = [(x * scale, y * scale, w * scale) for x, y, w, _ in SLOTS_B]
        holes = va.compute_holes(pd)[0]
        first = va.align(design, holes, va.DEFAULTS, pd.scale, pd.nominal)
        holes = va.compute_holes(pd, first.fit.theta)[0]
        return pd, holes, va.align(design, holes, va.DEFAULTS, pd.scale, pd.nominal)

    def assert_recovers(self, al, deg, t, tol, scale=1.0):
        self.assertEqual(al.failures, [])
        self.assertAlmostEqual(math.degrees(al.fit.theta), deg, delta=0.01)
        for x, y, _, _ in SLOTS_B:
            truth = T(deg, t[0] * scale, t[1] * scale, (x * scale, y * scale))
            self.assertLess(math.dist(al.fit.point(x * scale, y * scale), truth), tol)

    def test_two_half_slots_end_to_end(self):
        deg, t = 1.2, (2.0, -1.5)
        vm, m, ui, parks = slot_run(deg, t)
        self.assertEqual(vm.ended, "END")
        self.assertEqual(m.collisions, [])
        self.assertLessEqual(vm.max_depth, 5)
        self.assertLess(math.dist((m.x, m.y), parks[1][:2]), 0.002)       # backed out to where it was parked
        self.assertEqual([c[0].split()[-1] for c in vm.shell], ["newsession", "align"])
        pd, holes, al = self.fit(vm)
        self.assertEqual(pd.open, {1: (1, -1), 2: (1, 1)})
        for h, (_, _, w, _) in zip(holes, SLOTS_B):
            self.assertAlmostEqual(h.dia, w, delta=0.012)           # trigger travel reads a little wide
            self.assertLess(h.repeat, 0.0015)
            self.assertGreater(h.wall_margin, 0.05)
        self.assert_recovers(al, deg, t, 0.0015)
        # and the whole alignment of a job, as the window would run it
        data = os.path.join(self.tmp, "vd_probe_data.csv")
        with open(data, "w") as fh:
            fh.write(data_text(vm))
        job = os.path.join(self.tmp, "sideB.sbp")
        shutil.copy(JOB, job)
        res = va.run_alignment(data, job, [sv[:3] for sv in SLOTS_B])
        self.assertTrue(res.ok, res.report)
        with open(res.out_path, encoding="latin-1") as fh:
            sim_sbp.compare(JOB_TEXT, fh.read(), math.radians(deg), t[0], t[1], tol=0.003)

    def test_turned_board(self):
        for deg, t in ((-4.0, (1.0, 2.5)), (5.0, (-1.0, 0.5))):
            vm, m, _, _ = slot_run(deg, t, park_err=((0.0, -0.08), (0.0, 0.08)))
            self.assertEqual(m.collisions, [])
            self.assert_recovers(self.fit(vm)[2], deg, t, 0.0015)

    def test_parked_off_line_is_recentred_at_the_opening(self):
        vm, m, _, _ = slot_run(1.0, (0.5, 0.5), park_err=((0.0, 0.12), (0.0, -0.12)))
        self.assertEqual(m.collisions, [])
        self.assert_recovers(self.fit(vm)[2], 1.0, (0.5, 0.5), 0.0015)

    def test_metric_machine(self):
        k = 25.4
        deg, t = 0.7, (1.0, -0.5)
        vm, m, _, _ = slot_run(deg, t, k=k, machine_kw=dict(units=1, pretravel=0.1, latency=0.012, jitter=0.005,
                                                            overtravel=3.0, base_offset=(80.0, -40.0)))
        self.assertEqual(vm.ended, "END")
        self.assertEqual(m.collisions, [])
        pd, _, al = self.fit(vm, k)
        self.assertEqual(pd.units, 1)
        self.assert_recovers(al, deg, t, 0.04, scale=k)

    def test_spin180_cancels_stylus_runout(self):
        deg, t = 0.5, (1.0, 1.0)
        ecc = (0.004, -0.003)
        vm, m, ui, _ = slot_run(deg, t, spin180=1, machine_kw=dict(ecc=ecc),
                                on_spin=lambda mc: setattr(mc, "ecc", (-mc.ecc[0], -mc.ecc[1])))
        self.assertEqual(m.collisions, [])
        pd, holes, al = self.fit(vm)
        self.assert_recovers(al, deg, t, 0.0015)

    def test_short_slots_work_from_any_park_distance(self):
        # round-end centre 0.5 in inside the edge (Nikolai's slots): the first move touches the end, the
        # back-off puts the wall touches 0.08 in behind the round end whatever the park distance was
        short = [(0.5, 15.0, 0.625, (1, -1)), (23.5, 15.0, 0.75, (1, 1))]
        design = [sv[:3] for sv in short]
        for park_out in (0.05, 0.3, 0.6, 0.95):
            e = park_out - 0.3                             # slot 1 opens -X, slot 2 opens +X
            vm, m, ui, _ = slot_run(1.0, (0.4, -0.2), slots=short, park_err=((-e, 0.03), (e, -0.02)))
            self.assertEqual(m.collisions, [], park_out)
            pd = va.parse_probe_data(data_text(vm))
            holes = va.compute_holes(pd)[0]
            first = va.align(design, holes, va.DEFAULTS, 1.0, pd.nominal)
            holes = va.compute_holes(pd, first.fit.theta)[0]
            al = va.align(design, holes, va.DEFAULTS, 1.0, pd.nominal)
            self.assertEqual(al.failures + va.hole_checks(pd, holes, (0, 0), va.DEFAULTS)[0], [], park_out)
            self.assert_recovers(al, 1.0, (0.4, -0.2), 0.0015)
            for h in holes:
                self.assertGreater(h.wall_margin, 0.05, park_out)      # on the straight part, behind the round end

    def test_parked_more_than_an_inch_out_aborts_and_backs_out(self):
        vm, m, ui, parks = slot_run(0.5, (1.0, 1.0), park_err=((-1.0, 0.03), (1.0, -0.02)))
        self.assertEqual(m.collisions, [])
        self.assertTrue(any("No slot walls" in msg for msg in ui.messages), ui.messages)
        self.assertLess(math.dist((m.x, m.y), parks[0][:2]), 0.002)
        with self.assertRaises(va.ProbeDataError):
            va.parse_probe_data(data_text(vm))

    def test_parked_in_the_bay_aborts_before_the_body_can_hit_anything(self):
        # in the bay the probe body would reach the bay end wall before the ball does; the wall check at
        # the opening stops first
        # (a ball that straddles the bay floor still touches the slot walls' top edges - all at one height,
        # so the centres stay right and only the width reads wide)
        for h in (1.9, 1.7):                       # wholly above the bay floor (1.6)
            vm, m, ui, parks = slot_run(0.5, (1.0, 1.0), h=h)
            self.assertEqual(m.collisions, [], h)
            self.assertTrue(any("No slot walls" in msg for msg in ui.messages), ui.messages)
        self.assertLess(math.dist((m.x, m.y), parks[0][:2]), 0.002)

    def test_without_a_bay_the_stylus_hits_the_slot_ceiling(self):
        # the revision-2 mistake: a part-depth slot cannot be probed from above without a bay
        vm, m, _, _ = slot_run(0.5, (1.0, 1.0), bays=False)
        self.assertTrue(m.collisions)
        self.assertIn(m.collisions[0][3][0], ("stem", "body"))

    def test_bay_too_narrow_for_the_probe_body(self):
        vm, m, _, _ = slot_run(0.5, (1.0, 1.0), tol=-0.2, bay_err=(0.0, 0.0))
        self.assertTrue(m.collisions)
        self.assertEqual(m.collisions[0][3][0], "body")

    def test_simulation_never_moves_the_machine(self):
        typed = [(x, y, w) for x, y, w, _ in SLOTS_B]
        vm, m, _, _ = slot_run(0.0, (0.0, 0.0), sim=1, machine_kw=dict(mode=1),
                               ui_kw=dict(sim_inputs=typed))
        self.assertEqual(vm.ended, "END")
        self.assertEqual(m.moves, 0)
        pd = va.parse_probe_data(data_text(vm))
        self.assertTrue(pd.sim)
        for h, (x, y, _) in zip(va.compute_holes(pd)[0], typed):
            self.assertAlmostEqual(h.center[0], x + 0.020, delta=1e-6)     # the routine's synthetic offset
            self.assertAlmostEqual(h.center[1], y - 0.012, delta=1e-6)

    def test_bad_open_code_is_refused_before_anything_moves(self):
        vm, m, ui, _ = slot_run(0.0, (0.0, 0.0), open1=5)
        self.assertEqual(m.moves, 0)
        self.assertTrue(any("Settings problem" in msg for msg in ui.messages), ui.messages)


# ---- size calculator for the Fusion slots and bays (nothing is read from job files) ----------------
SIZES = dict(ball=0.125, stem=0.08, free=1.0, body=0.8, length=3.0, thick=3.0, slot_depth=1.75,
             widths=[0.625, 0.75], slot_length=1.25)


class TestSizeCalculator(unittest.TestCase):

    def test_example_is_ok_and_matches_the_simulated_bay(self):
        r = va.slot_bay_sizes(**SIZES)
        self.assertTrue(r.ok, r.problems)
        self.assertAlmostEqual(r.bay_floor, 1.6)
        self.assertTrue(r.body_in_bay)
        self.assertAlmostEqual(r.min_bay_width, 2 * (0.3125 + 0.4 + 0.4))
        self.assertAlmostEqual(r.min_bay_reach, 0.3125 + 0.4 + 0.4)
        self.assertAlmostEqual(r.bay_depth, 1.4)
        # the same bay the simulator probes without a collision (TestSlotRoutine)
        x0, y0, x1, y1 = bay_box((0.0, 0.0, 0.625, (1, -1)), far=0.0)
        self.assertAlmostEqual((y1 - y0), r.min_bay_width)
        self.assertAlmostEqual(x1, r.min_bay_reach)
        self.assertLess(r.park_lo, 0.8)                         # the simulator parks at 0.8
        self.assertGreater(r.park_hi, 0.8)
        self.assertIn("OK", r.text())

    def test_the_simulator_agrees_at_the_limits(self):
        # minimum bay, the worst rough-fit error toward the body, both ends of the park band: no collision
        r = va.slot_bay_sizes(**SIZES)
        for h in (r.park_lo + 0.01, r.park_hi - 0.01):
            for err in ((0.2, 0.0), (0.0, 0.2), (0.0, -0.2)):
                vm, m, ui, _ = slot_run(1.0, (0.5, 0.5), h=h, bay_err=err)
                self.assertEqual(m.collisions, [], (h, err))
                self.assertEqual(vm.ended, "END")
                self.assertFalse(any("aborted" in msg for msg in ui.messages), (h, err, ui.messages))

    def test_a_body_that_stays_above_the_stock_needs_only_room_for_the_stem(self):
        r = va.slot_bay_sizes(**dict(SIZES, free=2.0, length=4.5))
        self.assertTrue(r.ok, r.problems)
        self.assertFalse(r.body_in_bay)
        self.assertAlmostEqual(r.min_bay_width, 2 * (0.3125 + 0.04 + 0.4))
        self.assertGreaterEqual(r.park_lo + 2.0, 3.0)          # body bottom above the top in the whole band

    def test_problems_are_named(self):
        cases = [
            (dict(free=0.1), "body would go into the slot"),
            (dict(length=1.4), "collet nut"),
            (dict(widths=[0.5, 0.75]), "slot 1 is narrower"),
            (dict(widths=[0.7, 0.75]), "differ by"),
            (dict(slot_length=0.4), "too short"),
            (dict(slot_length=2.6), "end-search length"),
            (dict(bay_width=1.8), "bay is narrower"),
            (dict(bay_reach=0.9), "does not reach"),
            (dict(slot_depth=3.2), "deeper than the stock"),
        ]
        for change, words in cases:
            r = va.slot_bay_sizes(**dict(SIZES, **change))
            self.assertFalse(r.ok, change)
            self.assertTrue(any(words in pr for pr in r.problems), (change, r.problems))
            self.assertIn("NOT OK", r.text())

    def test_mm(self):
        k = 25.4
        mm = {n: ([w * k for w in v] if n == "widths" else v * k) for n, v in SIZES.items()}
        r = va.slot_bay_sizes(**mm, mm=True)
        self.assertTrue(r.ok, r.problems)
        self.assertAlmostEqual(r.min_bay_width, va.slot_bay_sizes(**SIZES).min_bay_width * k, places=6)
        self.assertIn(" mm", r.text())

    def test_command_line(self):
        rc = va.main(["sizes", "--ball", "0.125", "--stem", "0.08", "--free", "1", "--body", "0.8", "--length", "3",
                      "--thick", "3", "--slot-depth", "1.75", "--widths", "0.625,0.75", "--slot-length", "1.25"])
        self.assertEqual(rc, 0)
        rc = va.main(["sizes", "--ball", "0.125", "--stem", "0.08", "--free", "1", "--body", "0.8", "--length", "3",
                      "--thick", "3", "--slot-depth", "1.75", "--widths", "0.5,0.75", "--nogui"])
        self.assertEqual(rc, 1)


# ---- rough fit: places only the bay cut file, from the two jogged slot openings ---------------------
BAY_TEXT = """'bay cut for the half slots (Fusion style, side B coordinates)
SA
IF %(25)=1 THEN GOTO UNIT_ERROR
MS,1.0,0.5
J3,12.,-0.5,1.0
M3,12.,-0.5,0.4
M3,12.,2.2,0.4
M3,12.,-0.5,0.4
JZ,1.0
J2,12.,30.5
M3,12.,30.5,0.4
M3,12.,27.8,0.4
M3,12.,30.5,0.4
JZ,1.0
JH
END
UNIT_ERROR:
MSGBOX(The file is in inches,0,Units)
END
"""


def rough_text(points, base=(3.25, -1.5), units=0, sim=0, mode=0):
    rows = [f"VR,1,{units},{sim},{len(points)},{mode}", "DT,Monday October 05 2026 10:00:00"]
    rows += [f"R,{i},{x:.3f},{y:.3f},{base[0]},{base[1]}" for i, (x, y) in enumerate(points, 1)]
    rows.append(f"END,{len(points)}")
    return "\n".join(rows) + "\n"


class TestRoughFit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bay = os.path.join(self.tmp, "bays.sbp")
        with open(self.bay, "w") as fh:
            fh.write(BAY_TEXT)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def jog(self, deg, tx, ty, err=((0.06, -0.04), (-0.05, 0.07)), openings=((12.0, 0.0), (12.0, 30.0))):
        path = os.path.join(self.tmp, "rough.csv")
        pts = [(p[0] + e[0], p[1] + e[1]) for p, e in zip((T(deg, tx, ty, o) for o in openings), err)]
        with open(path, "w") as fh:
            fh.write(rough_text(pts))
        return path

    def test_openings_are_where_the_slot_axes_meet_the_box(self):
        self.assertEqual(va.slot_opening((12.0, 1.5, 0.5), STOCK24), (12.0, 0.0))
        self.assertEqual(va.slot_opening((12.0, 28.5, 0.625), STOCK24), (12.0, 30.0))
        self.assertEqual(va.slot_opening((1.5, 15.0, 0.5), STOCK24), (0.0, 15.0))
        self.assertEqual(va.open_side(22.5, 15.0, STOCK24), (1, 1))

    def test_bay_file_is_placed_from_the_jogged_openings(self):
        deg, tx, ty = 2.5, 3.0, -2.0
        res = va.run_rough(self.jog(deg, tx, ty), self.bay, SLOTS, stock=STOCK24)
        self.assertTrue(res.ok, res.report)
        self.assertTrue(res.out_path.endswith("bays_VDROUGH.sbp"))
        out = open(res.out_path, encoding="latin-1").read()
        self.assertTrue(va.is_aligned_file(res.out_path))
        self.assertIn("ROUGH", out)
        self.assertEqual(sbp_lint_problems(out), [])
        fit = va.Fit(math.radians(res.rotation_deg), res.shift[0], res.shift[1], [])
        sim_sbp.compare(BAY_TEXT, out, fit.theta, fit.tx, fit.ty, tol=1e-5)
        for d in SLOTS:                                       # the bays land within 0.2 in of the slots
            self.assertLess(math.dist(fit.point(*d[:2]), T(deg, tx, ty, d[:2])), 0.2)
        va.new_session()                                      # the precise probing starts a new session
        self.assertFalse(os.path.exists(res.out_path))

    def test_a_part_file_is_refused_as_the_bay_file(self):
        job = os.path.join(self.tmp, "sideB.sbp")
        shutil.copy(JOB, job)
        res = va.run_rough(self.jog(1.0, 1.0, 1.0), job, SLOTS, stock=STOCK24)
        self.assertFalse(res.ok)
        self.assertIsNone(res.out_path)
        self.assertIn("bay file", res.report)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "sideB_VDROUGH.sbp")))

    def test_openings_jogged_in_the_wrong_place_fail(self):
        res = va.run_rough(self.jog(1.0, 1.0, 1.0, err=((0.0, 0.0), (0.0, -2.0))), self.bay, SLOTS, stock=STOCK24)
        self.assertFalse(res.ok)
        self.assertIn("spacing", res.report)

    def test_the_bay_file_needs_the_stock_box(self):
        with self.assertRaises(va.VDError):
            va.run_rough(self.jog(1.0, 1.0, 1.0), self.bay, SLOTS)

    def test_key_notch_for_irregular_stock(self):
        # the real edges are 0.6 and 0.1 in inside the stock box at the two slots: the openings no longer sit
        # at known design points. Two openings fail the spacing check; with the key notch it works.
        deg, tx, ty = 2.0, 1.5, -0.5
        real = [(12.0, 0.6), (12.0, 29.9)]
        err = ((0.05, -0.04), (-0.06, 0.03))
        self.assertFalse(va.run_rough(self.jog(deg, tx, ty, err=err, openings=real), self.bay, SLOTS,
                                      stock=STOCK24).ok)
        key = (1.0, 10.0)                                          # small notch in the left edge, real edge 0.3 in
        path = os.path.join(self.tmp, "rough.csv")
        pts = [T(deg, tx, ty, o) for o in real + [(0.3, 10.0)]]
        pts = [(p[0] + e[0], p[1] + e[1]) for p, e in zip(pts, err + ((0.04, 0.05),))]
        with open(path, "w") as fh:
            fh.write(rough_text(pts))
        res = va.run_rough(path, self.bay, SLOTS, stock=STOCK24, key=key)
        self.assertTrue(res.ok, res.report)
        self.assertIn("Key notch", res.report)
        fit = va.Fit(math.radians(res.rotation_deg), res.shift[0], res.shift[1], [])
        for d in SLOTS:
            self.assertLess(math.dist(fit.point(*d[:2]), T(deg, tx, ty, d[:2])), 0.2)

    def test_line_fit_needs_lines_that_are_not_all_parallel(self):
        with self.assertRaises(va.VDError):
            va.fit_lines([((0, 0), (0.0, 1.0)), ((5, 0), (0.0, 1.0)), ((9, 0), (0.0, 1.0))],
                         [(0, 0), (5, 0.1), (9, 0.2)])

    def test_rough_data_rules(self):
        path = self.jog(0.0, 0.0, 0.0)
        text = open(path).read()
        for bad in (text.replace("END,2\n", ""), text.replace("VR,1,0,0,2,0", "VR,2,0,0,2,0"),
                    text.replace("R,2,", "R,3,")):
            with open(path, "w") as fh:
                fh.write(bad)
            with self.assertRaises(va.ProbeDataError):
                va.run_rough(path, self.bay, SLOTS, stock=STOCK24)


class TestRoughLocateRun(unittest.TestCase):
    """vd_probe.sbp in rough-locate mode: the bay cutter is in; the operator jogs over each slot opening."""

    def run_rough(self, points, sim=False, answers=None):
        text = configure(PROBE, rough=1, nholes=len(points), sim=int(sim), after='"rough"')
        m = Machine(holes=[], mode=1 if sim else 0)
        ui = UI(parks=[] if sim else [(x, y, 1.0) for x, y in points],
                sim_inputs=[(x, y, 0.0) for x, y in points] if sim else None, answers=answers)
        return TrackingVM(text, m, ui, numfmt=vb_single).run(), m, ui

    def test_records_each_opening_and_starts_the_window(self):
        pts = [(13.2, 1.1), (12.7, 31.4)]
        vm, m, ui = self.run_rough(pts)
        self.assertEqual(vm.ended, "END")
        self.assertEqual(m.moves, 0)                              # only the keypad moves the machine
        self.assertIn("C7", vm.commands)
        self.assertEqual([c[0].split()[-1] for c in vm.shell], ["newsession", "rough"])
        rd = va.parse_rough_data(data_text(vm))
        self.assertEqual(rd.points, pts)
        self.assertEqual(rd.base, [(3.25, -1.5)] * 2)
        self.assertFalse(rd.sim)

    def test_simulation(self):
        pts = [(13.2, 1.1), (12.7, 31.4), (1.0, 10.0)]
        vm, m, ui = self.run_rough(pts, sim=True)
        self.assertEqual(m.moves, 0)
        self.assertEqual([c[0].split()[-1] for c in vm.shell], ["rough"])     # no session clean-up in a dry run
        rd = va.parse_rough_data(data_text(vm))
        self.assertTrue(rd.sim)
        self.assertEqual(rd.points, pts)

    def test_cancel_leaves_no_usable_data(self):
        vm, m, ui = self.run_rough([(1.0, 1.0), (2.0, 2.0)], answers={"slot opening?": ["Cancel"]})
        self.assertEqual(vm.shell[-1][0].split()[-1], "newsession")
        with self.assertRaises(va.ProbeDataError):
            va.parse_rough_data(data_text(vm))


class TestBatch(unittest.TestCase):
    """Several side-B files (one per tool) aligned from one probing run in one go."""
    setUp = TestEndToEnd.setUp
    tearDown = TestEndToEnd.tearDown
    probe = TestEndToEnd.probe

    def test_every_file_is_aligned_and_a_bad_one_does_not_stop_the_rest(self):
        va.new_session()
        data = self.probe(0.3, 1.0, 0.5)
        tool2 = os.path.join(self.tmp, "sideB_tool2.sbp")
        shutil.copy(self.job, tool2)
        bad = os.path.join(self.tmp, "sideB_bad.sbp")
        with open(bad, "wb") as fh:
            fh.write(b"SA\rJZ,0.2\rM2,1,1\r")                     # bare CR - refused
        results = va.align_batch(data, [tool2, self.job, bad, tool2], DESIGN)
        self.assertEqual([os.path.basename(j) for j, _ in results],
                         ["sideB.sbp", "sideB_bad.sbp", "sideB_tool2.sbp"])     # sorted, no duplicates
        self.assertEqual([r.ok for _, r in results], [True, False, True])
        for job, r in results:
            if r.ok:
                self.assertTrue(os.path.exists(r.out_path))
                self.assertEqual(va.read_sidecar_full(job)[0], DESIGN)          # remembered per file
        self.assertIn("Could not align this file", results[1][1].report)
        self.assertIsNone(results[1][1].out_path)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "sideB_bad_VD.sbp")))
        summary = va.batch_summary(results)
        self.assertIn("2 of 3 file(s) aligned", summary)
        self.assertIn("FAIL  sideB_bad.sbp  (nothing written)", summary)
        # both outputs come from the same probing run, so a later run of the script keeps them
        va.run_alignment(data, self.job, DESIGN)
        self.assertTrue(os.path.exists(results[2][1].out_path))

    def test_prompts_are_prefilled_from_any_selected_file(self):
        tool2 = os.path.join(self.tmp, "sideB_tool2.sbp")
        shutil.copy(self.job, tool2)
        self.assertEqual(va.batch_defaults([self.job, tool2], 2), (None, None, []))
        va.write_sidecar(tool2, DESIGN, (0.0, 0.0, 24.0, 30.0))
        holes, stock, notes = va.batch_defaults([self.job, tool2], 2)
        self.assertEqual((holes, stock, notes), (DESIGN, (0.0, 0.0, 24.0, 30.0), []))
        va.write_sidecar(self.job, [(12.0, 1.0, 0.5), (12.0, 28.0, 0.625)])
        holes, _, notes = va.batch_defaults([self.job, tool2], 2)
        self.assertEqual(len(notes), 1)                               # disagreement is pointed out
        # the stock box is asked for only if some selected file has no material header (Fusion)
        self.assertEqual(va.needs_stock([self.job, tool2]), (False, None))
        fusion = os.path.join(self.tmp, "fusion.sbp")
        shutil.copy(FUSION, fusion)
        need, ext = va.needs_stock([self.job, fusion])
        self.assertTrue(need)
        self.assertIsNotNone(ext)

    def test_command_line_takes_several_jobs(self):
        data = self.probe(-0.4, 0.5, 1.5)
        tool2 = os.path.join(self.tmp, "sideB_tool2.sbp")
        shutil.copy(self.job, tool2)
        holes = "12,1,0.5;12,29,0.625"
        self.assertEqual(va.main(["--data", data, "--job", self.job, "--job", tool2, "--holes", holes, "--nogui"]), 0)
        for j in (self.job, tool2):
            self.assertTrue(os.path.exists(va.default_out_path(j, False)))
        self.assertEqual(va.main(["--data", data, "--job", self.job, "--job", tool2, "--holes", holes,
                                  "--out", os.path.join(self.tmp, "x.sbp"), "--nogui"]), 2)


# =====================================================================================
class _Folder(unittest.TestCase):
    """A scratch VirtualDowel folder: state, settings, run file and data all live in it."""
    KEYS = (("STATE_FILE", "vd_state.json"), ("MACHINE_FILE", "vd_machine.json"), ("RUN_FILE", "vd_run.sbp"),
            ("CAL_DATA", "vd_cal_data.csv"), ("DEFAULT_DATA", "vd_probe_data.csv"), ("TEMPLATE_FILE", "vd_probe.sbp"),
            ("ROUGH_DATA", "vd_rough_data.csv"))

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = {k: getattr(va, k) for k, _ in self.KEYS}
        for k, f in self.KEYS:
            setattr(va, k, os.path.join(self.tmp, f))
        shutil.copy(os.path.join(ROOT, "vd_probe.sbp"), va.TEMPLATE_FILE)   # saving settings updates it
        self.job = os.path.join(self.tmp, "sideB.sbp")
        shutil.copy(JOB, self.job)
        self.machine = va.load_machine(template=os.path.join(ROOT, "vd_probe.sbp"))

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(va, k, v)
        shutil.rmtree(self.tmp)

    def run_file(self):
        return open(va.RUN_FILE, newline="").read()


class TestPrepare(_Folder):
    """Step 1: everything checked before probing, then vd_run.sbp written with the settings."""

    def prep(self, jobs=None, design=DESIGN, stock=None, sim=False, mode="holes", centre=False, **m):
        mm = dict(self.machine, **m)
        return va.prepare_probing(jobs or [self.job], design, stock, mm, sim=sim, mode=mode, centre=centre,
                                  template=os.path.join(ROOT, "vd_probe.sbp"))

    def test_centre_fit_two_equal_bores_end_to_end(self):
        # a Fusion job (no material size) for a print 1 percent small, turned 1.2 deg, lying at (5, 7)
        job = os.path.join(self.tmp, "print.sbp")
        shutil.copy(FUSION, job)
        design = [(-2.0, 0.0, 0.5), (2.0, 0.0, 0.5)]
        self.assertIn("exactly two references", " ".join(self.prep([job], design + [(0.0, 2.0, 0.5)], centre=True).errors))
        p = self.prep([job], design, pin=2, trig=1, centre=True)
        self.assertTrue(p.ok, p.errors)
        self.assertEqual(va.load_session()["fit"], "centre")
        truth = [T(1.2, 5.0, 7.0, (-1.98, 0.0)) + (0.5,), T(1.2, 5.0, 7.0, (1.98, 0.0)) + (0.5,)]
        mach = Machine(holes=truth, probe_pin=2, idle=0)
        vm = VM(self.run_file(), mach, UI(parks=[(x + 0.03, y - 0.04, 0.1) for x, y, _ in truth]), numfmt=vb_single).run()
        self.assertEqual(vm.ended, "END")
        with open(va.DEFAULT_DATA, "w") as fh:
            fh.write(data_text(vm))
        r = va.align_batch(va.DEFAULT_DATA, [job], design, centre=True)[0][1]
        self.assertTrue(r.ok, r.report)
        self.assertAlmostEqual(r.rotation_deg, 1.2, delta=0.02)
        self.assertAlmostEqual(r.shift[0], 5.0, delta=0.01)
        self.assertAlmostEqual(r.shift[1], 7.0, delta=0.01)
        self.assertGreater(r.spacing_err, va.DEFAULTS["spacing_fail"])
        self.assertTrue(any("percent" in w for w in r.warns), r.warns)
        self.assertIn("Centre fit:", r.report)
        self.assertTrue(os.path.exists(va.default_out_path(job, False)))
        # the flip-milling fit refuses the same print
        r2 = va.align_batch(va.DEFAULT_DATA, [job], design)[0][1]
        self.assertFalse(r2.ok)
        self.assertTrue(any("spacing" in f for f in r2.fails), r2.fails)

    def test_run_file_carries_the_settings_and_probes_end_to_end(self):
        p = self.prep(passes=3, tip=0.118, pin=2, trig=1)
        self.assertTrue(p.ok, p.errors)
        text = self.run_file()
        self.assertTrue(text.startswith("'==== GENERATED"))
        self.assertEqual(text.count("\r\n"), len(text.splitlines()))            # CRLF kept
        vals = va._sbp_value_lines(text)
        self.assertEqual((vals["nholes"], vals["hole"], vals["passes"], vals["tip"], vals["pin"], vals["trig"]),
                         ("2", "0.625", "3", "0.118", "2", "1"))
        self.assertEqual((vals["launched"], vals["after"], vals["sim"], vals["cal"]), ("1", '"align"', "0", "0"))
        self.assertEqual(sbp_lint_problems(text), [])
        truth = [T(0.6, 1.2, 0.4, d[:2]) + (d[2],) for d in DESIGN]
        mach = Machine(holes=truth, probe_pin=2, idle=0, tip=0.118)
        vm = TrackingVM(text, mach, UI(parks=[(x + 0.03, y - 0.04, 0.1) for x, y, _ in truth]), numfmt=vb_single).run()
        self.assertEqual(vm.ended, "END")
        # clean-up again (harmless; covers an old vd_run.sbp being run again), then the window
        self.assertEqual([c[0].split()[-1] for c in vm.shell], ["newsession", "align"])
        self.assertIn("MSGBOX(VIRTUAL DOWEL probing - prepared ", text)              # names this preparation
        with open(va.DEFAULT_DATA, "w") as fh:
            fh.write(data_text(vm))
        r = va.align_batch(va.DEFAULT_DATA, [self.job], DESIGN)[0][1]
        self.assertTrue(r.ok, r.report)
        self.assertAlmostEqual(r.rotation_deg, 0.6, delta=0.01)
        self.assertLess(r.spacing_err, 0.002)
        self.assertEqual(va.read_sidecar_full(self.job)[0], DESIGN)
        self.assertEqual(va.load_session()["jobs"], [self.job])
        self.assertEqual(va.read_machine_file()["probe"]["passes"], 3)              # remembered for next time
        self.assertEqual(va._sbp_value_lines(open(va.TEMPLATE_FILE).read())["passes"], "3")   # and in vd_probe.sbp

    def test_jog_prompts_name_each_reference(self):
        say = va.say_prompts
        self.assertTrue(say([(0.0, 0.0, 0.75)], cal=True)[0].startswith("The calibration hole - measured diameter 0.75 in. Jog"))
        self.assertTrue(say(DESIGN, centre=True)[1].startswith("Bore 2 of 2 - diameter 0.625"))
        mm = say([(300.0, -25.0, 12.0), (10.0, -25.0, 16.0)], mm=True)
        self.assertIn("diameter 12 at X 300 Y -25 mm", mm[0])
        through = say([sv[:3] for sv in SLOTS_B], "through", box=STOCK24)
        self.assertIn("at mid slot height", through[0])                      # no bay in a through slot
        self.assertNotIn("bay", through[0])
        notch = say([sv[:3] for sv in SLOTS_B], "through", centre=True, box=STOCK24)
        self.assertTrue(notch[0].startswith("Notch 1 of 2 - width 0.625"))
        rough = say(SLOTS, "slots", box=STOCK24, key=(0.0, 15.0), rough=True)
        self.assertEqual(len(rough), 3)
        self.assertTrue(rough[0].startswith("Slot 1 of 3 - width 0.5 - round end X 12 Y 1.5 in - opens to Y min. "
                                            "Jog the bit tip"), rough[0])
        self.assertTrue(rough[2].startswith("The key notch - 3 of 3 - at X 0 Y 15 in. Jog the bit tip"), rough[2])
        long_mm = say([(300.5, -25.25, 15.875), (1200.75, -25.25, 19.05)], "slots", mm=True, box=(0, -50, 1250, 0))
        for line in say(DESIGN) + through + rough + mm + long_mm:
            self.assertFalse(set(",'()") & set(line), line)                   # MSGBOX bodies: SB3's forbidden characters
            self.assertLessEqual(len(line) + len("MSGBOX(,64,Virtual Dowel)"), va.MAX_SBP_LINE, line)
        tpl = open(os.path.join(ROOT, "vd_probe.sbp"), newline="").read()
        self.assertLessEqual(max(len(ln) for ln in tpl.split("\r\n")), va.MAX_SBP_LINE)   # the template too
        with self.assertRaises(va.VDError):
            va.render_say(tpl, ["x" * va.MAX_SBP_LINE])
        old = re.sub(r"(?m)^vd_say\d:\r?\n.*\r?\n", "", open(os.path.join(ROOT, "vd_probe.sbp"), newline="").read())
        with self.assertRaises(va.VDError) as cm:
            va.render_say(old, say(DESIGN))
        self.assertIn("vd_say1", str(cm.exception))

    def test_a_slot_too_close_to_the_edge_is_refused_before_probing(self):
        # the wall touches are made 0.08 in behind the round end: a centre 0.1 in inside the edge puts them
        # at the mouth. The job fixture's stock box is 24 x 30 with the left edge at X 0.
        p = self.prep(design=[(0.1, 15.0, 0.625), (22.5, 15.0, 0.75)], mode="slots")
        self.assertFalse(p.ok)
        self.assertTrue(any("slot 1" in e and "at least 0.25" in e for e in p.errors), p.errors)
        p = self.prep(design=[(0.3, 15.0, 0.625), (22.5, 15.0, 0.75)], mode="slots")
        self.assertTrue(p.ok, p.errors)

    def test_half_slots_end_to_end(self):
        # the window's Start probing in half-slot mode: each slot's open side comes from the stock box
        design = [sv[:3] for sv in SLOTS_B]
        p = self.prep(design=design, mode="slots")
        self.assertTrue(p.ok, p.errors)
        vals = va._sbp_value_lines(self.run_file())
        self.assertEqual([vals[f"open{i}"] for i in range(1, 7)], ["1", "2", "0", "0", "0", "0"])
        self.assertEqual(va.load_session()["mode"], "slots")
        self.assertIn("MSGBOX(Slot 1 of 2 - width 0.625 - round end X 1.5 Y 15 in - opens to X min. Jog the ball "
                      "OUTSIDE the opening in line with the slot - under 1 in out - below the bay floor.", self.run_file())
        self.assertIn("MSGBOX(Slot 2 of 2 - width 0.75 - round end X 22.5 Y 15 in - opens to X max.", self.run_file())
        vm, m, _, _ = slot_run(0.9, (1.0, -0.5), text=self.run_file())
        self.assertEqual(m.collisions, [])
        self.assertTrue(any(s.startswith("Slot 2 of 2") for s in vm.ui.messages))
        with open(va.DEFAULT_DATA, "w") as fh:
            fh.write(data_text(vm))
        r = va.align_batch(va.DEFAULT_DATA, [self.job], design)[0][1]
        self.assertTrue(r.ok, r.report)
        self.assertAlmostEqual(r.rotation_deg, 0.9, delta=0.01)
        # a hole run never carries open sides
        self.prep()
        self.assertEqual(va._sbp_value_lines(self.run_file())["open1"], "0")

    def test_slots_need_the_stock_box(self):
        fus = os.path.join(self.tmp, "fusion.sbp")
        shutil.copy(FUSION, fus)
        p = self.prep(jobs=[fus], design=[sv[:3] for sv in SLOTS_B], mode="slots")
        self.assertFalse(p.ok)
        self.assertTrue(any("stock box" in e for e in p.errors), p.errors)
        p = self.prep(jobs=[fus], design=[sv[:3] for sv in SLOTS_B], stock=STOCK24, mode="slots")
        self.assertTrue(p.ok, p.errors)

    def test_job_names_outside_latin1_do_not_break_start(self):
        # SB3 files are Latin-1. A file name with an en dash used to crash Start after the old
        # outputs and probe data were already deleted.
        job = os.path.join(self.tmp, "side B – final.sbp")
        shutil.copy(JOB, job)
        p = self.prep(jobs=[job])
        self.assertTrue(p.ok, p.errors)
        text = open(va.RUN_FILE, "rb").read().decode("latin-1")
        self.assertIn("side B ? final.sbp", text)                                  # only in the comment line
        self.assertEqual(sbp_lint_problems(text), [])

    def test_problems_are_found_before_probing(self):
        # a symmetric layout cannot tell a wrong flip apart
        p = self.prep(design=[(12.0, 1.0, 0.5), (12.0, 29.0, 0.5)])
        self.assertFalse(p.ok)
        self.assertIn("looks the same after a half turn", p.checks[0][1].fails[0])
        self.assertFalse(os.path.exists(va.RUN_FILE))
        self.assertIsNone(va.read_sidecar_full(self.job)[0])
        # Fusion files need the stock box; with it the check passes
        fus = os.path.join(self.tmp, "fusion.sbp")
        shutil.copy(FUSION, fus)
        design = [(1.0, 15.0, 0.5), (23.0, 15.0, 0.625)]
        p = self.prep(jobs=[fus], design=design)
        self.assertFalse(p.ok)
        self.assertIn("no material size", p.checks[0][1].fails[0])
        p = self.prep(jobs=[fus], design=design, stock=(0.0, 0.0, 24.0, 30.0))
        self.assertTrue(p.ok, p.checks[0][1].fails)
        # input mistakes
        for design, msg in (([(1.0, 1.0, 0.5)], "2 to 6"), ([(1.0, 1.0, None), (5.0, 1.0, 0.5)], "diameter"),
                            ([(1.0, 1.0, 0.15), (5.0, 1.0, 0.16)], "at least 0.04 larger")):
            p = self.prep(design=design)
            self.assertTrue(any(msg in e for e in p.errors), (design, p.errors))
        p = self.prep(jobs=[os.path.join(self.tmp, "nope.sbp")])
        self.assertTrue(any("not found" in e for e in p.errors))
        p = self.prep(pin=9)
        self.assertTrue(any("Probe input" in e for e in p.errors))

    def test_dry_check_never_touches_files(self):
        data = os.path.join(self.tmp, "real.csv")
        truth = [T(0.3, 1.0, 0.5, d[:2]) + (d[2],) for d in DESIGN]
        vm, _, _ = run_probe(truth, [(x + 0.03, y - 0.04, 0.1) for x, y, _ in truth])
        with open(data, "w") as fh:
            fh.write(data_text(vm))
        real = va.run_alignment(data, self.job, DESIGN)
        self.assertTrue(real.ok)
        before = sorted(os.listdir(self.tmp))
        checks = va.precheck_jobs([self.job], DESIGN, None, 0.125)
        self.assertTrue(checks[0][1].ok)
        self.assertEqual(sorted(os.listdir(self.tmp)), before)
        self.assertTrue(os.path.exists(real.out_path))

    def test_start_removes_old_aligned_files_except_in_a_dry_run(self):
        data = os.path.join(self.tmp, "real.csv")
        truth = [T(0.3, 1.0, 0.5, d[:2]) + (d[2],) for d in DESIGN]
        vm, _, _ = run_probe(truth, [(x + 0.03, y - 0.04, 0.1) for x, y, _ in truth])
        with open(data, "w") as fh:
            fh.write(data_text(vm))
        out = va.run_alignment(data, self.job, DESIGN).out_path
        self.assertTrue(self.prep(sim=True).ok)
        self.assertTrue(os.path.exists(out))
        self.assertEqual(va._sbp_value_lines(self.run_file())["sim"], "1")
        # the dry run probes and aligns: its _VDSIM output must leave the real _VD file alone
        vm = TrackingVM(self.run_file(), Machine(holes=[], mode=1), UI(sim_inputs=[(12.1, 1.0, 0.5), (12.0, 29.0, 0.625)]),
                        numfmt=vb_single).run()
        with open(va.DEFAULT_DATA, "w") as fh:
            fh.write(data_text(vm))
        sim = va.align_batch(va.DEFAULT_DATA, [self.job], DESIGN)[0][1]
        self.assertTrue(sim.ok, sim.report)
        self.assertTrue(sim.out_path.endswith("_VDSIM.sbp"))
        self.assertTrue(os.path.exists(out))
        self.assertTrue(self.prep().ok)
        self.assertFalse(os.path.exists(out))                                   # board is about to move
        self.assertFalse(os.path.exists(va.DEFAULT_DATA))                       # and its probe data is void

    def test_mm_jobs_probe_with_the_right_search_distance(self):
        mm = os.path.join(self.tmp, "mm.sbp")
        with open(mm, "w", newline="") as fh:
            fh.write("'UNITS:MM\r\n'Length of material in X = 600\r\n'Length of material in Y = 760\r\n"
                     "IF %(25)=0 THEN GOTO UNIT_ERROR\r\nSA\r\nJZ,5\r\nJ2,100,100\r\nM3,100,100,-5\r\n"
                     "M3,400,600,-5\r\nJZ,5\r\nEND\r\nUNIT_ERROR:\r\nEND\r\n")
        design = [(300.0, 20.0, 12.7), (300.0, 740.0, 15.875)]
        stock = (0.0, 0.0, 600.0, 760.0)
        p = self.prep(jobs=[mm], design=design, stock=stock)
        self.assertTrue(p.ok, (p.errors, [r.fails for _, r in p.checks]))
        self.assertAlmostEqual(float(va._sbp_value_lines(self.run_file())["hole"]), 0.625, places=4)  # inches
        p = self.prep(jobs=[mm], design=[(300.0, 20.0, 3.0), (300.0, 740.0, 3.5)], stock=stock)
        self.assertTrue(any("at least 1.016 larger" in e for e in p.errors), p.errors)    # 0.04 in, tip 3.175 mm
        p = self.prep(jobs=[mm, self.job], design=design)
        self.assertTrue(any("mix inches and mm" in e for e in p.errors))

    def test_settings_come_from_the_template_then_the_window(self):
        tpl = os.path.join(self.tmp, "tpl.sbp")
        text = open(os.path.join(ROOT, "vd_probe.sbp"), newline="").read()
        text = re.sub(r"(?m)^&vd_tip = 0\.125", "&vd_tip = 0.093", text)
        text = re.sub(r"(?m)^&vd_passes = 2", "&vd_passes = 77", text)          # out of range -> default
        with open(tpl, "w", newline="") as fh:
            fh.write(text)
        m = va.load_machine(template=tpl)
        self.assertEqual((m["tip"], m["passes"]), (0.093, 2))
        va.save_machine(dict(m, pin=4))
        self.assertEqual(va.load_machine(template=tpl)["pin"], 4)
        with self.assertRaises(va.VDError):
            va.render_run(text, {"nosuchsetting": 1})
        out = va.render_run(text, {"tip": 0.1})
        self.assertEqual(va._sbp_value_lines(out)["tip"], "0.1")
        self.assertIn("&vd_tip = &vd_tip * &vd_u", out)                          # only the first line changes

    def test_closing_the_window_leaves_nothing_to_run(self):
        va.write_run_stub("closed")
        vm = VM(self.run_file(), Machine(holes=[]), UI(), numfmt=vb_single).run()
        self.assertEqual(vm.ended, "END")
        self.assertEqual(m_moves(vm), 0)


def m_moves(vm):
    return vm.machine.moves


def sbp_lint_problems(text):
    return risky_string_lines(text)


class TestReadmeDryRun(_Folder):
    """README H1: the dry-run kit in docs/example with the numbers the README gives."""

    def test_h1_numbers_pass(self):
        ex = os.path.join(ROOT, "docs", "example")
        job = os.path.join(self.tmp, "sideB_example.sbp")
        bay = os.path.join(self.tmp, "bays_example.sbp")
        shutil.copy(os.path.join(ex, "sideB_example.sbp"), job)
        shutil.copy(os.path.join(ex, "bays_example.sbp"), bay)
        slots = [(1.5, 15.0, 0.625), (22.5, 15.0, 0.75)]
        p = va.prepare_rough([job], bay, slots, va.slot_box([job], None), self.machine, sim=True,
                             template=os.path.join(ROOT, "vd_probe.sbp"))
        self.assertTrue(p.ok, p.errors)
        vm = VM(self.run_file(), Machine(holes=[], mode=1), UI(sim_inputs=[(0.05, 15.1, 0), (24.03, 15.2, 0)]),
                numfmt=vb_single).run()
        with open(va.ROUGH_DATA, "w") as fh:
            fh.write(data_text(vm))
        res = va.rough_from_session()
        self.assertTrue(res.ok, res.report)
        self.assertTrue(res.out_path.endswith("bays_example_VDROUGHSIM.sbp"))
        p = va.prepare_probing([job], slots, None, self.machine, sim=True, mode="slots",
                               template=os.path.join(ROOT, "vd_probe.sbp"))
        self.assertTrue(p.ok, p.errors)
        vm = VM(self.run_file(), Machine(holes=[], mode=1), UI(sim_inputs=list(slots)), numfmt=vb_single).run()
        with open(va.DEFAULT_DATA, "w") as fh:
            fh.write(data_text(vm))
        r = va.align_batch(va.DEFAULT_DATA, [job], slots)[0][1]
        self.assertTrue(r.ok, r.report)
        self.assertTrue(r.out_path.endswith("sideB_example_VDSIM.sbp"))


class TestPrepareRough(_Folder):

    def test_rough_locate_places_the_bay_file(self):
        bay = os.path.join(self.tmp, "bays.sbp")
        with open(bay, "w") as fh:
            fh.write(BAY_TEXT)
        old = os.path.join(self.tmp, "old_VD.sbp")                # an aligned file from side A
        with open(old, "w") as fh:
            fh.write(f"'==== {va.MARKER} - old\n")
        va.record_output(old)
        p = va.prepare_rough([self.job], bay, SLOTS, STOCK24, self.machine,
                             template=os.path.join(ROOT, "vd_probe.sbp"))
        self.assertTrue(p.ok, p.errors)
        self.assertFalse(os.path.exists(old))                     # a new board position
        text = self.run_file()
        vals = va._sbp_value_lines(text)
        self.assertEqual((vals["rough"], vals["nholes"], vals["after"]), ("1", "2", '"rough"'))
        self.assertTrue(vals["out"].strip('"').endswith("vd_rough_data.csv"))
        self.assertIn("MSGBOX(Slot 1 of 2 - width 0.5 - round end X 12 Y 1.5 in - opens to Y min. "
                      "Jog the bit tip over the centre of its opening", text)
        self.assertEqual(va.load_session()["kind"], "rough")
        deg, tx, ty = 1.5, 2.0, -1.0
        pts = [T(deg, tx, ty, o) for o in ((12.0, 0.0), (12.0, 30.0))]
        vm = VM(text, Machine(holes=[]), UI(parks=[(x, y, 1.0) for x, y in pts]), numfmt=vb_single).run()
        with open(va.ROUGH_DATA, "w") as fh:
            fh.write(data_text(vm))
        self.assertEqual(va.main(["rough", "--nogui"]), 0)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "bays_VDROUGH.sbp")))

    def test_problems_are_found_first(self):
        p = va.prepare_rough([self.job], os.path.join(self.tmp, "nope.sbp"), SLOTS, None, self.machine)
        self.assertFalse(p.ok)
        self.assertTrue(any("stock box" in e for e in p.errors))
        self.assertTrue(any("bay cut file" in e for e in p.errors))


class TestCalibration(_Folder):
    """Probe calibration: finds the input, measures repeatability, effective ball and stylus offset."""

    def cal_run(self, ref=0.75, detect=True, spin=True, mach=None, passes=8):
        p = va.prepare_calibration(self.machine, ref, passes=passes, spin180=spin, detect=detect,
                                   template=os.path.join(ROOT, "vd_probe.sbp"))
        self.assertTrue(p.ok, p.errors)
        text = self.run_file()
        mach = mach or Machine(holes=[(10.0, 5.0, ref)])
        ui = UI(parks=[(10.03, 4.97, 0.1)])
        ui.on_spin = lambda mc: setattr(mc, "ecc", (-mc.ecc[0], -mc.ecc[1]))
        vm = TrackingVM(text, mach, ui, numfmt=vb_single).run()
        return text, vm, ui

    def test_finds_input_and_measures_the_probe(self):
        ecc = (0.004, -0.002)
        mach = Machine(holes=[(10.0, 5.0, 0.75)], probe_pin=3, idle=0, ecc=ecc,
                       other_inputs={1: 1, 2: 1, 4: 0, 5: 1})
        text, vm, ui = self.cal_run(mach=mach)
        vals = va._sbp_value_lines(text)
        self.assertTrue(vals["out"].endswith('vd_cal_data.csv"'))               # job data is never overwritten
        self.assertEqual((vals["cal"], vals["detect"], vals["nholes"], vals["after"]), ("1", "1", "1", '"calresult"'))
        self.assertEqual([c[0].split()[-1] for c in vm.shell], ["calresult"])   # no clean-up of aligned files
        pd = va.parse_probe_data(data_text(vm))
        c = va.analyze_calibration(pd)
        self.assertEqual(c.detected, (3, 1))
        self.assertAlmostEqual(c.ecc[0], ecc[0], delta=0.0005)
        self.assertAlmostEqual(c.ecc[1], ecc[1], delta=0.0005)
        self.assertLess(c.repeat, 0.0015)
        self.assertAlmostEqual(c.eff_tip, 0.125 - 0.01, delta=0.004)             # ball minus trigger travel
        self.assertIn("off the spindle axis", va.calibration_report(c))
        m = va.apply_calibration(self.machine, c)
        self.assertEqual((m["pin"], m["trig"]), (3, 1))
        self.assertAlmostEqual(m["tip"], c.eff_tip, places=4)
        # the calibrated settings then probe a job on input 3
        p = va.prepare_probing([self.job], DESIGN, None, m, template=os.path.join(ROOT, "vd_probe.sbp"))
        self.assertTrue(p.ok)
        truth = [T(0.2, 0.5, 0.5, d[:2]) + (d[2],) for d in DESIGN]
        vm = TrackingVM(self.run_file(), Machine(holes=truth, probe_pin=3, idle=0),
                        UI(parks=[(x + 0.03, y - 0.04, 0.1) for x, y, _ in truth]), numfmt=vb_single).run()
        self.assertEqual(vm.ended, "END")
        self.assertIn("END, 2", data_text(vm))

    def test_no_probe_found(self):
        mach = Machine(holes=[(10.0, 5.0, 0.75)], probe_pin=3, idle=1)
        p = va.prepare_calibration(self.machine, 0.75, template=os.path.join(ROOT, "vd_probe.sbp"))
        self.assertIn("MSGBOX(The calibration hole - measured diameter 0.75 in.", self.run_file())
        ui = UI(parks=[(10.0, 5.0, 0.1)])
        ui.msgbox_orig = ui.msgbox
        def msgbox(vm, body, btype, title):                                      # operator never touches the tip
            ui.messages.append(body)
            return "OK"
        ui.msgbox = msgbox
        vm = TrackingVM(self.run_file(), mach, ui, numfmt=vb_single).run()
        self.assertTrue(any("No input changed" in m for m in ui.messages))
        self.assertEqual(mach.moves, 0)

    def test_records_and_limits(self):
        hdr = "VD,1,0,0,0.125,0.75,1,4,0,1,0,0\n"
        with self.assertRaises(va.ProbeDataError):
            va.parse_probe_data(hdr + "END,1\n")                                 # one hole only with CAL
        with self.assertRaises(va.VDError):
            va.analyze_calibration(va.parse_probe_data(
                "VD,1,0,0,0.125,0.75,2,1,0,1,0,0\nT,1,0,1,1,1,1,0,0,0,0\nT,1,0,1,1,-1,0,0,0,0,0\n"
                "T,1,0,1,2,1,0,1,0,0,0\nT,1,0,1,2,-1,0,0,0,0,0\nT,2,0,1,1,1,1,0,0,0,0\nT,2,0,1,1,-1,0,0,0,0,0\n"
                "T,2,0,1,2,1,0,1,0,0,0\nT,2,0,1,2,-1,0,0,0,0,0\nEND,2\n"))
        self.assertFalse(va.prepare_calibration(self.machine, 0.0).ok)
        self.assertFalse(va.prepare_calibration(self.machine, 0.13).ok)          # smaller than ball + 0.04

    def test_mm_machine(self):
        # SB3 set to mm: the routine scales the reference like every other distance
        mach = Machine(holes=[(254.0, 127.0, 19.05)], tip=3.175, units=1, pretravel=0.1, overtravel=3.0,
                       jitter=0.004, start=(0.0, 0.0, 25.0))
        p = va.prepare_calibration(self.machine, 0.75, passes=4, spin180=False, detect=False,
                                   template=os.path.join(ROOT, "vd_probe.sbp"))
        self.assertTrue(p.ok)
        vm = TrackingVM(self.run_file(), mach, UI(parks=[(254.5, 126.6, 2.5)]), numfmt=vb_single).run()
        self.assertEqual(mach.collisions, [])
        c = va.analyze_calibration(va.parse_probe_data(data_text(vm)))
        self.assertEqual(c.units, "mm")
        self.assertAlmostEqual(c.ref, 19.05, places=3)
        self.assertAlmostEqual(c.eff_tip, 3.175 - 0.2, delta=0.1)
        m = va.apply_calibration(self.machine, c)
        self.assertAlmostEqual(m["tip"], c.eff_tip / 25.4, places=4)         # saved in inches

    def test_simulated_or_poor_calibration_changes_nothing(self):
        c = va.Calibration(ref=0.75, passes=4, repeat=0.0001, eff_tip=0.135, nominal_tip=0.125, roundness=0.0,
                           ecc=None, detected=(2, 1), sim=True, units="in", advice=[])
        self.assertEqual(va.apply_calibration(self.machine, c), self.machine)
        c.sim = False
        c.repeat = 0.004
        m = va.apply_calibration(self.machine, c)
        self.assertEqual((m["pin"], m["tip"]), (2, self.machine["tip"]))       # input yes, ball no

    def test_job_session_survives_calibrations(self):
        va.save_session({"kind": "probe", "jobs": [self.job], "holes": [list(d) for d in DESIGN]})
        for _ in range(3):
            self.assertTrue(va.prepare_calibration(self.machine, 0.75).ok)
        self.assertEqual(va.load_session()["keep"]["jobs"], [self.job])

    def test_dry_run_calibration(self):
        p = va.prepare_calibration(self.machine, 0.75, sim=True, template=os.path.join(ROOT, "vd_probe.sbp"))
        self.assertEqual(va._sbp_value_lines(self.run_file())["detect"], "0")    # nothing to detect in a dry run
        vm = TrackingVM(self.run_file(), Machine(holes=[], mode=1), UI(sim_inputs=[(1.0, 2.0, 0.75)] * 2),
                        numfmt=vb_single).run()
        c = va.analyze_calibration(va.parse_probe_data(data_text(vm)))
        self.assertTrue(c.sim)
        self.assertIn("SIMULATED", va.calibration_report(c))


try:
    import tkinter as _tk
    _tk.Tk().destroy()
    HAVE_TK = True
except Exception:                         # no tkinter or no display: window tests are skipped
    HAVE_TK = False


def ttk_Button():
    from tkinter import ttk
    return ttk.Button


def vd_app_red():
    import vd_app
    return vd_app.RED


@unittest.skipUnless(HAVE_TK, "tkinter with a display is needed for the window tests")
class TestWindow(_Folder):
    def app(self, mode, **kw):
        import tkinter as tk
        import vd_app
        a = vd_app.App(mode, root=tk.Tk(), auto=False, **kw)
        a.info = a.error = lambda *x: None
        a.ask_yes_no = lambda *x: True
        return a

    def test_prepare_probe_align(self):
        fus = os.path.join(self.tmp, "fusion.sbp")
        shutil.copy(JOB, fus)
        a = self.app("prepare")
        a.pick_files = lambda: [self.job, fus]
        a.on_add_files()
        a._set_holes(DESIGN)
        self.assertTrue(a.on_check())
        a.mvars["passes"].set("3")
        a.on_start()
        self.assertEqual(a.result_code, 0)
        truth = [T(0.6, 1.2, 0.4, d[:2]) + (d[2],) for d in DESIGN]
        vm = TrackingVM(self.run_file(), Machine(holes=truth),
                        UI(parks=[(x + 0.03, y - 0.04, 0.1) for x, y, _ in truth]), numfmt=vb_single).run()
        # the jog prompt names the hole and its design position - not "the next hole"
        said = [m for m in vm.ui.messages if m.startswith("Hole ")]
        self.assertTrue(said[0].startswith("Hole 1 of 2 - diameter 0.5 at X 12 Y 1 in. Jog the probe tip"), said)
        self.assertTrue(said[1].startswith("Hole 2 of 2 - diameter 0.625 at X 12 Y 29 in."), said)
        with open(va.DEFAULT_DATA, "w") as fh:
            fh.write(data_text(vm))
        b = self.app("align")
        self.assertEqual(b.jobs, [self.job, fus])                               # remembered from step 1
        b.on_align()
        self.assertEqual([b.tree.item(j)["values"][1] for j in b.jobs], ["PASS", "PASS"])
        self.assertTrue(os.path.exists(va.default_out_path(fus, False)))
        b.root.destroy()

    def test_close_without_start_and_calibration(self):
        a = self.app("prepare")
        a.on_close()
        self.assertIn("END", self.run_file())
        a = self.app("prepare")
        a.on_calibrate()
        a.cal_ref.set("0.75")
        a.on_start_calibration()
        self.assertEqual(a.result_code, 0)
        mach = Machine(holes=[(10.0, 5.0, 0.75)], probe_pin=2)
        ui = UI(parks=[(10.02, 5.03, 0.1)])
        vm = TrackingVM(self.run_file(), mach, ui, numfmt=vb_single).run()
        with open(va.CAL_DATA, "w") as fh:
            fh.write(data_text(vm))
        c = self.app("calresult")
        self.assertIn("Probe input        : 2", c.text.get("1.0", "end"))
        c.on_save_cal()
        self.assertEqual(va.load_machine()["pin"], 2)
        c.root.destroy()

    def test_align_again_from_the_prepare_window(self):
        # C80 again after probing: the prepare window offers the probe data on file and becomes the results window
        truth = [T(0.3, 1.0, 0.5, d[:2]) + (d[2],) for d in DESIGN]
        vm, _, _ = run_probe(truth, [(x + 0.03, y - 0.04, 0.1) for x, y, _ in truth])
        va.save_session({"kind": "probe", "jobs": [self.job], "holes": [list(d) for d in DESIGN],
                         "prepared": time.time() - 30})                    # Start was clicked before probing
        with open(va.DEFAULT_DATA, "w") as fh:
            fh.write(data_text(vm))
        a = self.app("prepare")
        self.assertTrue(a.data_row.winfo_manager())
        a.switch_to_align()
        self.assertEqual((a.mode, a.root.title()), ("align", "Virtual Dowel - results"))
        self.assertEqual(a.tree.item(self.job)["values"][1], "PASS")
        self.assertTrue(os.path.exists(va.default_out_path(self.job, False)))
        self.assertIn("nothing to run", self.run_file())                     # ShopBot runs nothing after the window
        self.assertNotIn("MSGBOX", self.run_file())
        a.on_close()
        self.assertNotIn("MSGBOX", self.run_file())
        # data older than the last Start belongs to an earlier board position: not offered
        os.utime(va.DEFAULT_DATA, (time.time() - 60, time.time() - 60))
        va.save_session({"kind": "probe", "jobs": [self.job], "holes": [list(d) for d in DESIGN],
                         "prepared": time.time()})
        b = self.app("prepare")
        self.assertFalse(hasattr(b, "data_row"))
        b.root.destroy()

    def test_a_changed_probe_setting_is_kept_on_close(self):
        a = self.app("prepare")
        a.mvars["tip"].set("0.13")
        a.on_close()
        self.assertEqual(va.load_machine()["tip"], 0.13)

    def test_stale_probe_data_is_not_aligned(self):
        truth = [T(0.3, 1.0, 0.5, d[:2]) + (d[2],) for d in DESIGN]
        vm, _, _ = run_probe(truth, [(x + 0.03, y - 0.04, 0.1) for x, y, _ in truth])
        with open(va.DEFAULT_DATA, "w") as fh:
            fh.write(data_text(vm))
        os.utime(va.DEFAULT_DATA, (time.time() - 60, time.time() - 60))
        va.save_session({"kind": "probe", "jobs": [self.job], "holes": [list(d) for d in DESIGN],
                         "prepared": time.time()})                         # Start was clicked after it
        b = self.app("align")
        b.on_align_auto()
        b.on_align()
        self.assertTrue(b.stale)
        self.assertFalse(os.path.exists(va.default_out_path(self.job, False)))
        b.root.destroy()

    def test_half_slots_rough_locate_then_probing(self):
        bay = os.path.join(self.tmp, "bays.sbp")
        with open(bay, "w") as fh:
            fh.write(BAY_TEXT)
        a = self.app("prepare")
        a.pick_files = lambda: [self.job]
        a.on_add_files()
        a.loc_var.set("slots")
        a._on_locate_mode()
        a._set_holes(SLOTS)
        a.bay_var.set(bay)
        a.on_start_rough()
        self.assertEqual(a.result_code, 0)
        vals = va._sbp_value_lines(self.run_file())
        self.assertEqual((vals["rough"], vals["nholes"]), ("1", "2"))
        pts = [T(1.0, 1.5, -0.5, o) for o in ((12.0, 0.0), (12.0, 30.0))]
        vm = VM(self.run_file(), Machine(holes=[]), UI(parks=[(x, y, 1.0) for x, y in pts]), numfmt=vb_single).run()
        with open(va.ROUGH_DATA, "w") as fh:
            fh.write(data_text(vm))
        r = self.app("rough")
        self.assertIn("Bay file placed", r.status.cget("text"))
        self.assertIn("bays_VDROUGH.sbp", r.text.get("1.0", "end"))
        self.assertEqual(r.result_code, 0)
        r.root.destroy()
        b = self.app("prepare")                                     # C80 again, with the probe in
        self.assertEqual(b.loc_var.get(), "slots")
        self.assertEqual(b.bay_var.get(), bay)
        self.assertEqual(b.jobs, [self.job])
        b.on_start()
        self.assertEqual(b.result_code, 0)
        vals = va._sbp_value_lines(self.run_file())
        self.assertEqual((vals["rough"], vals["open1"], vals["open2"]), ("0", "3", "4"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "bays_VDROUGH.sbp")))   # the new session removed it

    def current_step(self, a):
        return [w.cget("text") for w in a.steps_bar.winfo_children() if w.cget("bg") == "#2f5d8a"][0]

    def test_the_window_says_what_to_do_for_each_method(self):
        a = self.app("prepare")
        report = lambda: a.text.get("1.0", "end")                               # noqa: E731
        self.assertIn("Through holes - what to do now", report())
        self.assertEqual(len(a.steps_bar.winfo_children()), 4)
        self.assertEqual(self.current_step(a), "1  Prepare")
        self.assertEqual(a.size_header.cget("text"), "Diameter")
        self.assertIn("lift a cutter", a.method_note.cget("text"))
        a.loc_var.set("slots")
        a._on_locate_mode()
        self.assertIn("Fit the BAY CUTTER and click Start rough locate", report())
        self.assertEqual([w.cget("text") for w in a.steps_bar.winfo_children()],
                         ["1  Prepare", "2  Rough locate", "3  Cut bays", "4  Probe", "5  Align", "6  Cut side B"])
        self.assertEqual((a.size_header.cget("text"), a.add_hole_btn.cget("text")), ("Width", "+ Add slot"))
        # buttons read in work order, left to right: Check, rough locate, probing, close
        order = [w.cget("text") for w in reversed(a.check_btn.master.pack_slaves()) if isinstance(w, ttk_Button())]
        self.assertEqual(order, ["Check", "Start rough locate (bay cutter fitted)", "Start probing (probe fitted)",
                                 "Close without starting"])
        a._set_holes([(1.5, 15.0, None), (22.5, 15.0, 0.75)])
        self.assertIn("slot 1: enter its width", a.read_holes()[1])
        a.loc_var.set("through")
        a._on_locate_mode()
        self.assertNotIn("Start rough locate (bay cutter fitted)",
                         [w.cget("text") for w in a.check_btn.master.pack_slaves() if isinstance(w, ttk_Button())])
        self.assertIn("Through slots - what to do now", report())
        a.root.destroy()

    def test_centre_method_in_the_window(self):
        a = self.app("prepare")
        radios = [w.cget("text") for w in a.method_row.winfo_children() if w.winfo_class() == "TRadiobutton"]
        self.assertEqual(radios, ["Through holes", "Half slots", "Through slots", "Centre and line"])
        a.loc_var.set("centre")
        a._on_locate_mode()
        self.assertIn("Centre and line - what to do now", a.text.get("1.0", "end"))
        self.assertEqual([w.cget("text") for w in a.steps_bar.winfo_children()],
                         ["1  Prepare", "2  Probe", "3  Align", "4  Cut"])
        self.assertTrue(a.ref_frame.winfo_manager())                      # bores / notches choice is shown
        self.assertEqual((a.probe_mode(), a.size_header.cget("text")), ("holes", "Diameter"))
        a.ref_var.set("through")
        a._on_locate_mode()
        self.assertEqual((a.probe_mode(), a.size_header.cget("text")), ("through", "Width"))
        self.assertTrue(a.holes_frame.cget("text").startswith("References"))
        self.assertEqual(a.jobs_frame.cget("text"), "Job files for the part - one per tool")   # no side B here
        self.assertEqual(a.stock_frame.cget("text"), "Part outline - job coordinates")
        buttons = [w.cget("text") for w in a.check_btn.master.pack_slaves() if isinstance(w, ttk_Button())]
        self.assertNotIn("Start rough locate (bay cutter fitted)", buttons)
        a.loc_var.set("holes")
        a._on_locate_mode()
        self.assertFalse(a.ref_frame.winfo_manager())
        self.assertTrue(a.jobs_frame.cget("text").startswith("Side-B job files"))
        a.root.destroy()
        # the results screen remembers the method from the session
        va.save_session({"kind": "probe", "mode": "through", "fit": "centre", "jobs": [self.job],
                         "holes": [[-2.0, 0.0, 0.5], [2.0, 0.0, 0.5]], "stock": None, "sim": False, "prepared": 0})
        a = self.app("align")
        self.assertEqual((a.method, a.probe_mode()), ("centre", "through"))
        a.root.destroy()

    def test_after_a_rough_locate_the_next_step_is_probing(self):
        va.save_session({"kind": "rough", "mode": "slots", "jobs": [self.job], "bay": os.path.join(self.tmp, "bays.sbp"),
                         "holes": [list(d) for d in SLOTS], "stock": list(STOCK24), "key": None})
        with open(va.ROUGH_DATA, "w") as fh:
            fh.write(rough_text([(12.0, 0.0), (12.0, 30.0)]))
        a = self.app("prepare")
        self.assertEqual(a.loc_var.get(), "slots")
        self.assertEqual(self.current_step(a), "4  Probe")
        self.assertIn("bays_VDROUGH.sbp", a.text.get("1.0", "end"))
        a.root.destroy()

    def test_results_screen_names_slots(self):
        design = [sv[:3] for sv in SLOTS_B]
        va.save_session({"kind": "probe", "mode": "slots", "jobs": [self.job], "holes": [list(d) for d in design],
                         "stock": None, "prepared": time.time() - 60})
        vm, _, _, _ = slot_run(0.5, (1.0, 1.0))
        with open(va.DEFAULT_DATA, "w") as fh:
            fh.write(data_text(vm))
        b = self.app("align")
        self.assertIn("2 slots", b.probe_info.cget("text"))
        self.assertIn("round-end centre", b.probe_info.cget("text"))
        self.assertTrue(b.holes_frame.cget("text").startswith("Slots"))
        self.assertEqual(self.current_step(b), "5  Align")
        b.on_align()
        self.assertEqual(b.tree.item(self.job)["values"][1], "PASS")
        b.root.destroy()

    def test_size_calculator_window(self):
        a = self.app("prepare")
        a._set_holes([(1.5, 15.0, 0.625), (22.5, 15.0, 0.75)])
        a.on_sizes()
        self.assertEqual(a.size_vars["widths"].get(), "0.625, 0.75")      # taken from the slot table
        for k, val in dict(ball="0.125", stem="0.08", free="1", body="0.8", length="3", thick="3",
                           slot_depth="1.75", slot_length="1.25").items():
            a.size_vars[k].set(val)
        a.update_sizes()
        self.assertTrue(a.size_result.ok, a.size_result.problems)
        self.assertIn("OK", a.size_status.cget("text"))
        self.assertGreater(len(a.size_canvas.find_all()), 20)
        reds = lambda: [i for i in a.size_canvas.find_all()                # noqa: E731
                        if a.size_canvas.type(i) in ("rectangle", "oval", "line")
                        and "outline" in a.size_canvas.itemconfig(i)
                        and a.size_canvas.itemcget(i, "outline") == vd_app_red()]
        self.assertEqual(reds(), [])
        a.size_vars["bay_width"].set("1.5")                                # too narrow for the probe body
        a.update_sizes()
        self.assertFalse(a.size_result.ok)
        self.assertIn("NOT OK", a.size_status.cget("text"))
        self.assertTrue(reds())
        a.size_vars["widths"].set("0.6")
        a.update_sizes()
        self.assertIn("Enter the numbers", a.size_status.cget("text"))
        a.size_vars["widths"].set("0.625, 0.75")
        a.size_vars["free"].set("1.2")
        a._sizes_close()
        self.assertEqual(va.load_sizes_inputs()["free"], 1.2)              # remembered for next time
        a.root.destroy()

    def test_rough_result_without_a_rough_session(self):
        r = self.app("rough")
        self.assertIn("NOT placed", r.status.cget("text"))
        self.assertEqual(r.result_code, 1)
        r.root.destroy()

    def test_button_errors_are_shown(self):
        a = self.app("prepare")
        shown = []
        a.error = lambda t, m: shown.append(m)
        a.root.report_callback_exception(ValueError, ValueError("boom"), None)
        self.assertTrue(shown and "boom" in shown[0])
        a.root.destroy()


if __name__ == "__main__":
    unittest.main()
