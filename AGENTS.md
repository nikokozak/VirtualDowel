# Virtual Dowel - instructions for coding agents

Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

## 1. Purpose

Virtual Dowel aligns side-B jobs for flip milling on a ShopBot CNC router. ShopBot's control software is SB3. Its language is OpenSBP.

1. Side A cuts two or more reference through-holes in the waste. The holes have different diameters.
2. The operator flips the board and clamps it at any angle up to 5 degrees. The operator does not zero X or Y.
3. `vd_probe.sbp` runs in SB3 and finds each hole centre with the digitizing probe.
4. `vd_align.py` fits a 2D rigid transform (rotation and translation, no scale) from the design hole positions to the measured positions. It writes a rotated, shifted copy of each side-B job: `<job>_VD.sbp`.

The PC does the correction, not SB3. Nikolai does not trust SB3 to apply corrections live.

**Scope: Virtual Dowel probes, checks, and aligns (rotates and shifts) the operator's own job files. It never makes toolpaths or cut files.** Rules for cuts become checks and pages in the guide.

The software is proprietary. The repository is private. Every source file has a copyright line. Add the same line to every new file.

## 2. Commands

```bash
# all tests (window tests need tkinter; /usr/bin/python3 on this Mac has Tk 8.5)
/usr/bin/python3 -m unittest discover -s tests
# Homebrew python3 has no tkinter: 4 window tests skip. Fix: brew install python-tk@3.14
python3 -m unittest discover -s tests
# one test
python3 -m unittest tests.test_vd.TestFitMath.test_fit_recovers_rigid_transform
# the window (writes generated files into the repo folder; they are in .gitignore)
/usr/bin/python3 vd_align.py prepare
# half-slot sizes for the Fusion slots and bays (nothing is read from job files)
python3 vd_align.py sizes --ball 0.125 --stem 0.08 --free 1 --body 0.8 --length 3 --thick 3 --slot-depth 1.75 --widths 0.625,0.75
# command line alignment
python3 vd_align.py vd_probe_data.csv --job tests/fixtures/sideB_vcarve.sbp --holes "12,1,0.5;12,29,0.625" --nogui
# zip for the shop PC (tracked files only, so no generated files)
git archive --format=zip --prefix=VirtualDowel/ -o ../VirtualDowel.zip HEAD
```

Expected test result: `OK` (161 tests). Dependencies: Python 3.8+ standard library only, tkinter for the window. Do not add packages. The shop PC must need only a python.org install.

## 3. Environments

| | Development (Mac) | Target (shop PC) |
|---|---|---|
| Runs | Python code, tests, the OpenSBP simulator | SB3, the `.sbp` routines, Python started by SHELL |
| Python | any 3.8+ | python.org 3.14 (install manager, `%LOCALAPPDATA%\Python\pythoncore-3.14-64`) |
| Folder | this repo | `C:\SbParts\VirtualDowel` in the README; Nikolai has also used `C:\Users\itpstudent\Downloads\VirtualDowel` |

You cannot run SB3 here. These paths never run on the Mac: `pythonw.exe` selection, `GetLongPathNameW`, the custom-cut folder `C:\SbParts\Custom`, `venv\Scripts`. Results from the real machine come only from Nikolai, as files, logs and screenshots.

## 4. Architecture

```
vd_align.py      core: probe data parser, hole centres, rigid fit, checks, OpenSBP rewriter,
                 runtime-check emitter, reports, state, installer, prepare, calibration, CLI
vd_app.py        tkinter window, modes prepare / align / calresult. Thin layer over vd_align.
vd_probe.sbp     the probing routine. It is a TEMPLATE: render_run fills its &vd_<key> lines.
                 It also runs alone with FP.
vd_selftest.sbp  offline check of SB3 features (Preview, no motion). The header refers to its
                 OPEN lines by number (19, 22, 27). Keep them on those lines.
vd_align.bat     double-click launcher; logs to vd_launch.log. SB3 never uses it.
vd_install.bat   runs vd_align.py --install
docs/guide.html  operator guide for thick stock (half slots): Fusion setup, sizes, probing; offline, no external files
docs/thick-stock-plan.md  design plan and status of the thick-stock modes
tests/test_vd.py   unittest suite
tests/sbp_vm.py    OpenSBP interpreter plus a simulated ShopBot and probe
tests/sim_sbp.py   independent toolpath sampler that verifies the rewriter
tests/fixtures/    sideB_vcarve.sbp, sideB_fusion.sbp (LF line endings)
```

