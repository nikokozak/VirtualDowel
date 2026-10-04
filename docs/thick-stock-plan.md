# Locating thick stock without through-holes - plan (draft for review)

Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

Status: revision 6, 2026-10-04. Phases 1 and 2 are built and tested in the simulator; nothing is confirmed on the machine.

History: revision 2 added the probe and Z constraints and CNC-machined edges. Revision 3 corrected the slot depth (a part-depth slot has a ceiling on side B; Nikolai found the error). Revision 4 follows Nikolai's direction: assume no full-length bit, focus on the half-slot technique, keep the through slots and the edge mode as further options. Revision 5: the bays are made in Fusion; how the bay file is placed before anything can be probed (rough fit); probe sizes are settings. The key notch is optional (only for stock that does not match the stock box). Revision 6: rules found while building the routine in the simulator (bay size, slot width and length, park height), the routine's safety steps, and Nikolai's scope rule: Virtual Dowel only probes, checks and aligns; it never makes toolpaths.

## 1. Problem and constraints

The hole mode needs a through-hole, and the cutter for it must start above the stock. That needs about 2 to 2.5 times the stock thickness T in Z travel. Thick stock does not fit.

Constraints:

- **No full-length bit.** Each side can only cut a little past mid-thickness, as in any flip-milling job. (A full-length bit with too little Z travel to go over the stock is the second case; section 9.)
- **The probe cannot go above the top of thick stock.** It always travels around the outside of the stock. It can only get into a feature from the side.
- Z is set by the operator. A Z touch-off with the probe is possible but not exact.
- Stock can be large and irregular, so the method must not need machined edges.

**Scope (Nikolai, 2026-10-04): Virtual Dowel only probes, checks, and aligns files that Nikolai makes in Fusion. It never makes toolpaths or cut files.** Rules for the cuts become checks on his files and pages in the guide (section 11).

## 2. Modes and build order

1. **Half-slot mode (build first).** Side A cuts two U-shaped slots in the edges, from face A to past mid-thickness. On side B, Virtual Dowel writes a short cut that opens a large bay above each slot, from face B. Then the probe goes in from the side and probes the slots like holes. Sections 3-8.
2. **Through-slot mode (comes almost free).** The same slots cut through the full thickness from outside with a full-length bit. No bay cut. Section 9.
3. **Edge mode (later).** No features in the waste; uses the four machined edges. Needs every edge machined, so it does not suit irregular stock. Section 10.

## 3. Half-slot mode: the method

**Side A is placed as in the hole mode:** zero X/Y at the corner of the Fusion Setup 1 WCS and run it; side A is never probed. Its zero does not affect the A/B registration, but it matters twice: the rough fit assumes each slot opening is at the stock-box edge, so zero within about 0.1 in along the flip axis (or cut the key notch); and the round end must end up at least 0.75 in inside the real edge, so make the slot longer by the expected zero error.

**Side A** cuts two slots, like the two holes of the hole mode:

- On the flip axis, at opposite ends (for an end-over-end flip: one in the left edge, one in the right edge). On the flip axis, the side-A and side-B coordinates are the same.
- Different widths (for example 0.5 and 0.625 in), so a wrong flip fails, as with holes.
- Each slot is cut with an end mill that moves inward from outside the stock, to a round end. The round end must be at least 0.75 in inside the real stock edge. The reference point is the centre of the round end.
- Depth from face A: as deep as the side-A tool allows, past mid-thickness (D_A).

Optional, only for stock that does not match the stock box at the slots (section 5): side A also cuts one small **key notch** on an edge at right angles to the flip axis. It needs no bay and is never probed; it is only a jog target.

**Side B** (after the flip):

1. **Rough locate (cutter in).** The operator jogs the bit tip over the two slot openings (and the key notch, if cut), which show on the edge faces. Virtual Dowel makes a rough fit from them (section 5).
2. **Bay cut (cutter in).** Nikolai's bay file from Fusion (section 7) is moved with the rough fit, the same way as a part file, and written as `<file>_VDROUGH.sbp`. The operator previews it, zeroes Z, and runs it.
3. **Probe (probe in).** The operator parks the probe ball outside each slot opening, in line with the slot, at mid slot height below the bay floor. Then the routine (all moves guarded):
   1. moves 0.6 in into the opening; nothing may be touched (else: stops, the slot is too short or the park is off-line);
   2. checks that the slot walls are there and centres on them (else: stops, the ball is in the bay or not at the opening);
   3. searches along the slot for the round end; it must be at least the back-off distance + 0.1 in further in (else: stops, the slot is too short);
   4. backs off onto the straight part; one coarse and N fine passes of wall, wall, end;
   5. backs out the way it came in.
