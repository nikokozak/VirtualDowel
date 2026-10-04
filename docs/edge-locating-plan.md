# Edge locating mode - plan (draft for review)

Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

Status: proposal, 2026-10-04. No code is written yet. Nikolai must agree to the direction before work starts.

## 1. Problem

The hole mode needs a through-hole. To cut it, the machine must lift a cutter as long as the stock is thick above the stock, plus the collet. That is about 2 to 2.5 times the stock thickness in Z travel. For thick stock the machine cannot do this.

Goal: find the stock on side A and on side B from its edges only. The registration between side-A and side-B features must stay as accurate as possible, and the procedure must stay easy.

Assumptions (from Nikolai): both faces are faced; the edges are machined smooth, straight and as square as possible; some stock is a slight rhomboid.

## 2. Recommendation

Build the mode as Nikolai proposed, with four additions.

**Nikolai's proposal (keep):** at each of the four corners, probe the two edges near the corner. Each edge then has two points, one near each end. Make a line through the two points of each edge. The corners are where adjacent lines meet. The operator picks one corner as the job zero and one edge at it as the X direction. Do this on side A and on side B. On side B, use the same physical corner and the same physical edge.

**Additions:**

1. **Probe both sides at the same physical height in the edge. If possible, probe at mid-thickness.** Edge tilt (an edge that is not square to the faces) is the largest error. When both sides touch the edge at the same height, tilt has no effect. (Section 4.)
2. **Touch the top surface at each corner first**, and set the probing depth from that surface. This makes the probing height correct on both sides, whatever the park height.
3. **Cancel stylus runout** with the existing spin-180 option (or keep runout under 0.0005 in). Runout does not cancel between the sides.
4. **Record the side-A measurement and compare it on side B** (edge lengths, corner angles, top height). This finds a moved board, a wrong corner, and bad touches.

**Reuse:** the result of the measurement is a rotation and a translation, the same as the hole mode. So the OpenSBP rewriter, the runtime checks, the output files, the stale-output protection and the window flow all stay. Only the measurement and the fit are new.

## 3. Why the same physical corner and the same edge

The job frame on each side is: origin at corner C, X along edge E. If both sides use the same physical C and E, then:

- The real stock size does not matter. The Fusion stock box can differ from the real stock.
- A rhomboid does not matter. Only C and the direction of E are used.
- Errors that push every touch the same distance toward the stock cancel between the sides: a wrong ball size, probe pretravel, stop latency, and backlash. The reason: the probe always approaches the edge from outside the stock, on both sides, so the error is in the same direction in the stock's own frame.

If side B uses a different corner, the real stock size enters the result (real size minus box size). Thus the window must default to the same physical corner on side B and must warn if the operator picks another one.

## 4. Error budget (checked with a simulation)

Simulation: stock 30 x 20 x 3 in, side A turned 1.3 deg, side B turned -2.1 deg after an end-over-end flip. Worst offset between matching side-A and side-B points (script: `docs/edge_sim.py`; it becomes a unit test in phase 1).

| Case | A/B offset |
|---|---|
| Perfect probe, square stock | 0 |
| Rhomboid, 1.5 deg out of square | 0 |
| Ball diameter setting wrong by 0.010 in | 0 |
| Pretravel/backlash/latency 0.004 in, same in all directions | 0 |
| Stylus runout 0.002 in, no spin-180 | 0.003-0.004 in |
| All edges tilted 0.5 deg, probed 0.5 in deep on both sides | 0.025 in |
| All edges tilted 0.5 deg, probed at mid-thickness on both sides | 0 |
| All edges tilted 0.1 deg, probed 0.5 in deep on both sides | 0.005 in |

Tilt error = tilt x (height difference between the side-A and side-B touches). The height difference is T - depth_A - depth_B. Each 0.1 deg of tilt per inch of height difference gives 0.0017 in.

Errors that remain after the additions in section 2:

- Probe noise. The rotation error is about 1.4 x repeatability / edge length, the same as for two holes the same distance apart. The corner position is near the touch repeatability.
- Edge straightness at the touch points. The touches are at a fixed distance from the corner on both sides, so a bowed edge gives the same chord on both sides. A local bump or chip does not cancel.
- Probe pretravel that changes with direction (lobing). Small, usually under 0.0005 in.
- Tilt, only when mid-thickness is out of reach of the stylus.

Expected result (estimate, not measured): 0.002-0.004 in with good edges and mid-thickness probing; worse with tilted edges when mid-thickness is out of reach. The hole mode is better (about 0.001-0.002 in) because each hole centre cancels the one-sided errors by itself.