Data flow:

1. **Start.** Operator types `C80`. The custom cut `C:\SbParts\Custom\Custom80.sbc` (or the next free number to 89) SHELLs `pythonw vd_align.py prepare` with SYNC. The window opens.
2. **Prepare** (`prepare_probing`). The operator enters side-B files, holes (X, Y, diameter in side-B design coordinates), a stock box for Fusion files, and probe settings. *Check* runs `precheck_jobs`: a dry alignment against synthetic perfect probe data. *Start probing* deletes old outputs and probe data, writes sidecars, session and machine settings, renders `vd_run.sbp`, and exits. The custom cut then runs `FP,<folder>\vd_run.sbp`. If the operator closes the window, `vd_run.sbp` is a stub that does nothing.
3. **Probe** (`vd_run.sbp`). Finger test. For each hole: keypad (SK) to park about 0.1 in above the centre; guarded plunge; one coarse pass; N fine passes (+X, -X, +Y, -Y; fast touch, back off, slow touch); optional spin-180. It writes `vd_probe_data.csv`, then SHELLs `vd_align.py align`.
4. **Align** (`align_batch` -> `run_alignment` per file). PASS writes `<job>_VD.sbp` and `<job>_VD_report.txt`. FAIL writes no `_VD` file and deletes an older one.
5. **Cut.** Operator fits the cutter, zeroes Z only, and runs each `_VD.sbp`. Each file checks at run time that X/Y zero, units, and FP offset/scale did not change since probing.
6. **Calibrate** (optional, `prepare_calibration` / `analyze_calibration`). Finds the probe input, probes one known hole 10 times at 0 and 180 degrees, reports repeatability, effective ball and stylus offset. *Save* writes `vd_machine.json` and syncs the template. It writes `vd_cal_data.csv` and never touches job data or outputs.

Key functions in `vd_align.py`: `parse_probe_data`, `compute_holes`, `fit_rigid`, `align`, `hole_checks`, `mirror_alternatives`, `SbpTransformer`, `build_output`, `run_alignment`, `render_run`, `prepare_probing`, `install_paths`.

Half-slot mode (thick stock; see `docs/thick-stock-plan.md`): a half slot is a hole with one open side at the stock edge. `compute_holes(pd, theta)` gives its round-end centre (`_slot_pass`); `open_side` / `slot_opening` come from the stock box; `run_rough` places the Fusion bay cut file from the jogged slot openings and writes `_VDROUGH.sbp` (never for part files: `bay_reach` check). Status: Python (phase 1), the routine in `vd_probe.sbp` (phase 2: `vd_probe_slot`, `&vd_open1..6`), the size calculator (`slot_bay_sizes`, CLI word `sizes`) and the rough locate (phase 3: `&vd_rough`, `prepare_rough`, word `rough`, key notch via `fit_lines`) are done and tested in the simulator (`SlotStock` in `tests/sbp_vm.py`, which also checks stylus and body collisions); the window has the minimum for a machine test (Locate with, bay file, Start rough locate, rough-result window, Guide) and a visual size calculator (*Sizes...*: live section and top-view drawings; inputs remembered in `vd_machine.json` under `sizes`); README section 3b has the machine tests H0-H3 and `docs/example` the dry-run kit. Not run on the machine.

Generated files (never commit; already in `.gitignore`): `venv/`, `vd_start.sbp`, `vd_run.sbp`, `vd_state.json`, `vd_machine.json`, `vd_settings.json`, `vd_probe_data.csv`, `vd_cal_data.csv`, `vd_rough_data.csv`, `vd_launch.log`, `*_VD.sbp`, `*_VDSIM.sbp`, `*_VDROUGH.sbp`, `*_report.txt`, `*.vdholes.txt`, `vd_selftest_*.txt`.

## 5. Hard SB3 constraints (confirmed on the real machine)

Each of these caused a real failure. Do not break them. Tests enforce most of them: `test_only_documented_then_actions`, `test_every_pause_is_whole_seconds`, `test_no_operator_characters_in_quoted_text` (`risky_string_lines`), `test_installer_variables_are_not_reused`, and the install/path tests.

