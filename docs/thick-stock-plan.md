# Locating thick stock without through-holes - plan (draft for review)

Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

Status: proposal, revision 2, 2026-10-04. No code for this is written yet. Nikolai must choose the direction (section 2) before work starts. Revision 2 adds Nikolai's constraints: the probe cannot reach the top of thick stock, Z is set by the operator, and the edges are machined on the CNC.

## 1. Problem and constraints

The hole mode needs a through-hole. To cut it, the cutter (as long as the stock is thick) must start above the stock. That needs about 2 to 2.5 times the stock thickness T in Z travel. Thick stock does not fit.

Constraints from Nikolai:

- The probe is too long to go above the top of thick stock. The probe ball is always below the top surface. So the probe can never cross over the stock; it travels around the outside.
- A Z touch-off with the probe is possible but not exact (vertical pretravel). Z can be left to the operator.
- Both faces are faced. The edges are machined on the same CNC, two opposite edges per setup, clamped on the other two. With a long enough bit each edge is cut in one pass. Otherwise each edge is cut in two halves (one from each face), and the halves can be slightly offset.
- Some stock is a slight rhomboid (expected, because the two edge pairs are cut in two setups).

## 2. Two ways to do it - choose one to build first

**A. Edge mode (Nikolai's proposal).** Probe two edges at each of the four corners, make a line through each edge, intersect the lines to get the corners, pick one corner as the job zero and one edge as the X direction. Do it on both sides with the same physical corner and edge. Uses no waste; the stock edges are the reference. Sections 3-9.

**B. Edge-slot mode (alternative).** Side A cuts two short U-shaped slots, open to the outside, at the two ends of the flip axis, with different widths. These are "half-holes" at the edges. The cutter and the probe both come in from outside the stock, so neither has to be above it. On side B the routine probes the slots as it probes holes now. Section 10.

| | A. Edges | B. Edge slots |
|---|---|---|
| Expected A/B registration | 0.002-0.004 in with good edges (estimate) | about the same as the hole mode, 0.001-0.002 in (estimate) |
| Probing on side A | 4 corners | none (side A only cuts the slots) |
| Probing on side B | 4 corners | 2 slots |
| Wrong flip direction detected | no (operator mark only) | yes, by the slot widths, as with holes |
| Depends on edge quality, edge tilt, two-half edges | yes | no |
| Needs waste at both ends of the flip axis | no | yes |
| New code | new measurement, fit, window picker, simulator model | new touch pattern only; the rest of the hole mode is reused |

**Recommendation:** build B first for parts that have waste at both ends of the flip axis, because it is more accurate, less work at the machine, and reuses the proven hole code. Build A for parts that use the whole stock. If most thick parts fill the stock, build A first.

## 3. Edge mode: the method

At each corner, probe the two edges near the corner. Each edge then has two points, one near each end. A line goes through the two points of each edge, and the corners are where adjacent lines meet. The operator picks the corner where the Fusion WCS is and the edge that is its X axis. On side B, the window selects the same physical corner and edge from the flip type.

Why the same physical corner and edge: the job frame is origin C, X along E on both sides. Then:

- The real stock size does not matter; the Fusion stock box can differ from it.
- A rhomboid does not matter; only C and the direction of E are used.
- Errors that push every touch the same distance toward the stock cancel between the sides: wrong ball size, pretravel, stop latency, backlash. The probe always comes from outside the stock, so these errors point the same way in the stock's own frame.

If side B uses a different corner, the real stock size enters the result. So the window defaults to the same physical corner and warns if the operator picks another one.

The result is a rotation and a translation, the same as the hole mode. The OpenSBP rewriter, the runtime checks, the outputs and the stale-output protection stay as they are.

## 4. Edge mode: errors (checked with a simulation)

Simulation: stock 30 x 20 x 3 in, side A turned 1.3 deg, side B turned -2.1 deg after an end-over-end flip. Worst offset between matching side-A and side-B points. Script: `docs/edge_sim.py` (it becomes a unit test in phase 1).

| Case | A/B offset |
|---|---|
| Perfect probe, square stock | 0 |
| Rhomboid, 1.5 deg out of square | 0 |
| Ball diameter setting wrong by 0.010 in | 0 |
| Pretravel/backlash/latency 0.004 in, same in all directions | 0 |
| Stylus runout 0.002 in, no spin-180 | 0.003-0.004 in |
| All edges tilted 0.5 deg, probed 0.5 in deep on both sides | 0.025 in |
| All edges tilted 0.5 deg, probed at the same physical height on both sides | 0 |
| All edges tilted 0.1 deg, probed 0.5 in deep on both sides | 0.005 in |

**Tilt and the probing height.** Tilt error = tilt x (height difference between the side-A and side-B touches in the edge). When both sides touch the edge at the same physical height, tilt has no effect. With edges cut on the CNC, tilt is the spindle tram error, the same for all edges of one setup.

**Z accuracy is not critical.** A height error h moves the result by h x tan(tilt): 0.0002 in for h = 0.1 in and a tilt of 0.1 deg. So the probe's vertical pretravel does not matter, a Z touch-off with the probe is good enough, and so is an operator's eye with a rule. No top touch is needed.

**Which heights the probe can reach.** Beside the stock, the ball can only work in a band of heights above the spoilboard:

- top of the band: the ball height with Z at the top of its travel (below the stock top for thick stock);
- bottom of the band: the stock top minus the free stylus length (lower than that, the probe body hits the top edge of the stock).

Side A touches at height h (above face B). Side B touches the same physical height at T - h above the spoilboard. Both must be in the band:

- **One-pass edges:** use h = T/2 on both sides (the same Z number when Z zero is at the spoilboard). The band must include T/2.
- **Two-half edges:** probe the same half on both sides, away from the step: h = T/2 - s on side A and T/2 + s on side B (s = step clearance, for example 0.25 in). The band must include T/2 - s to T/2 + s. If it does not, the same-half rule cannot be met: the step offset and the tilt then go straight into the result.

The window computes the two heights from T, s and the band, and refuses a height outside the band.

**Other errors that remain:**

- Probe noise. Rotation error about 1.4 x repeatability / edge length, the same as two holes that distance apart.
- Stylus runout: use spin-180 at each corner or keep runout under 0.0005 in.
- Local edge defects at the touch points. Touches are at the same distance from the corner on both sides, so a bowed edge gives the same chord both times.
- Direction-dependent pretravel (lobing): usually under 0.0005 in.

**Nikolai's edge preparation.** Opposite edges cut in one setup are parallel. The angle between the two pairs depends on the re-clamping, so the stock is a parallelogram. Two results follow:

- The parallelism of opposite edges is a good check of the measurement (a bad touch shows as a pair that is not parallel).
- A parallelogram looks the same after either flip, so the geometry cannot detect a wrong flip. The corner mark is the only control.

## 5. Edge mode: risks

| Risk | Effect | Control |
|---|---|---|
| Wrong flip direction | Job turned 180 deg; geometry cannot see it | Mark the reference corner on its edge face on side A (visible from side B). Side B asks the operator to confirm the mark is at the corner the window shows. |
| Probe band too narrow for the needed heights | Tilt or step error stays | Machine settings: band top and free stylus length. The window refuses heights outside the band and shows the expected error. |
| Clamps at the corners | Collision, or no free edge | All moves at probing height are guarded (the probe input stops the move). Keep 2 in each way from every corner free. |
| Chipped corners | Bad touches | Touch stations `inset` (default 1 in) from the corner. |
| Operator parks over a clamp or too close | Guarded move or touch fails | The routine stops, lifts the probe clear (outward), and asks for a new park. |
| Board moves during probing | Wrong corners | Opposite-edge parallelism; comparison with the side-A record (edge lengths, angles). |
| Corner far from 90 deg | Part may not fit on the stock | Warn above a limit (for example 0.5 deg), FAIL above a larger one (for example 3 deg); both in `vd_settings.json`. |
| Wrong corner picked for the Fusion WCS | Job placed off the stock | The job's toolpaths must fall inside the measured outline (same idea as the stock-box coverage check). |

## 6. Edge mode: operator procedure

Side A:

1. Clamp the stock. Mark the reference corner (for example "C") on its edge face.
2. Type `C80`. Choose *Locate with: stock edges*, *Side A*. Add the side-A files. Click *Start probing*. The window shows the probing height for this side.
3. First corner (front-left): jog the ball outside the corner, about 0.3 in from both edges, at the probing height. Press ESC. The routine records this Z.
4. Jog around the outside of the stock to each next corner (front-right, back-right, back-left) and press ESC. The routine moves to the recorded Z (guarded) before it touches.
5. The window shows the outline, corner angles, edge lengths and parallelism. Click the corner of the Fusion Setup 1 WCS and its X edge. *Align*. Cut side A with the `_VD.sbp` files (zero Z only).

Side B: flip, clamp, `C80`, *stock edges*, *Side B*, probe the four corners as on side A (at the side-B height). The window selects the physical corner and edge of side A and shows the comparison. Confirm the corner mark. *Align*. Cut.

Motion at one corner (front-left shown; the other corners are mirror images). Everything happens at the probing height, outside the stock:

```
  y
  ^          |  stock
  |       F  |
  |          |<- F station: touch toward +X, `inset` from the corner
  |          |
  |          C------------------------ E
  |         .        ^
  |        .         E station: touch toward +Y, `inset` from the corner
  |       o  park point: the operator leaves the ball here
  +-------------------------------------------> x
```

1. Guarded move along E (outside it) to the E station. Touch toward the stock: one coarse touch, then N fine passes (fast, back off, slow), as in the hole mode. Move back out.
2. Guarded move back past the park point and along F to the F station. Touch. Move back out.
3. Optional spin-180: the operator turns the spindle; touch both stations again.
4. Return to the park point.

Defaults (to confirm): park about 0.3 in from both edges, `inset` 1.0 in, search distance 0.8 in. 4 corners x 2 stations x (1 + N) touches. About 4-6 minutes per side.

## 7. Fusion 360 rule (edge mode)

- Setup 1: WCS origin at the stock-box corner that is the physical reference corner C; X along the reference edge E.
- Setup 2: WCS origin at the same physical corner. After an end-over-end flip, a front-left C is at the back-left: stock box `0, -H, W, 0`, Y and Z inverted (Nikolai's earlier back-left habit).
- Stock box: the nominal size.

## 8. Edge mode: design decisions

1. **One routine.** Add the mode to `vd_probe.sbp` (`&vd_kind`) instead of a new file. It reuses the proven setup, finger test, input detection, `vd_touch_rec`, `vd_touch`, `vd_onhit`, error exits and the end SHELL. The hole path does not change. GOSUB depth stays at 5. New subroutines: corner, station, guarded move.
2. **Data format.** A new header tag for edge data, so the hole format stays the same byte for byte. `T` records keep their format (the `hole` field is the corner number; axis and sign give the edge; `z` gives the probing height).
3. **Python.** Parse the new header; `compute_edges` (stations, lines, corners, angles, parallelism, repeatability); `edge_frame(corner, edge) -> Fit`; edge checks. `run_alignment` is split so that the transform/check/write part is shared by all modes. CLI flags.
4. **Side-A record.** In `vd_state.json` (`edge_ref`): outline in the stock frame, corner and edge, probing height, date. `new_session` keeps it.
5. **Window.** *Locate with* switch; edge settings (side, flip type, thickness, step clearance; band limits as machine settings). The Align screen gets a tkinter Canvas: measured outline, four clickable corners, the X-edge choice, toolpath extents, side-A comparison. Standard library only.
6. **Simulator.** `tests/sbp_vm.py` gets a stock-block model (convex outline, optional edge tilt and two-half step). The routine runs end to end in it, with its error paths.

## 9. Phases

Each phase ends with all tests passing and a short report. Nothing is called "works on the machine" until Nikolai confirms it.

| Phase | Content | Check |
|---|---|---|
| 0 | Fixes from the v2.0 review (section 12). Items 1-4 approved 2026-10-04. | existing tests plus a new test for each fix |
| 1 | Python: parser, fit, checks, shared write path, CLI (for the chosen mode) | unit tests with synthetic touches; the side-A -> flip -> side-B registration test |
| 2 | Routine in `vd_probe.sbp`; SIM (dry run); VM model | VM end-to-end runs: square and rhomboid stock, rotated board, mm machine, spin-180, clamp in the path, no contact; static lint tests |
| 3 | Window | window tests (with Tk) |
| 4 | README operator section, troubleshooting, machine test stages | machine tests below |
| 5 | Only if the data shows a need: automatic travel between corners; whole-outline fit against the side-A record | decided from the phase-4 data |

Machine tests (phase 4):

- **M0** Preview dry run (typed positions) and window round trip.
- **M1** Measure the probe band: ball height above the spoilboard with Z at the top of its travel; free stylus length.
- **M2** Repeatability: probe the same block (or slots) three times without moving it. Scatter should be under 0.002 in.
- **M3** New mode against the hole mode on a thin board: side A cuts two holes (and slots, for mode B); on side B, align with both. The difference is the error of the new mode, with the hole mode as the reference. No side-B cut needed.
- **M4** Thick flip test: a feature cut half from each side; measure the step.

## 10. Edge-slot mode (alternative B)

**Idea.** A reference hole whose centre is at the stock edge becomes a slot that opens to the outside. A cutter can cut it from outside the stock, the same way Nikolai plans to machine the edges, so it does not have to be above the stock. The probe can also enter it from outside, at its working height.

**Layout** (same rules as the holes):

- Two U-shaped slots, cut with an end mill moving inward from outside, on the flip axis, at opposite ends. For an end-over-end flip (axis Y = H/2): one in the left edge, one in the right edge. On the flip axis, the side-A and side-B coordinates are the same.
- Different widths (for example 0.5 and 0.625 in), so a wrong flip fails, as with holes.
- Length into the stock about 1 in. Reference point: the centre of the round end (the tool's end position in CAM).
- Depth from face A: enough to reach the probe band on side B. Side A cuts from face A, so on side B the slot is at the bottom, from the spoilboard up to the slot depth. Depth at least T - free stylus length + about 0.3 in. It does not need to go through.

**Probing on side B.** The operator parks the ball outside each slot opening at the probing height. The routine moves in along the slot to the round end, then:

- touches both side walls: a two-sided pair, so the across-slot coordinate cancels ball size, pretravel and backlash exactly, as a hole does;
- touches the slot end: one-sided. Half the measured width bias corrects it for the ball size and pretravel. What remains (for example backlash) has the opposite sign at the two opposite slots, so it cancels in the translation and shows only as a spacing error, which the existing spacing check already reports.

The rotation comes from the across-slot coordinates of the two slots (two-sided, exact) over their full spacing. So the accuracy is about the same as the hole mode.

**Reuse.** The design entry (X, Y, width), size matching, symmetric-layout check, side-A/side-B mirror check, rigid fit, spacing check, rewriter and runtime checks are the hole-mode code. New: the slot's opening direction (taken from the nearest stock-box edge), the touch pattern (one wall pair plus the end, entered from outside) and the centre calculation.

**Needs and risks:**

- Waste at both ends of the flip axis.
- CAM: the cutter enters and leaves from outside the stock, with retract heights that never go over the stock. Nikolai's edge passes need the same.
- Wall tilt from the tram, as in the hole mode (the hole mode probes near the exit, so it has the same effect).
- The probe body must stay above the stock top when the ball is in the slot (the same band limit as in section 4).

## 11. Questions for Nikolai

1. **A or B first?** Do most thick parts have waste at both ends of the flip axis?
2. **Probe band:** with Z at the top of its travel, how high is the ball above the spoilboard? How long is the free stylus (from the ball to where the probe body gets wider)? And the thickest stock you plan?
3. **Edges in one pass or two halves?** If two halves, the band must reach T/2 + s on side B.
4. Can the clamps keep 2 in each way from every corner free (mode A) or the slot openings free (mode B)?
5. What registration do you need for the thick work (for example 0.005 in)?
6. Is your usual flip end over end, with the reference corner front-left on side A? (The plan assumes yes.)

## 12. Phase 0: findings from the v2.0 review

Three independent review passes read all the code (2026-10-04). Each item says how it was checked: **run** (reproduced with code), **traced** (code paths read end to end), or **hypothesis** (needs the machine). The fit maths, the hole matching, the symmetry and mirror checks, and the unit scaling had no defect.

**Fix first (wrong result or safety rule broken). Items 1-4 approved by Nikolai on 2026-10-04:**

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