## 5. Risks and failure modes

| Risk | Effect | Control |
|---|---|---|
| Wrong flip direction (end over end instead of side over side) | A rectangle or parallelogram outline looks the same both ways. The geometry cannot see the mistake. Job turned 180 deg. | Mark the reference corner on its edge face on side A (the edge face is visible from side B). Side B asks the operator to confirm where the mark is. Phase 3: compare the four corner angles with side A. This detects the wrong flip only when the stock is measurably out of square. |
| Stylus too short for mid-thickness | Tilt error stays | Setting "probe reach" (exposed stylus length minus margin). The window shows the tilt sensitivity in in/deg. Keep the edges square. |
| Chipped or rounded corners and arrises | Bad touches | Touch stations at `inset` (default 1 in) from the corner; depth at least 0.25 in below the top. |
| Clamps near the corners or above the top surface | Collision, or no free edge | All low moves are guarded (the probe input stops the move and the routine lifts). Operator rule: keep 2 in each way from every corner free. |
| Operator parks over a clamp or not over the corner | Top touch fails or finds the clamp | Top touch must find a surface within a short search. Top heights at the four corners must agree. |
| Probe does not trigger when pushed upward | Top touch and guarded plunge do not work | The hole mode's guarded plunge already assumes this. Not confirmed on the machine. Machine test E1 checks it. |
| Board moves during probing | Wrong corners | Side lengths and angles compared with side A; four top heights compared. |
| Corner angle far from 90 deg | Part may not fit on the stock | Warning above a limit (for example 0.5 deg); FAIL above a larger limit (for example 3 deg); both in `vd_settings.json`. |
| Wrong corner picked for the Fusion WCS | Job placed off the stock | Check that the job's toolpaths fall inside the measured outline (the same coverage check as the Fusion stock box). |

## 6. Operator procedure

Side A:

1. Clamp the stock. Mark the reference corner (for example "C") on its edge face.
2. Type `C80`. Choose *Locate with: stock edges*, *Side A*. Add the side-A job files. Click *Start probing*.
3. For each corner in turn (front-left, front-right, back-right, back-left): jog the probe ball over the corner, 0.1-0.25 in above the top, and press ESC. The routine does the rest.
4. The window shows the measured outline, its corner angles and edge lengths. Click the corner where the Fusion Setup 1 WCS is, and the edge that is its X axis. Click *Align*.
5. Cut side A with the `_VD.sbp` files (zero Z only).

Side B:

1. Flip the board as planned and clamp it.
2. `C80`, *stock edges*, *Side B*, side-B files, *Start probing*. Probe the four corners as on side A.
3. The window selects the physical corner and edge used on side A (from the flip type). It shows the comparison with side A. Confirm that the mark is at the corner the window shows. *Align*. Cut.

Motion at one corner (front-left shown; the other corners are mirror images):

```
  y
  ^          |  stock
  |       F  |
  |          |<- F station: touch toward +X, `inset` from C
  |          |
  |          C------------------------ E
  |         .        ^
  |        .         E station: touch toward +Y, `inset` from C
  |       o  outside point: `clear` from C on both axes; probe lowered here
  +-------------------------------------------> x
```

1. Top touch: move `inset` toward the inside of the corner at park height, guarded move down, record the top Z, lift back to park height.
2. Move to the outside point at park height. Guarded plunge to top Z - `depth`.
3. Guarded move to the E station. Touch toward the stock: one coarse touch, then N fine passes (fast, back off, slow), as in the hole mode. Move back out.
4. Guarded move past the outside point to the F station. Touch. Move back out.
5. Optional spin-180: lift, the operator turns the spindle, touch both stations again.
6. Lift to park height.

Defaults (to confirm): `clear` 0.4 in, `inset` 1.0 in, `depth` = half the thickness when the stylus reaches it, otherwise the probe reach. 4 corners x 2 stations x (1 + N) touches, plus 4 top touches. About 4-6 minutes per side.

## 7. Fusion 360 rule

- Setup 1: WCS origin at the stock-box corner that is the physical reference corner C. X along the reference edge E.
- Setup 2: WCS origin at the same physical corner. After an end-over-end flip, a front-left C is at the back-left: stock box `0, -H, W, 0`, Y and Z inverted. This is Nikolai's earlier back-left habit.
- Stock box: the nominal stock size. No reference holes are needed.

## 8. Design decisions

