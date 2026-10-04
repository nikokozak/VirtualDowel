# Brief for Claude Code: set up CLAUDE.md for Virtual Dowel

Paste this into Claude Code, opened in the repository folder (the unzipped `VirtualDowel` folder).

---

## Your task

You are joining an existing project, **Virtual Dowel**, as its development assistant on Nikolai's Mac.

1. **Read the code first:** `vd_align.py`, `vd_app.py`, `vd_probe.sbp`, `vd_selftest.sbp`, `README.md`, `tests/`.
2. **If the folder is not yet a git repository,** initialise one. Commit everything that exists, and check that `.gitattributes` (`* -text`) is in the first commit. Line endings must never be converted (see "Hard constraints").
3. **Run the tests:**
   ```
   python3 -m unittest discover -s tests
   ```
   Expect `OK`. The window tests need tkinter and a display, and are skipped without them. Report the result.
4. **Write `CLAUDE.md`** at the repository root, using this brief together with what you find in the code.
   - **Verify each claim below against the code.** Where they disagree, trust the code and tell Nikolai.
   - Keep CLAUDE.md focused on what a future session needs: commands, architecture, the hard constraints, invariants, conventions, and current status. Aim for about 150–250 lines; do not copy this brief wholesale.
   - The section "What CLAUDE.md should contain" at the end gives the outline.
5. **Do not change any code in this session.** Report anything that looks wrong instead.

---

## 1. What Virtual Dowel is

Virtual Dowel is a flip-milling alignment tool for ShopBot CNC routers running ShopBot's own control software, SB3, which uses the OpenSBP language. It replaces dowel pins and jigs.

1. **Side A:** the job cuts two or more reference through-holes in the waste.
2. **Side B:** the board is flipped and clamped anywhere, at any angle up to a limit. Nothing is squared and X/Y is not re-zeroed.
3. **Probe:** a routine running inside SB3 finds each hole centre with the ShopBot digitizing probe.
4. **Align:** a Python program fits a 2D rigid transform (rotation + translation, no scale) from the design hole positions to the measured ones. It then rewrites each side-B OpenSBP job file into a rotated, shifted copy, `<job>_VD.sbp`, with safety checks built in.

The correction is done on the PC, not live in SB3, by Nikolai's choice: he doesn't trust SB3 to apply changes live.

- **Owner:** Nikolai Kozak. Proprietary; all rights reserved; not open source.
  - Every source file carries a copyright notice, and `LICENSE.txt` holds the full terms.
  - Keep the notice in every new file.
  - The repo is private on GitHub.
- **Built and tested on:** NYU ITP's shop (Nikolai is a research fellow in Fabrication there). It runs on an old ShopBot MAX, with SB3 on a Windows PC.

## 2. Environments