4. **Align and cut (cutter in)**, as in the hole mode.

Compared with the hole mode, side B has one more tool change (cutter, probe, cutter) and one short cut.

## 4. Why it works without a full-length bit

On side B, face A is on the spoilboard. The side-A slot goes from the spoilboard up to height D_A. The bay goes from the top (T) down to D_A minus a small overlap. Together they open a path from the top down into the slot. Side A cuts D_A and side B cuts T - D_A + overlap: each is about half the thickness, the same depths the flip-milling job itself needs. **So the half-slot mode works whenever the flip-milling job works.**

The probe does not have to reach down from above the stock. Its ball enters the slot from the side, and its body moves along inside the bay. The bay must be wider than the probe body. The only reach limit is that the collet nut stays above the stock top: the ball must be at least (T - probe length + clearance) above the spoilboard, which a long probe easily meets.

## 5. Placing the bay file before anything can be probed (rough fit)

There are two fits on side B, both with the same X/Y zero:

1. **Rough fit** from the jogged slot openings. It places only the bay file.
2. **Precise fit** from probing the slots after the bay cut. It places the side-B part files, exactly as the hole mode does. The rough fit is not used for it.

The slots are the through holes of the hole mode, moved out to the edges. For the rough fit, each opening is the point where the slot axis meets the stock edge.

**Stock that matches the stock box (the normal case): two openings.** Each opening is a known design point (slot axis at the box edge). Two points give the rotation and both shifts. This uses the existing two-point rigid fit. Simulation: 0.1 in jog error, board turned up to 5 deg, real edge within the given distance of the box edge (this includes the side-A zero error):

| Real edge from the box edge | Worst error at the slot ends (2000 cases) |
|---|---|
| within 0.05 in | 0.15 in |
| within 0.15 in | 0.24 in |
| within 0.30 in | 0.38 in |

So the 0.3 in bay oversize needs the real edge within about 0.15 in of the box at both slots.

**Irregular stock: add the key notch.** Where the real edge is not near the box, an opening only shows where the slot axis is, not where along it the slot is. The key notch, on an edge at right angles to the flip axis, gives that missing position. Virtual Dowel then puts each of the three jogged points on its design axis line (rotation, X, Y). Simulation (`docs/rough_fit_sim.py`): 0.1 in jog error and real edges up to 1 in inside the box give at most 0.19 in at the slot ends.

**Sizes** (found while building the routine in the simulator; the window will compute them from the probe settings):

- The probe body follows the ball. If the body is below the stock top while probing (free stylus length shorter than stock top minus the probing height), it is inside the bay, so the bay must leave room for it beside every wall touch and at the round end. Use the stylus stem radius instead of the body radius when the body stays above the stock top.
- **Bay half-width** at least rho_max + body radius + 0.4 in, where rho_max = (widest slot width - ball diameter) / 2. The 0.4 in covers the rough-fit error (up to 0.2 in) and the wall check's sideways search at the opening.
- **Bay length:** from outside the stock box to past the round end by rho_max + body radius + 0.4 in.
- **Bay depth:** to 0.15 in below the top of the side-A slot.
- **Slot width** at least ball diameter + 2 x (end-search length x tan 5 deg + 0.1 in): the end search runs along a machine axis, so on a board turned 5 deg it drifts sideways about 0.17 in over 2 in. About 0.6 in for a 1/8 in ball.
- **Slot length:** the round end at least 0.75 in inside the real stock edge.
- **Probing height:** the ball below the bay floor by at least its radius; the probe body bottom above the bay floor.

Built (`slot_bay_sizes`, CLI word `sizes`). The bay reach is measured from the round-end centre, the slot's reference point. Example: 1/8 in ball, 0.8 in body below the stock top, free stylus 1 in, 3 in stock, side-A slots 1.75 in deep, slots 0.625 and 0.75 in wide and 1.25 in long: bay at least 2.225 in wide, reaching 1.113 in past the round-end centre, 1.4 in deep; park the ball 0.65 to 1.49 in above the spoilboard. The simulator probes this minimum bay without a collision, with a 0.2 in rough-fit error and at both ends of the park band (a test).

The bay must be open to the outside edge. A closed pocket, round or not, does not work, because the probe cannot come down into it from above. An open bay also lets every plunge happen in the air outside the stock.

**Safety of the rough fit.** It is good to about 0.2 in, so it must never place a part file:

- it is applied only to the file chosen as the bay file, and the output is named `_VDROUGH.sbp`;
- the bay file's toolpaths must stay within the bay size of the two slots, or Virtual Dowel refuses it (this catches the part file chosen by mistake);
- the output has the normal runtime checks (units, X/Y zero, FP offset) and a first message that names it a rough bay cut;
- the precise probing that follows starts a new session, which deletes the `_VDROUGH` file.

## 6. Accuracy (expected about the same as the hole mode)

- **Across the slot** (both walls, opposite directions): ball size, pretravel, latency and backlash cancel exactly, as in a hole.
- **Along the slot** (the end touch, one direction only): half the measured width bias corrects the ball size and pretravel. What remains, for example backlash, has the opposite sign at the two opposite slots. So it cancels in the translation and shows only as a spacing error, which the existing spacing check reports.
- **Rotation** comes from the across-slot coordinates of the two slots (two-sided) over their full spacing: the same as two holes that far apart.
- **Stylus runout** shifts every touch the same way, as in the hole mode. The existing spin-180 option cancels it (the spindle can be turned with the ball in the slot).
- **Tram tilt** of the slot walls: as in the hole mode, but smaller, because the probe touches near face A, where side A started the cut.

**Reuse.** In the code, a half-slot is a hole with one open side. The routine skips the touch toward the open side. The centre along the slot is the end touch minus half the measured width. Size matching, the symmetric-layout and mirror checks, the rigid fit, the spacing check, the rewriter, the runtime checks and the stale-output protection are the hole-mode code, unchanged.

## 7. The bay cut file (made in Fusion)

Nikolai makes the bays in Fusion, as a Setup 2 operation or as their own file, posted with his ShopBot post. So the cutter, feeds, Z and the post's conventions are his. Virtual Dowel only moves the file (section 5), with the existing rewriter and its rules, and checks it:

- kept: every cut is within `bay_reach` of a slot. This does not measure the bays; it only stops the rough fit (good to about 0.2 in) from being applied to a part file by mistake. It works on any OpenSBP file (Fusion, VCarve, Aspire or hand-written), with the same parser that rotates every job file.
- dropped (Nikolai, 2026-10-04): reading the bay and slot sizes back out of the toolpaths. It would need the cutter diameter and guesses about the CAM, and it breaks on unusual toolpaths. Replaced by the **size calculator**: Nikolai enters the probe and stock numbers and the slot and bay sizes he plans; it gives the minimum sizes of section 5, the probing height band, and OK or not OK. One Python function, used by the window and by a CLI word; the guide shows the formulas and a worked example.

Rules for the bay operation (in the README):

- Size as in section 5. Open to the outside edge.
- Cutter: upcut spiral or straight end mill with a cutting length of at least the bay depth + 0.1 in, and no reduced neck.
- Enter from outside the stock: no plunge or ramp into material.
- Bays only in waste.

## 8. Half-slot mode: risks

| Risk | Effect | Control |
|---|---|---|
| Chips from the bay cut fall into the slot | Bad touches | Vacuum the slots before probing (the window says so). The repeat and width checks catch most bad touches. |
| Probe body wider than the bay | Collision | Probe sizes are machine settings. The window shows the minimum bay width for the Fusion design. All moves at probing height are guarded. |
| Round end less than 0.75 in inside the real edge (irregular stock) | No room for the wall touches | Rule in the README and the window. The routine stops with a message if the end comes too soon. |
| Wrong flip | Job turned 180 deg | Different slot widths, as with holes: the fit fails. |
| Bay overlaps the part | Damaged part | Bays only in waste (Fusion design). The window draws the moved bay file over the side-B toolpaths. |
| Part file chosen as the bay file | Part cut with the rough fit | Refused: the bay file's toolpaths must stay near the slots. Output named `_VDROUGH`. |
| Real edge farther from the box than about 0.15 in, and no key notch | Bay misses the slot end | The window shows the jogged points on a drawing of the design and the expected error. The precise probing finds no slot end and stops (guarded). Use the key notch for such stock. |
| X/Y zero changed between rough locate, bay cut and probing | Bays in the wrong place | The bay file has the runtime zero check; the precise probing measures again anyway. |
| Ball parked too high, in the bay | The probe body reaches the bay end wall before the ball touches anything; the stylus does not deflect, so the move would not stop | The routine checks for the slot walls at the opening first, with a search short enough that the body cannot reach the bay sides, and stops (simulator test). |
| Slot too short | Wall touches on the edge corners give a wrong width without any visible error | The routine stops if the round end is too close to the opening (simulator test). |
| FP of the bay file from inside a running file is not confirmed on SB3 | Extra step | Use C80 for each step (the window opens at the current step), as now. |