1. **One routine.** Add an edge mode to `vd_probe.sbp` (`&vd_kind = 1` holes, `2` edges) instead of a new file. The edge mode reuses the proven code: setup, finger test, input detection, `vd_touch_rec`, `vd_touch`, `vd_onhit`, `vd_lift`, the error exits and the end SHELL. The hole path does not change. GOSUB depth stays at 5. New subroutines: corner, top touch, station, guarded move.
2. **Data format.** A new header tag `VE` for edge data, so the hole format stays byte for byte the same. `T` records keep their format (the `hole` field is the corner number; axis and sign give the edge). A new `Z,corner,ztop` record holds the top touches.
3. **Python.** New: parse `VE`; `compute_edges` (stations, lines, corners, angles, repeatability); `edge_frame(corner, reference edge) -> Fit`; edge checks. `run_alignment` is split so that the transform/check/write part is shared by both modes. CLI flags for the edge mode.
4. **Side-A record.** Kept in `vd_state.json` (`edge_ref`): outline in the stock frame, corner and edge choice, depth, top height, date. `new_session` does not remove it.
5. **Window.** *Locate with* switch on the Prepare screen; edge settings (side, flip type, thickness, depth, probe reach; `clear` and `inset` under Advanced). The Align screen gets a tkinter Canvas with the measured outline, four clickable corners, the reference-edge choice, the toolpath extents, and the side-A comparison. Standard library only.
6. **Simulator.** `tests/sbp_vm.py` gets a stock-block model: a convex outline with a top surface and optional edge tilt. The edge routine runs end to end in it, with its error paths.

## 9. Phases

Each phase ends with all tests passing and a short report. Nothing is called "works on the machine" until Nikolai confirms it.

| Phase | Content | Check |
|---|---|---|
| 0 | Fixes from the v2.0 review (section 12; only the ones Nikolai approves) | existing tests plus a new test for each fix |
| 1 | Python geometry, `VE` parser, edge fit and checks, shared write path, CLI | unit tests with synthetic touches: rotated/shifted rhomboids recovered to 1e-6 in; the side-A -> flip -> side-B registration test from section 4 |
| 2 | Edge mode in `vd_probe.sbp`; SIM (dry run) mode; VM stock-block model | VM end-to-end runs: square and rhomboid stock, rotated board, mm machine, spin-180, park over the stock, clamp in the path, no contact; all static lint tests |
| 3 | Window: mode switch, edge settings, outline canvas, corner picker, side-A record and comparison, Fusion hints | window tests (with Tk) |
| 4 | README operator section, troubleshooting, machine test stages | Nikolai's machine tests below |
| 5 | Only if the machine data shows a need: automatic travel to the next corner; least-squares fit of the whole outline against the side-A record; two-depth tilt measurement when mid-thickness is out of reach | decided from the phase-4 data |

Machine tests (phase 4):

- **E0** Preview dry run of the edge mode (typed corner positions), window round trip.
- **E1** Probe checks: does the input change when the tip is pushed up? Measure the exposed stylus length.
- **E2** Repeatability: probe the same block three times without moving it. Corner scatter should be under 0.002 in.
- **E3** Edge mode against hole mode on a thin board: side A probes the edges and cuts two holes. On side B, align with both methods. The difference is the edge-mode error, with the hole mode as the reference. No cutting needed on side B.
- **E4** Thick flip test: a feature cut half from each side; measure the step.

## 10. Alternatives

- **Spoilboard dowel pins and short blind holes.** Side A drills shallow holes; side B drops onto pins at the mirrored positions. No Z-travel problem. Accuracy depends on the pin fit and on a fixture located on the machine. This is the method Virtual Dowel replaces, but it stays a valid fallback.
- **Fit the whole outline against the side-A record** (phase 5 option). Uses all eight stations, so it averages noise and cancels some tilt patterns. Harder to explain, and it depends on all four edges being good.
- **Three corners only.** The corner and both edges at it are still found from the two neighbour corners. Less probing, but no fourth corner to check the outline.

## 11. Questions for Nikolai

1. What is the exposed stylus length of the probe, and what is the thickest stock you plan? This decides if mid-thickness probing is possible.
2. How are the edges made (jointer and table saw, CNC from both faces, other)? This decides the likely tilt.
3. Can the clamps keep 2 in each way from every corner free, and below the top surface?
4. What registration do you need for the thick work (for example 0.005 in)?
5. Is your usual flip end over end, with the reference corner front-left on side A? (The plan assumes yes.)
6. Are four parks per side acceptable for the first version? Automatic travel between corners can come after the first machine tests.

## 12. Phase 0: findings from the v2.0 review

Three independent review passes read all the code (2026-10-04). Each item says how it was checked: **run** (reproduced with code), **traced** (code paths read end to end), or **hypothesis** (needs the machine). The fit maths, the hole matching, the symmetry and mirror checks, and the unit scaling had no defect.