| | Development (Nikolai's Mac, Apple silicon) | Target (shop PC, Windows, SB3) |
|---|---|---|
| Runs | Python code and the test suite | SB3 + the `.sbp` routines + Python via SHELL |
| Python | any 3.8+; tkinter needed for window tests | 3.14.7 at `C:\Users\itpstudent\AppData\Local\Python\pythoncore-3.14-64\` |
| Folder | the git repo | `C:\Users\itpstudent\Downloads\VirtualDowel` (installed with `vd_install.bat`) |

**Not possible on the Mac:** running SB3 or the real `.sbp` files. Windows-only code paths are never exercised locally:
- `pythonw.exe` selection
- `GetLongPathNameW`
- the default custom-cut folder `C:\SbParts\Custom`
- `venv\Scripts`

All OpenSBP behaviour is tested in the repo's own simulator. **Real-machine results come only from Nikolai**, as data files, logs and screenshots he sends back.

**Dependencies:** standard library only, Python ≥ 3.8; tkinter for the window. This is deliberate, because the shop PC must need nothing beyond a python.org install.

## 3. Workflow (v2.0)

1. **Start.** The operator types the custom cut **C80** in SB3.
   - `C:\SbParts\Custom\Custom80.sbc` (or the next free number up to 89) SHELLs `pythonw vd_align.py prepare` (SYNC).
   - That opens the **Virtual Dowel window** (`vd_app.py`) in *prepare* mode.
2. **Prepare.** The operator adds the side-B job files (one per tool) and enters the reference holes (X, Y, diameter in side-B design coordinates). Fusion files, which carry no stock size, also need a stock box. Probe settings sit on the same screen, with depths and speeds under Advanced.
   - **Check** runs every check that doesn't need the probe, through a dry alignment against synthetic "perfect" probe data.
   - **Start probing:**
     1. deletes aligned outputs and probe data from earlier runs (the board moved);
     2. writes the hole sidecars, the session and the machine settings;
     3. renders **`vd_run.sbp`** from the template `vd_probe.sbp` with all values filled in;
     4. exits.
   - The custom cut then continues with `FP,<folder>\vd_run.sbp`.
   - Closing the window without Start leaves `vd_run.sbp` as an empty stub, so nothing runs.
3. **Probe.** `vd_run.sbp` runs in SB3:
   1. finger test;
   2. per hole, the keypad (SK) lets the operator park the tip about 0.1 in over the hole centre;
   3. guarded plunge, a coarse pass, then N fine passes. Each pass touches +X, −X, +Y, −Y; fine touches are fast-touch, back-off, slow-touch;
   4. optional spin-180, where the operator turns the spindle by hand and the hole is probed again to cancel stylus runout;
   5. it writes `vd_probe_data.csv`, then SHELLs `vd_align.py align`.
4. **Align.** The window reopens in *align* mode with the files and holes pre-filled, and aligns all files at once (`align_batch`).
   - Per file: PASS → `<job>_VD.sbp` + `<job>_VD_report.txt`; FAIL → nothing written, and any older `_VD` file for that job is deleted.
5. **Cut.** The operator swaps the probe for the cutter, zeroes **Z only**, and FPs each `_VD.sbp`.
   - Each output re-checks at runtime that X/Y zero, units, and FP offset/scale are unchanged since probing.
6. **Calibrate (optional; once per probe).** In the window, *Calibrate probe…*:
   1. auto-detects the probe input and its deflected state: reads `%(51..58)` at rest, then sees which input changes while the operator holds the tip over;
   2. probes one reference hole of known diameter, 10 passes, at 0° and 180°;
   3. reports repeatability, effective ball diameter (including trigger travel) and stylus offset from the spindle axis;
   4. *Save* stores them in `vd_machine.json` and syncs them into the template.
   - Calibration writes `vd_cal_data.csv` and never touches job data or `_VD` files.

**Fallbacks:**
- `FP` → `vd_start.sbp` does the same as C80.
- Double-clicking `vd_align.bat` opens the window on the last probe data, to re-align.
- The command line takes `--job` (repeatable), `--holes`, `--stock`, `--out` and `--nogui`.

## 4. Repository layout

```
vd_align.py      core: probe data parsing, hole centres, rigid fit, checks, OpenSBP rewriter,
                 runtime-check emitter, reports, state/session, installer, prepare/calibration, CLI
vd_app.py        tkinter window (modes: prepare / align / calresult); thin layer over vd_align
vd_probe.sbp     the probing routine; a TEMPLATE whose &vd_<key> settings lines get filled in;
                 still runnable on its own via FP
vd_selftest.sbp  offline check of SB3 features (Preview, no motion); lines 19/22/27 are OPEN tests
                 referenced by line number in its header - keep them there
vd_align.bat     double-click launcher (finds a working Python, logs to vd_launch.log)
vd_install.bat   runs `vd_align.py --install`
README.md        operator manual (user-facing); keep in sync with behaviour
LICENSE.txt, .gitignore, .gitattributes
tests/test_vd.py   ~110 tests (unittest)
tests/sbp_vm.py    OpenSBP interpreter + simulated ShopBot/probe (see §9)
tests/sim_sbp.py   independent toolpath sampler used to verify the rewriter
tests/fixtures/    sideB_vcarve.sbp, sideB_fusion.sbp
```

**Made at install or run time; never commit these** (already in `.gitignore`):
- `venv/`
- `vd_start.sbp`, `vd_run.sbp`
- `vd_state.json`, `vd_machine.json`, `vd_settings.json`
- `vd_probe_data.csv`, `vd_cal_data.csv`, `vd_launch.log`
- `*_VD.sbp`, `*_VDSIM.sbp`, `*_report.txt`, `*.vdholes.txt`
- the self-test outputs

## 5. Core design (vd_align.py)

**Hole centres (`compute_holes`)**
- Each centre is the midpoint of two opposite touches, so trigger latency, pretravel, backlash and ball size cancel; they only bias the measured diameter.
- Touch positions are the working coordinates `%(1)`/`%(2)`. SB3 reports them rounded to 0.001 in, so passes are averaged.
- `%(6)`/`%(7)` are recorded with each touch only to detect a re-zero during probing (see §7).
- With spin-180, the two orientations are averaged and half their difference is the stylus eccentricity.

**Matching (`align`)**
- Hole identity is by **size**: diameters are compared relative to a common size bias, over every permutation.
- **Symmetric layouts FAIL.** If the layout maps onto itself after a half turn, a board flipped the wrong way would pass unnoticed. This is why the holes must differ in size.
- Also checked: spacing error (2 holes) and residuals (3+ holes).
- Rotation limit: `max_rotation_deg`, default 5.

**Mirror checks (`mirror_alternatives`, `has_mirror_symmetry`)**
- Catch side-A coordinates typed instead of side-B ones, for off-axis holes. They need the stock box, which comes from the VCarve header or is entered for Fusion.
- Holes on the flip axis have identical side-A and side-B coordinates, which avoids the ambiguity.

**`SbpTransformer`**
- Rewrites M2–M5, J2–J5, MX/MY/JX/JY and CG arcs.
- Tracks modal state and SR (relative) mode; mode becomes unknown after labels/GOSUB in SR files.
- Z-only lines stay byte-identical.
- JH/MH pass through with a warning; ON INPUT MSGBOX is allowed.
- Refuses a list of unsafe commands, bare-CR files, and mixed CRLF/LF files.

**`build_output`**
- Adds a header (unit, mode, FP-offset/proportion and zero checks) plus `GOSUB VDALIGN_CHECK` after every custom cut (C#/CN), SK, untimed PAUSE, MSGBOX and INPUT.
- The X/Y zero check compares `%(6)`/`%(7)` with their values at probing, within ±0.002 in.
- Checks only run in Move mode (`%(22)=0`); in Preview only units are checked.

**`run_alignment(..., dry=False)`**
- One job.
- `dry=True` must never write or delete anything; it is used for pre-checks.
- Simulated (SIM) data must never delete real `_VD` files.

**Stale-output protection**
- `vd_state.json` records each output with the probe-data hash it came from.
- `new_session()` deletes all recorded outputs.
- `retire_other_runs()` deletes outputs from other probe runs whenever a real alignment runs.
- Probe data older than 120 min needs confirmation.

**Prepare / calibration**
- `prepare_probing`, `prepare_calibration`, `render_run` (fills `&vd_<key>` lines; first occurrence only; refuses unknown keys; keeps CRLF), `precheck_jobs` (dry run with `synthetic_probe_text`), `analyze_calibration`, `calibration_usable` (no sim; the ball only when repeat ≤ 0.002 and roundness ≤ 0.005) and `apply_calibration`.
- Settings are in inches. Job files may be mm: the hole diameter is converted to inches for `&vd_hole`, and all selected jobs must share units.

**Installer (`install_paths`)**
- Folder path must contain only letters, digits, `_` and `.` (see §7).
- Makes `venv`, preferring `pythonw.exe`.
- Patches the paths in both `.sbp` files.
- Writes `vd_start.sbp`, a stub `vd_run.sbp` and the custom cut. It never overwrites a custom cut whose first line is not the Virtual Dowel marker, and keeps its existing number on reinstall.

**CLI words:** SB3 cannot pass `-`, `/` or `~` intact (see §7), so SBP files call plain words: `prepare`, `align`, `calresult`, `newsession`, `shelltest`. Flags are lower-cased before parsing.

## 6. Data formats

**Probe data CSV** (written by the routine; numbers in SB3's own format, e.g. `.333` without a leading zero):
```
VD,1,units,sim,tip,hole,nholes,passes,spin180,pin,trig,mode      (11 fields after the tag; header)
CFG,depth,margin,backoff,fast,slow,zspd
DT,<%(146)> <%(147)>        date contains commas - DT is free text
CAL,<reference diameter>    calibration runs only (allows nholes = 1)
DET,<pin>,<deflected state> input detection only
T,hole,orient,pass,axis,sign,x,y,z,bx,by      one per touch; pass 0 = coarse; bx/by = %(6)/%(7)
H,hole,orient,xc,yc,zp      routine's running estimate (informational)
END,<nholes>                missing END = aborted run
```

**Other files**
- **Sidecar `<job>.sbp.vdholes.txt`:** `holeN = x, y, d` lines and an optional `stock = xmin, ymin, xmax, ymax`.
- **`vd_state.json`:** `outputs` (`[{path, data_id}]`), `session` (`{kind: probe|cal, jobs, holes, stock, sim, prepared, keep}`) and `last_dir`.
- **`vd_machine.json`:** `probe` (the `PROBE_FIELDS` values) and `calibration` (the last record).
- **`vd_settings.json`:** optional overrides of the alignment thresholds in `DEFAULTS`.

## 7. Hard constraints: SB3 behaviour confirmed on the real machine

These were found by testing on Nikolai's SB3. Breaking any of them has already caused a real failure. Most are encoded as tests: `test_only_documented_then_actions`, `test_every_pause_is_whole_seconds`, `test_no_operator_characters_in_quoted_text` (via `risky_string_lines`), `test_installer_variables_are_not_reused`, and the install/path tests.

1. **SHELL starts `.exe` programs but not `.bat` files.** SB3 starts the venv's `pythonw.exe`/`python.exe` directly.
2. **SHELL text is altered:** every `~` is deleted, and spaces are put around every `/`. So 8.3 short paths (`DOWNLO~1`) and switches (`/c`, `--flag`) break. Paths given to SB3 must contain only letters, digits, `_`, `.`, `:` and `\`, and arguments must be plain words.
3. **Quoted text containing `- + * / =` in an assignment is evaluated as arithmetic.** `&m = "a-b+c"` gives "Undefined variable: A". Keep such characters out of quoted strings except in MSGBOX, PRINT and INPUT prompts, where they are confirmed fine.
4. **`PAUSE` under 1 second freezes the file in Move mode** until the operator continues it by hand. Preview doesn't wait, so the self-test missed it. Every timed PAUSE must be a whole number of seconds; the VM raises on `PAUSE <1`.
5. **`%(6)`/`%(7)` ("Base Coord") do not follow the tool.** They hold the table position of the current X/Y zero. `%(1)`/`%(2)`/`%(3)` (working location) follow the tool and are rounded to 0.001.
   - *Inferred, not yet confirmed:* `%(6)`/`%(7)` change when X/Y is re-zeroed. The runtime zero check relies on this. If it's false, the check never fires; the risk is losing that protection, not false stops.
6. **MSGBOX body:** no commas, apostrophes, parentheses or colons. The answer arrives in `&msganswer` as `OK`, `Cancel`, `Yes` or `No`.
7. **IF … THEN** is only used with GOTO, GOSUB, `&var = <single value>`, or `C7`. `<>` is untested, so avoid it. (Normal operator precedence was confirmed.)
8. **Other confirmed behaviour:**
   - `ON INP(&pin,&state) GOSUB` interrupts fire only on a *change*, so the routine checks the input reads idle before arming.
   - 6-deep GOSUB works.
   - INPUT takes three values.
   - OPEN with a literal or variable absolute path works.
   - `%(22)` = 1 in Preview; `%(61)`/`%(62)` = 0 and `%(66)`/`%(67)` = 1 with no FP offset/scale.
   - SB3 uppercases created file names, and the date in `%(146)` contains commas.

**Authoring rules that follow** (for any `.sbp` code you write or generate):
- CRLF line endings, byte-for-byte; `.gitattributes` is `* -text`.
- Every variable assigned before use.
- Every error path after a plunge lifts the probe out of the hole before anything else.
- SIM mode never commands motion.
- No `<>`; no `THEN WRITE`.
- Installer-set variables are assigned exactly once.
- Watch name collisions: `&vd_py` is the probe's Y position, which is why the Python path is `&vd_pyexe`.

## 8. Safety invariants: never break these

- Only files the script itself recorded as outputs are ever deleted. Job files, sidecars and other people's custom cuts are never overwritten.
- `_VD` is written only on PASS. FAIL deletes any older `_VD` file of that job, so no stale output survives a failed run.
- Pre-checks (`dry=True`) write and delete nothing.
- A simulated run never removes real outputs. A calibration never touches job data or outputs.
- Starting a new real probing session invalidates the old outputs and probe data.
- Aligned files refuse to run if X/Y zero, units or the FP offset/scale changed since probing.
- Simulated (`_VDSIM`) outputs only run in Preview.
- Any change to the OpenSBP rewriter must be verified with `sim_sbp.compare` (independent geometry check) on both fixtures, at several rotations.
- Distances are in inches internally (scaled ×25.4 for mm data). Watch every place a value crosses between job units, SB3 units and settings units. An mm search distance written unconverted was a real bug, caught in review.

## 9. Testing

- Run: `python3 -m unittest discover -s tests`. Windows-only paths are skipped or simulated.
- **`TestWindow`** needs tkinter and a display. On macOS a python.org Python includes Tk; on Linux CI use `xvfb-run`.
- **`tests/sbp_vm.py` is an OpenSBP interpreter plus a simulated ShopBot and probe.** It models:
  - `%(1..3)` rounded to 3 decimals, and `%(6)/%(7)` constant at the zero position;
  - probe pretravel, latency and jitter;
  - probe on any input pin (`probe_pin`, `other_inputs`);
  - finger presses during the finger test and input detection;
  - stylus eccentricity flipped by `ui.on_spin`;
  - PAUSE < 1 s raising an error;
  - MSGBOX/INPUT/SK via a scripted `UI`;
  - SHELL and FP calls recorded, not run.

  When SB3 surprises you, **update the VM to match the real behaviour first**, then fix the code. Several bugs went unnoticed because the VM had the same wrong assumption as the code (`%(6)`/`%(7)`, sub-second PAUSE).
- **`tests/sim_sbp.py`** samples toolpaths independently of the rewriter, to prove the aligned file equals the original under the fitted transform (deviation ~2e-6 in on Nikolai's real 28k-line Fusion export).
- **Static checks** cover: undocumented THEN actions, variables used before assignment, PAUSE values, quoted strings with operators (`risky_string_lines`), installer variables assigned exactly once, OPEN line numbers in the self-test, and copyright notices.

## 10. Fusion 360 conventions (Nikolai's setups)

- **Setup 2's WCS can sit at any corner.** Hole coordinates and the stock box must be in Setup 2's coordinates, the ones the side-B file uses.
- **His flip:** end over end about an X-parallel axis, so Y and Z invert. The flip axis is Y = H/2, and the holes go on it.
  - The B-Side-Test stock is 15 × 5.6 in, with holes at (4.2, 2.8, Ø0.6) and (11.22, 2.8, Ø0.8).
  - An earlier export had its origin at back-left, which made the stock box `0, -H, W, 0`.
- **Fusion's ShopBot post omits the stock size,** hence the stock box prompt. It emits `ON INPUT … MSGBOX` and `JH`, and Z zero at the spoilboard.

## 11. Status (as of 2026-10-04)

**Confirmed on the machine**
- All of §7.
- SHELL → `python.exe` works, in Preview too.
- A real probing run touches all walls correctly.
- After the v1.8 fix (`%(6)`/`%(7)`), alignment from real probe data proceeded.

**Not yet reported or measured**
- A Stage 4 registration measurement: the step between side-A and side-B features.
- The probe's real ball size; the first run used a wrong value, which affects only diameters, not centres.

**New in v2.0 and not yet run on the machine**
- the C80 custom cut and its folder `C:\SbParts\Custom`;
- `FP` of `vd_run.sbp` from inside a custom cut;
- `pythonw.exe` launch;
- the tkinter window on Windows;
- input auto-detection and the calibration run.

The README's Stage 0 (dry run in Preview) is the planned first check.

**Known mechanical caveat:** the old machine stalled once on a fast finishing pass (X/Y slip). No software check can detect a slip during cutting.

**Version history in brief**
- v1.x: probe routine, alignment, Fusion support, batch alignment.
- SHELL saga: `.bat` → `cmd.exe` → plain words → `python.exe` direct.
- PAUSE fix.
- `%(6)` fix.
- Copyright added.
- v2.0: window, prepare/align/calibration, custom cut, pre-checks.

**Roadmap / ideas discussed, not built**
- Keep the core controller-agnostic, with a "machine adapter" boundary covering measurement, applying the correction, and runtime checks. For controllers with coordinate rotation (G68, LinuxCNC `G10 L2 R`) output the transform instead of rewriting files; G38.2 probing.
- Pre-position the probe over hole 2 from the predicted position.
- A picture of board, holes and aligned extents before cutting.
- A probe profile with drift alerts.
- A desktop shortcut.
- Possibly a source-available licence later.

## 12. Working with Nikolai

**Who he is:** a research fellow at ITP and an experienced fabricator and engineer (CNC, boatbuilding, embedded systems, Rhino/Grasshopper). He prefers hands-on work.

**How he wants answers**
- Terse, logically structured, technical, readable at a high-school level.
- Challenge his proposals as hypotheses.
- Surface hidden assumptions, trade-offs and failure modes early.
- State uncertainty explicitly. Always offer at least one alternative framing.
- Cite sources in place, with links.
- When depth and clarity compete, offer the option of more detail.

**How to change code**
- Confirm direction before large changes he didn't ask for. He has interrupted work that went beyond the request.
- Prefer small, verified changes.
- Never claim something works on the machine unless he has confirmed it. Say clearly what was only tested in the simulator.

**Machine-side testing**
- Give him short, numbered steps.
- Say what he should see, and which files to send back: `vd_launch.log`, `vd_probe_data.csv`, the reports, the self-test results.

**Conventions**
- Version and date go in each file's header and in `VERSION`.
- The README is the operator manual; update it with every behaviour change.
- Packaging: zip the `VirtualDowel` folder, without the generated files.

---

## What CLAUDE.md should contain (outline)

1. **One-paragraph purpose**, plus the ownership notice.
2. **Commands:** run tests, run the window locally, CLI examples, how to package.
3. **Architecture map:** files, key functions, data flow prepare → probe → align → cut, generated files.
4. **Hard SB3 constraints (§7)** as a compact checklist, with the authoring rules. This is the most important section.
5. **Safety invariants (§8).**
6. **Testing:** what the VM models and the rule "fix the VM to match the machine first"; `sim_sbp` verification for rewriter changes.
7. **Data formats**, short.
8. **Status:** confirmed versus unconfirmed on the machine, and the next planned checks.
9. **Working agreements** with Nikolai (§12).