## 9. Through-slot mode (full-length bit, too little Z travel)

The same slots cut through the full thickness from outside. On side B there is no rough locate and no bay cut: the operator parks the probe outside each slot opening and the routine probes it. Same code as the half-slot mode, without steps 1 and 2. CAM note: with a full-length bit, the retract and linking moves must stay low and outside the stock.

## 10. Edge mode (built after the slot modes)

Kept from revisions 1-3. It uses no waste features, but every edge must be machined, and side A must be probed too.

### Method

At each corner, probe the two edges near the corner. Each edge then has two points, one near each end. A line goes through the two points of each edge, and the corners are where adjacent lines meet. The operator picks the corner where the Fusion WCS is and the edge that is its X axis. On side B, the window selects the same physical corner and edge from the flip type.

Why the same physical corner and edge: the job frame is origin C, X along E on both sides. Then:

- The real stock size does not matter; the Fusion stock box can differ from it.
- A rhomboid does not matter; only C and the direction of E are used.
- Errors that push every touch the same distance toward the stock cancel between the sides: wrong ball size, pretravel, stop latency, backlash. The probe always comes from outside the stock, so these errors point the same way in the stock's own frame.

If side B uses a different corner, the real stock size enters the result. So the window defaults to the same physical corner and warns if the operator picks another one.

The result is a rotation and a translation, the same as the hole mode. The OpenSBP rewriter, the runtime checks, the outputs and the stale-output protection stay as they are.

### Errors (checked with a simulation)

Simulation: stock 30 x 20 x 3 in, side A turned 1.3 deg, side B turned -2.1 deg after an end-over-end flip. Worst offset between matching side-A and side-B points. Script: `docs/edge_sim.py` (it becomes a unit test when the edge mode is built).

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
  One side always probes the lower half of the edge. If the other half sticks out over it by more than the ball-to-stem clearance (ball radius minus stylus stem radius), the stem touches the overhang. So probe the half that sticks out, or keep the step smaller than that clearance.

The window computes the two heights from T, s and the band, and refuses a height outside the band.

**Other errors that remain:**

- Probe noise. Rotation error about 1.4 x repeatability / edge length, the same as two holes that distance apart.
- Stylus runout: use spin-180 at each corner or keep runout under 0.0005 in.
- Local edge defects at the touch points. Touches are at the same distance from the corner on both sides, so a bowed edge gives the same chord both times.
- Direction-dependent pretravel (lobing): usually under 0.0005 in.

**Nikolai's edge preparation.** Opposite edges cut in one setup are parallel. The angle between the two pairs depends on the re-clamping, so the stock is a parallelogram. Two results follow:

- The parallelism of opposite edges is a good check of the measurement (a bad touch shows as a pair that is not parallel).
- A parallelogram looks the same after either flip, so the geometry cannot detect a wrong flip. The corner mark is the only control.

### Risks

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

### Operator procedure

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

### Fusion 360 rule