**Fix first (wrong result or safety rule broken):**

1. **Relative moves after `JH`/`MH` are cut in the wrong place.** *Run.* `SA / J2,5,5 / JH / SR / M2,1,0` is accepted: the relative moves are rotated but not shifted. After `JH` the tool is at machine 0,0, not at the design origin, so the path is offset by the translation. A `CG` with a blank end after `JH` is the same. Files that start in `SR` with no absolute move are accepted too. Fix: reject a relative move or a blank-end `CG` until an absolute move with X and Y has set the position (`vd_align.py:948-954`, `1019-1027`). Nikolai's VCarve and Fusion posts write absolute moves, so his real files are probably not affected.
2. **An old probing file can run again.** *Traced.* Nothing resets `vd_run.sbp` after a run (the `write_run_stub` docstring says something does). If SB3 cannot start Python at the next C80, the previous `vd_run.sbp` runs, and its `newsession` call fails in the same way. Fix: write the stub when the window opens after probing (`align`, `calresult`), and let the start file write an `END` stub with OPEN/WRITE before its SHELL line.
3. **Files that the script did not write can be deleted.** *Traced.* `run_alignment` deletes `<job>_VD.sbp`, `<job>_VDSIM.sbp` and the `--out` target if they exist, before any check (`vd_align.py:1397-1400`). It does not look in `vd_state.json`. Fix: delete or overwrite only recorded outputs, or files whose first line is the Virtual Dowel marker.
4. **A job name with a character outside Latin-1 crashes Start after old outputs and probe data are deleted.** *Run* (en dash). Fix: make the run-file text (and encode it) before the first destructive step (`vd_align.py:2079-2092`).
5. **A failed delete of an old output is ignored.** *Traced.* A locked `_VD.sbp` stays on disk and probing starts anyway (`_remove_outputs`, `prepare_probing`). The state file is not written atomically. Fix: stop Start and show the file; write the state with a temporary file and `os.replace`.

**Fix or confirm on the machine:**

6. **`IF … THEN &var = <expression>`** is in 9 lines of `vd_probe.sbp` (302, 358, 359, 379, 380, 410, 417, 418, 420). The documented rule allows only a single value. The real probing run probably ran these lines, so it probably works. *Traced.* Fix: add one such line to the self-test, or change the lines to branch-free arithmetic, and make the lint match the decision.
7. **Self-test gaps.** Add: an `IF` with a negative number (`IF %(6) < -0.002`, which every aligned file uses); a THEN with an expression; the probe input when the tip is pushed up.
8. **Dry-run Start deletes the real probe data** and replaces the real session (`vd_align.py:2084-2088`). *Traced.*
9. **Back-off against stop distance.** The VM stops after 12 ms. With a slower real stop, the 0.03 in back-off can leave the tip deflected, and the run aborts (safely). *Hypothesis.* Add a VM test with 50-100 ms latency; check `backoff` against `fast`.
10. **Reinstall can break a working install.** `venv.create(..., clear=True)` deletes the venv each time. If the window is open, the delete fails half way. The fallback Python path (`pythoncore-3.14-64`) has a `-`, so it is refused. *Traced.* Fix: keep a venv that runs.
11. **`ENDALL` in the runtime checks can leave the spindle on** when a check fails after a custom cut that started it. Fix: `C7` before each `ENDALL`.
12. **Short hole spacing is not compared with the job size.** Two holes 2 in apart with 0.003 in noise give about 0.06 in at 30 in. Fix: warn when the spacing is less than about half the toolpath diagonal.

**Documents and dead code:**

- README: "All maths now uses ... %(6)/%(7)" is the opposite of the current code; "80 automated tests" (now 110); `&vd_spin180 = 1` and `&vd_test = 0` instructions are window settings now; "C80" is fixed in texts, but the installer can choose C81-C89.
- Dead: `&vd_launched`; `ProbeData.cfg`, `pin`, `trig`; `BASIC_FIELDS`; `Result.shift`; `AMBER`. Stale comment on `offset_consistency` in `DEFAULTS`. The `vd_err_cfg` message is wrong for a calibration.
- Window `Check` uses an inch tip with mm holes in `hole_errors` (`vd_app.py:506`); Start is correct. The calibration record has no unit.

**Where the brief and the code differ:**

- There is no `VERSION` file. The version is the `VERSION` constant in `vd_align.py` and the file headers.
- The brief's THEN rule is stricter than the code (item 6).
- The brief says error paths lift "before anything else". The code shows the error message first, then lifts. The probe does not move while the message is open, so this is safe, but the words differ.