- [ ] SHELL starts `.exe` programs. It does not start `.bat` files.
- [ ] SHELL deletes every `~` and puts spaces around every `/`. Paths given to SB3 contain only letters, digits, `_`, `.`, `:` and `\`. Arguments are plain words (`prepare`, `align`, `calresult`, `rough`, `newsession`, `shelltest`).
- [ ] In an assignment, quoted text with `- + * / =` is calculated as arithmetic (`&m = "a-b+c"` gives "Undefined variable: A"). These characters are safe only in MSGBOX, PRINT and INPUT prompts.
- [ ] A timed `PAUSE` under 1 second stops the file in Move mode. Use whole seconds. Preview does not show this.
- [ ] `%(6)`/`%(7)` do not follow the tool. They hold the table position of the current X/Y zero. Use `%(1)`/`%(2)`/`%(3)` for the tool position (rounded to 0.001). *Not confirmed:* `%(6)`/`%(7)` change when X/Y is zeroed again. The runtime zero check depends on this.
- [ ] MSGBOX body: no commas, apostrophes, parentheses or colons. The answer is in `&msganswer`: `OK`, `Cancel`, `Yes`, `No`.
- [ ] Use `IF … THEN` only with `GOTO`, `GOSUB`, `&var = <single value>`, or `C7`. Do not use `<>`. Do not use `THEN WRITE`.
- [ ] `ON INP(&pin,&state) GOSUB` fires only on a change. Check that the input reads idle before you arm it.
- [ ] Also confirmed: 6-deep GOSUB; INPUT with three values; OPEN with a literal or a variable absolute path; `%(22)` = 1 in Preview; `%(61)`/`%(62)` = 0 and `%(66)`/`%(67)` = 1 with no FP offset/scale; SB3 makes created file names upper case; the date in `%(146)` contains commas.

Rules for any `.sbp` code that you write or generate:

- CRLF line endings, byte for byte. `.gitattributes` is `* -text`. Never let a tool convert line endings.
- Assign every variable before use.
- After a plunge, every error path lifts the probe out before the routine ends or asks for a new jog.
- SIM mode never commands motion.
- Installer-set variables are assigned exactly once.
- Look for name collisions: `&vd_py` is the probe Y position, so the Python path is `&vd_pyexe`.

## 6. Safety invariants

- The script deletes or overwrites an output only if Virtual Dowel wrote it: the first line is the aligned-file header (`is_aligned_file`). An occupied output name stops the alignment of that file. It never overwrites job files, sidecars, or custom cuts that are not Virtual Dowel's.
- `_VD` is written only on PASS. FAIL deletes the older `_VD` of that job.
- Pre-checks (`dry=True`) write nothing and delete nothing.
- A simulated run never removes real outputs. A calibration never touches job data or outputs.
- A new real probing session invalidates old outputs and old probe data (`new_session`, `retire_other_runs`, the data hash in `vd_state.json`).
- An aligned file stops if X/Y zero, units or the FP offset/scale changed since probing. `_VDSIM` files run only in Preview.
- Distances are in inches internally and scale by 25.4 for mm data. Probe settings are in inches. Job files can be mm. Check every place where a value moves between job units, SB3 units and settings units. (An unconverted mm search distance was a real bug.)
- Any change to `SbpTransformer` must pass `sim_sbp.compare` on both fixtures at several rotations.

## 7. Testing

- `tests/sbp_vm.py` models: `%(1..3)` rounded to 3 decimals; `%(6)/%(7)` constant at the zero position; probe pretravel, latency and jitter; the probe on any input pin; finger presses; stylus eccentricity that flips with `ui.on_spin`; PAUSE under 1 s raises; MSGBOX/INPUT/SK through a scripted `UI`; SHELL and FP are recorded, not run.
- The VM is strict: an unknown statement raises. When you add an OpenSBP statement to a routine, add it to the VM only with the semantics that the handbook or the machine confirms.
- **When SB3 surprises you, change the VM to match the machine first. Then fix the code.** Bugs went unseen when the VM had the same wrong assumption as the code (`%(6)`/`%(7)`, sub-second PAUSE).
- Static checks cover: THEN actions, variables used before assignment, PAUSE values, quoted strings with operators, installer variables, self-test OPEN line numbers, copyright lines.
- Each new behaviour gets at least one test. Each new `.sbp` routine runs end to end in the VM, including its error paths.

## 8. Data formats

Probe data CSV (numbers as SB3 writes them, for example `.333`):

```
VD,1,units,sim,tip,hole,nholes,passes,spin180,pin,trig,mode   header, 11 fields after the tag
CFG,depth,margin,backoff,fast,slow,zspd
DT,<%(146)> <%(147)>          free text (the date has commas)
CAL,<reference diameter>      calibration only (then nholes may be 1)
DET,<pin>,<deflected state>   input detection only
T,hole,orient,pass,axis,sign,x,y,z,bx,by    one per touch; pass 0 = coarse; bx/by = %(6)/%(7)
H,hole,orient,xc,yc,zp        routine's running estimate (not used)
O,hole,axis,sign              half slot: the hole opens toward (axis, sign); no touch is made that way
END,<nholes>                  no END = aborted run
```

Rough locate data `vd_rough_data.csv` (half-slot mode, side B, before the bay cut; written by `vd_probe.sbp` with `&vd_rough = 1`): `VR,1,units,sim,n,mode`, `DT,...`, one `R,i,x,y,bx,by` per jogged opening (the slots in the listed order, then the key notch if cut), `END,n`.

- Sidecar `<job>.sbp.vdholes.txt`: `holeN = x, y, d` lines and an optional `stock = xmin, ymin, xmax, ymax`.
- `vd_state.json`: `outputs` (`[{path, data_id}]`), `session` (`{kind: probe|cal, jobs, holes, stock, sim, prepared, keep}`), `last_dir`.
- `vd_machine.json`: `probe` (the `PROBE_FIELDS` values) and `calibration` (last record).
- `vd_settings.json`: optional overrides of `DEFAULTS` thresholds.

## 9. Fusion 360 conventions (Nikolai's setups)

- Setup 2's WCS can be at any corner. Hole coordinates and the stock box are in Setup 2's coordinates (the side-B file's coordinates).
- His usual flip: end over end about an X-parallel axis, so Y and Z invert. The flip axis is Y = H/2. The holes go on it.
- Fusion's ShopBot post does not write the stock size, so the window asks for a stock box. The post writes `ON INPUT … MSGBOX` and `JH`, and puts Z zero at the spoilboard.

## 10. Status (2026-10-04)

Confirmed on the machine: all of section 5; SHELL to `python.exe` (also in Preview); a real probing run touches all walls; after the v1.8 `%(6)`/`%(7)` fix, alignment from real probe data works; Nikolai has flip-milled test pieces with the two-hole method.

Not yet measured: a Stage 4 registration step (side-A to side-B feature); the real ball size.

New in v2.0, not yet run on the machine: the C80 custom cut and `C:\SbParts\Custom`; `FP` of `vd_run.sbp` from inside a custom cut; `pythonw.exe` launch; the window on Windows; input detection and calibration. The README Stage 0 (dry run in Preview) is the first planned check.

Known mechanical limit: the old ShopBot MAX lost X/Y steps once on a fast finishing pass. No software check finds a slip during a cut.

In progress: locating modes for stock too thick to drill through with the available Z travel. First: half-depth edge slots with a side-B bay cut. Plan: `docs/thick-stock-plan.md`.

## 11. Working with Nikolai

- He is a research fellow in Fabrication at NYU ITP, an experienced fabricator and engineer (CNC, boatbuilding, embedded systems, Rhino/Grasshopper).
- Answers: short, logical, technical, easy to read. Treat his proposals as hypotheses and challenge them. Show hidden assumptions, trade-offs and failure modes early. Say what is uncertain. Give at least one alternative. Cite sources with links.
- Get agreement on direction before large changes that he did not ask for. Prefer small, verified changes.
- Never say that something works on the machine unless he confirmed it. Say clearly what was tested only in the simulator.
- Machine tests: give short numbered steps, say what he must see, and list the files to send back (`vd_launch.log`, `vd_probe_data.csv`, reports, self-test results).
- Put the version and date in each changed file's header and in the `VERSION` constant in `vd_align.py`. Update `README.md` (the operator manual) with every change in behaviour.