- Setup 1: WCS origin at the stock-box corner that is the physical reference corner C; X along the reference edge E.
- Setup 2: WCS origin at the same physical corner. After an end-over-end flip, a front-left C is at the back-left: stock box `0, -H, W, 0`, Y and Z inverted (Nikolai's earlier back-left habit).
- Stock box: the nominal size.

### Design decisions

1. **One routine.** Add the mode to `vd_probe.sbp` (`&vd_kind`) instead of a new file. It reuses the proven setup, finger test, input detection, `vd_touch_rec`, `vd_touch`, `vd_onhit`, error exits and the end SHELL. The hole path does not change. GOSUB depth stays at 5. New subroutines: corner, station, guarded move.
2. **Data format.** A new header tag for edge data, so the hole format stays the same byte for byte. `T` records keep their format (the `hole` field is the corner number; axis and sign give the edge; `z` gives the probing height).
3. **Python.** Parse the new header; `compute_edges` (stations, lines, corners, angles, parallelism, repeatability); `edge_frame(corner, edge) -> Fit`; edge checks. `run_alignment` is split so that the transform/check/write part is shared by all modes. CLI flags.
4. **Side-A record.** In `vd_state.json` (`edge_ref`): outline in the stock frame, corner and edge, probing height, date. `new_session` keeps it.
5. **Window.** *Locate with* switch; edge settings (side, flip type, thickness, step clearance; band limits as machine settings). The Align screen gets a tkinter Canvas: measured outline, four clickable corners, the X-edge choice, toolpath extents, side-A comparison. Standard library only.
6. **Simulator.** `tests/sbp_vm.py` gets a stock-block model (convex outline, optional edge tilt and two-half step). The routine runs end to end in it, with its error paths.


## 11. Half-slot mode: design decisions and phases

**Code:**

1. **A half-slot is a hole with an open side.** New per-hole field "open side" (none, +X, -X, +Y, -Y) in the window, the sidecar and the CLI; the window sets it from the nearest stock-box edge. The routine gets one direction variable per hole and skips the touch toward the open side. `T` records keep their format; a new record names the open side, so old data files still parse.
2. **Rough locate** is a short run mode of `vd_probe.sbp`: keypad at each opening, record `%(1)`/`%(2)` and `%(6)`/`%(7)`, write the data, start the window.
3. **Rough fit:** the existing two-point rigid fit for the normal case; the three-line fit when a key notch is cut (an option in the window, built in phase 3). The bay-file check is new; moving the bay file uses the existing rewriter and runtime header.
4. **Window steps.** The session records the step (rough locate, bay cut, probe, align). C80 opens the window at the current step, and each Start writes `vd_run.sbp` for that step, as now.
5. **Simulator.** `tests/sbp_vm.py` has stock with half-depth slots and bays (`SlotStock`), and checks the stylus stem, the probe body and the collet nut for collisions, not only the ball. It found the bay-size, slot-width and parked-in-the-bay problems above, and it shows the revision-2 slot error (a test).
6. **Guide (Nikolai's request).** `docs/guide.html`, shipped with the app: a styled page with annotated drawings of how to set up the side-A slots and the side-B bays in Fusion (WCS, flip axis, sizes from the probe settings) and how the probing works, step by step. The window's *Guide* button opens it (standard-library `webbrowser`); the README links to it.

**Phases** (each ends with all tests passing; nothing is "works on the machine" until Nikolai confirms it):

| Phase | Content | Check |
|---|---|---|
| 0 | v2.0 review fixes (section 13). Items 1-4 done in v2.0.1; the others wait for a decision. | a test per fix |
| 1 (done) | Python: open-side centre calculation, correction for a turned slot, open-side and stock-box checks; rough fit and bay-file check (moved here from phase 3) | synthetic touches; side-A -> flip -> side-B registration test |
| 2 (done) | Routine: half-slot probing; VM stock with slots, bays and stylus/body collisions | VM runs: both slots, turned board, mm machine, spin-180, end too close to the edge, bay too narrow, chips (false touch) |
| 3 (done) | Rough locate run in `vd_probe.sbp` (`&vd_rough`); `prepare_rough`; optional key notch (`fit_lines`); SHELL word `rough` (until the window step exists, it writes the report and the log) | VM run of the rough locate; three-line fit from noisy jogs (as in `docs/rough_fit_sim.py`); lint |
| 4 (minimum done 2026-10-04, for the first machine test) | Window: *Locate with* (holes, half slots, through slots), bay file and key notch, *Start rough locate*, rough-result window, *Guide* button. Visual size calculator added (*Sizes...*: live section and top-view drawings, too-small parts in red). | window tests |
| 5 (done) | README section 3b with machine tests H0-H3 and the dry-run kit in `docs/example`; `docs/guide.html` | Nikolai's tests |
| 6 | Edge mode | section 10 |

**Machine tests:**

- **M0** Preview dry run of every step (typed positions).
- **M1** Probe dimensions: body diameter, ball and stem diameters, nose-to-ball length, ball height above the spoilboard at the top of Z travel.
- **M2** Bay file: Preview, then an air cut (Z zero raised 2 in), then a cut in scrap.
- **M3** Half slots against holes on a thin board: side A cuts two holes and two half-slots; on side B, align with both. The difference is the half-slot error. No side-B cut of the part needed.
- **M4** Thick flip test: a feature cut half from each side; measure the step.

## 12. Questions for Nikolai

1. Is your usual flip end over end? (The plan assumes yes.)

Answered: probe sizes are settings (body diameter, ball and stem diameters, collet-nut-to-ball length); the bays and the slots are made in Fusion.

## 13. Phase 0: findings from the v2.0 review

Three independent review passes read all the code (2026-10-04). Each item says how it was checked: **run** (reproduced with code), **traced** (code paths read end to end), or **hypothesis** (needs the machine). The fit maths, the hole matching, the symmetry and mirror checks, and the unit scaling had no defect.

**Fix first (wrong result or safety rule broken). Items 1-4 approved by Nikolai on 2026-10-04 and done in v2.0.1:**

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
