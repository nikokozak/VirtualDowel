# Feature probing and side-neutral alignment - plan (draft for review)

Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

Status: revision 1, 2026-10-22. Sections 5 to 9 were agreed with Nikolai in chat (2026-10-16 to 2026-10-22). Nothing is built. Nothing is confirmed on the machine.

## 1. Goal and scope

Two changes:

1. **Side-neutral words.** Virtual Dowel stops calling itself a side-B tool. The fit maps reference positions, given in the coordinates of a job file, to measured positions, and rewrites that file. A flip is one use of it, not the only one.
2. **A *Probe features* tab.** The operator probes edges, corners and bores freehand, with no hole size and no preparation. The results stay in a log. From the log the operator can:
   - (a) zero X/Y on a feature (corner, bore centre, or one axis on an edge);
   - (b) make a frame from features and align job files to it.

   Nikolai chose (a) and (b) as the main jobs (2026-10-16). Inspection (c) comes last.

Scope does not change: Virtual Dowel probes, checks and aligns the operator's own files. It never makes toolpaths. *Zero X/Y* is the first action that changes the machine state live. It sets translation only, and only when the part is square to the axes within a limit (section 8.7).

## 2. Competitive position (research of 2026-10-15)

| | Packages |
|---|---|
| Common core: edge, inside/outside corner, bore/pocket centre, Z surface, set the work offset | all of them |
| Bore without a nominal size | [MASSO](https://docs.masso.com.au/getting-started-guides/machining-with-masso/probing), [Centroid](https://www.centroidcnc.com/centroid_diy/downloads/operator_manuals/centroid-cnc12-mill-operator-manual.pdf), [Tormach](https://tormach.com/articles/probing-techniques-for-accurate-machining-on-your-tormach-cnc), [QtDragon](https://linuxcnc.org/docs/html/gui/qtdragon.html). Renishaw and Haas need the diameter. |
| Skew applied to the job | Centroid [CSR](https://www.centroidcnc.com/centroid_diy/software_options.html) (paid option), QtDragon Auto Skew, [Mach4 Find Angle -> G68](https://www.machsupport.com/wp-content/uploads/2014/05/TouchOffHelp.pdf) |
| Inspection with tolerances; distance and angle between features | [Renishaw Inspection Plus](https://www.haascnc.com/content/dam/haascnc/en/service/manual/probe/english---renishaw-inspection-plus---programming-manual---2008.pdf) (O9834), [Set and Inspect](https://www.renishaw.com/sl/42122.aspx), [Fusion Part Alignment](https://help.autodesk.com/cloudhelp/ENU/Fusion-CAM/files/MFG-PROBING-OVERVIEW.htm) (extension) |
| Ring-gauge calibration | Renishaw, Haas, Tormach, Centroid |
| ShopBot | C2 Z plate, C3 prox home. The [3-axis zero plate](https://shopbottools.com/wp-content/uploads/2024/01/SBG-00444-Three-Axis-Zero-Plate-Guide-2015-10-16.pdf) only prints the material angle. The [SB3 command reference](https://shopbottools.com/wp-content/uploads/2024/01/SBG00253150707CommandRefV3.pdf) lists no rotation command. |

The other packages rotate the coordinate system in the controller (G68, CSR, G10 L2 R). No package that we found documents a fit of rotation and offset from two or more features followed by a rewrite of the program. SB3 cannot rotate, and Virtual Dowel already does this part. Position: match the common core, and be better at alignment.

## 3. Release order

| Release | Content | Needs before |
|---|---|---|
| v2.7 | Side-neutral words (section 4). The loop self-test file (section 9.6). No change in behaviour. | - |
| machine | Self-test results. The v2.6 graze fix confirmed (every feature uses the same touch). | v2.7 |
| v2.8 | Probe tab, step 1: edge, outside corner, bore or pocket; log; *Zero X/Y*; frame from an origin and a direction; *Align files to this frame*; ball radius for each direction | self-test results |
| v2.9 | Step 2: inside corner, web, boss, Z surface (measure only), best-fit frame from 3 or more features with residuals, edge intersections as origins, saved frames | v2.8 on the machine |
| later | Step 3: inspection (distances, angles, sizes against nominal and tolerance, CSV) | |

## 4. v2.7: side-neutral words

The side words did one real job: they told the operator which coordinates to type. They are replaced by the true rule, not only deleted:

- **The rule, in the window and the README:** "Reference positions in the coordinates of the files you align."
- **Renames:** *Side-B job files* -> *Job files*; *side-B coordinates* -> *job coordinates*; step *Cut side B* -> *Cut*; *Through holes* -> *Two holes* (any two bores work, not only through-holes).
- **Mirror check** (`mirror_alternatives`): it stays. New message: "The mirror image of these positions also fits. Check that they come from the files you align - not from the files of the other side."
- **"Flip" stays only where it is physical:** the half-slot and through-slot methods (two cuts in order), and the flip recipe in the guide. Method descriptions say "For a flip: ..." where it applies.
- **Not changed:** data formats, session keys, file names, function and fixture names.
- **Size:** about 140 places (`vd_app.py` 29, `vd_align.py` 42, `README.md` 28, `docs/guide.html` 37, `vd_probe.sbp` 2).
- **Tests:** the string assertions change with the words. No new behaviour, so no new behaviour tests.

## 5. Operator flow (agreed 2026-10-16)

1. **Open.** Type C80 and click *Probe features*.
2. **Choose a feature.** Click a symbol. The diagram shows where to park the ball and where each touch goes. Set the choices and distances. Click *Probe this feature*.
3. **Probe.** The window closes. SB3 shows a jog prompt that names the feature (for example "Corner front left: jog the ball below the top, about 0.3 in out from the corner"). The operator parks with the keypad and presses ESC. The probe touches, then lifts.
4. **See the result.** The custom cut opens the window again (section 9.2). The new feature is in the log. Go back to step 2 as many times as necessary.
5. **Use the result.**
   - (a) Select a corner or bore and click *Zero X/Y*. An edge gives *Zero X* or *Zero Y* only. The window writes a run file with `VA` (section 9.4). SB3 runs it, and the window opens again. Above the angle limit, the button tells the operator that the part is turned and offers alignment. It does not zero.
   - (b) Make a frame: select the origin feature, type its job X and Y (default 0, 0), and select which line a job axis runs along. Click *Align files to this frame*. This goes into the current align flow: the same rewriter, the same `_VD` runtime checks, the same results window.
6. **Finish.** Close the window. The loop ends.

Rules for all steps:

- All features are stored in table coordinates (section 8.1). If the operator zeroes between features, the log stays correct.
- If a touch fails, the window opens with the cause from the `ERR` line. The log does not change.
- A frame from two features is exact and has no check. The window says so. Three or more features (step 2) give a least-squares fit with residuals.

## 6. Window (agreed 2026-10-22)

**Look:** the current window - the `vista` theme on Windows, LabelFrames, the coloured status bar. No new theme (Nikolai, 2026-10-22).

**Layout rules** for the new tab, and for later changes to the other tabs:

1. *Probe features* is a `ttk.Notebook` tab next to *Align files*. *Calibrate* stays a button.
2. Left: three tiles - *Edge*, *Corner*, *Bore or pocket*. Each is a 48 px Canvas symbol with a label. One tile is selected.
3. Centre: the selected feature - title, choice arrows (`ttk` Toolbutton radios: 4 directions or 4 corners), the diagram, one help sentence, the parameters, the *Probe this ...* button.
4. Right: the log. Each entry has its number and type (grey), the result (Consolas 15) and the details (Consolas 9). Buttons *Delete* and *Save* (CSV).
5. Bottom: the frame strip - origin feature and its job X/Y, which job axis runs along which line, the result (rotation, shift), *Zero X/Y* and *Align files...*.
6. One default button with bold text: the next step. It is *Probe this ...* until a frame is complete, then *Align files...*.
7. Status bar: one line, coloured, says what to do next.
8. One drawing grammar for all Canvas pictures, also the current *Sizes* drawings: part light grey with a dark outline; ball as a black circle; park position as a dashed circle; path as blue arrows; touch points as blue dots; result as a black cross.

The minimum window size stays 820 x 560. Check it with a screenshot on the shop PC (Windows display scaling).

## 7. Cycles (agreed 2026-10-22)

| | Edge | Corner (outside) | Bore or pocket |
|---|---|---|---|
| Choose | approach direction (4 arrows) | corner (4 arrows); which face gets 2 touches (default: the face along X) | nothing |
| Park | ball beside the face, below the top, closer than *Search* | ball outside the corner, below the top, about *Park* out on both axes | ball over the middle, about 0.1 in above the surface (as now) |
| Touches | 1, or 2 at *Spacing* along the face | 2 on the chosen face, 1 or 2 on the other; first station at *Inset* from the corner | +X, -X -> move to mid -> +Y, -Y -> move to mid -> X pair again -> the current fine passes |
| Defaults (in) | Search 0.5, Spacing 1.0 | Park 0.3, Inset 0.5, Spacing 1.0, Search = Park + 0.2 | Search 1.0 from the park; Depth as now |
| Result | position; with 2 touches, also the angle | corner point; angle of the 2-touch face; with 2 + 2, squareness | centre, X and Y width, roundness |
| Ball size | necessary | necessary | cancels |

*Inset* changed from 0.25 in the chat to 0.5: `thick-stock-plan.md` section 10 uses 1.0 against chipped corners, and 0.5 still suits small parts. To confirm.

### 7.1 Geometry

- **Edge.** Approach direction d is one of +X, -X, +Y, -Y. Touch 1 is made from the park. For touch 2 the probe backs off 0.1 in, makes a guarded move of *Spacing* along the face (signed: + is toward + on the face's axis), and touches again. The diagram shows where touch 2 goes.
- **Corner.** s = (sx, sy) points from the corner into the part (front left: +1, +1). The estimated corner is C' = park + Park x s.
  - Face along X (touched in direction sy along Y): stations at x = C'x + sx x Inset, then + sx x Spacing; the probe stays on the park row (y = park y) between touches.
  - Face along Y (touched in direction sx along X): stations at y = C'y + sy x Inset, then + sy x Spacing; the probe stays on the park column (x = park x).
  - To change face, the probe goes back to the park first. All moves stay on the park row or column, outside the part's quadrant.
- **Bore or pocket.** The mid of any chord is on the centre line, so the first X pair gives the true centre X at any Y. The Y pair taken at that X gives the true centre Y and the diameter. The measured diameter then goes into the current fine passes (`vd_center_pass`). A rectangular pocket gives its centre and its two widths with the same cycle.
- **Each station:** one coarse touch, then N fine passes (back off, slow touch), as in the hole mode (*Passes* setting).
- **Touch height:** the park height for edges and corners; the park height minus *Depth* for a bore.

### 7.2 Rules for all cycles

1. Every move below the top is guarded: `vd_gmove` (section 9.1). This includes the moves between touches, along a face and around a corner. A trip on such a move is abort code 10, "hit something between touches", and the probe lifts, as on every abort.
2. A park that is too far out gives "no contact" (code 1), and the probe lifts.
3. All touches use `vd_touch`, so the v2.6 graze rule and the `ERR` line apply.
4. Spin-180: for edges and corners the stylus offset does not cancel, as it does in a bore. When spin-180 is on, each touch is made again after the spin and the two are averaged.
5. Ball size for each direction: calibration already touches +X, -X, +Y and -Y in its ring. It stores an effective radius for each direction (section 10). If edges or corners have no calibration for the current ball setting, the status bar warns, and the log marks the result "uncalibrated". The probe still runs.
6. The routine records the touches. Python does the calculation, as now.

## 8. Calculations (Python)

1. **Table coordinates.** Each `T` record has x, y (current zero) and bx, by (`%(6)`, `%(7)`). The table position is (x + bx, y + by). The log stores features in table coordinates, in the units of their run.
2. **Ball compensation.** The contact point is the ball centre plus r_d along the approach direction d. r_d is the effective radius for that direction from calibration. With no calibration: tip / 2, and the result is marked "uncalibrated".
3. **Edge.** 1 touch: the face position on its axis. 2 touches: the line through the two contact points, and its angle to the axis.
4. **Corner.** The line of the 2-touch face. The other face: the line through its touches; with 1 touch, the line at 90 deg to the first line through that touch. The corner is the intersection. Squareness (2 + 2 only) is the angle between the lines. The angle of the corner is the angle of the 2-touch face.
5. **Bore or pocket.** Centre and widths from the final passes, as `compute_holes` does now (centre: mean of the +/- ball centres; width: span + 2 r). Roundness: X width minus Y width.
6. **Frame.** Origin O: a corner point or a bore centre, with job position (jx, jy). Direction: a measured line (a corner face, or an edge with 2 touches) or the line from O to a second point feature. The operator selects which job axis (+X, +Y, -X, -Y) runs along it. A corner face points away from its corner; an edge line points to the side nearer the table's +X, and a *Reverse* tick changes this. Then theta = angle of the line - angle of the job axis, and the translation puts (jx, jy) on O. The frame is converted from table coordinates to the zero of the last feature run (its bx, by) and becomes a `Fit`. The current `max_rotation_deg` limit (5 deg) applies, and it can be changed in `vd_settings.json`.
7. **Zero X/Y.** New position values: the tool's table position minus (O - (jx, jy)). The run file calculates this at run time (section 9.4). Limit: `zero_angle_max` = 0.05 deg, a new `DEFAULTS` entry. An edge with 1 touch has no angle; the window says "angle not measured" and allows *Zero X* or *Zero Y*.
8. **Units.** A frame from features of different units is refused. Job files in mm go through the current rewriter conversion.

## 9. SB3 mechanics (agreed 2026-10-22)

### 9.1 Where the cycles live

In `vd_probe.sbp`, as a new mode next to holes, slots, rough locate and calibration (`&vd_feat` = 1 edge, 2 corner, 3 bore or pocket). The cycles share `vd_touch`, `vd_lift`, the finger test, the error paths and the `ERR` line. A separate template was rejected: it would need a copy of the touch code, and the two copies would drift.

- New subroutine `vd_gmove`: a guarded move. `vd_moveto` today is a plain `M2` with no guard.
- The data goes to `vd_feature_data.csv`. A feature run never touches the alignment data, the outputs or the calibration (the same pattern as calibration).
- The jog prompt comes from `render_say`, which fills it with the feature's name.
- The template grows by about 150 to 200 lines. GOSUB depth stays at 6 or less. Line length stays at 226 or less.

### 9.2 The loop in the custom cut

`start_file_text` writes the custom cut and `vd_start.sbp`. The operator runs INSTALL.bat once after the update.

```
vd_loop:
'empty vd_run.sbp
SHELL, <python> vd_align.py prepare, 1, SYNC
&vd_more = 0
FP, vd_run.sbp
IF &vd_more = 1 THEN GOTO vd_loop
```

Only a feature run sets `&vd_more = 1`. If anything goes wrong, the loop stops:

- If the window does not open, `vd_run.sbp` stays empty and the loop ends.
- If the operator closes the window, a stub runs and the loop ends.
- Alignment and calibration runs never set the variable, so they behave as they do now.
- `&vd_more` is reset before each FP, so an old value cannot restart the loop.

### 9.3 The end of a feature run

No finish MSGBOX and no SHELL. The run lifts, closes the data file and returns; the loop opens the window. The window sees a feature session whose data is newer than the last start, adds the feature to the log once (guarded by the data hash) and shows the *Probe features* tab.

Unknown: whether `END` in an FP'd file also stops the custom cut. If it does, feature mode ends with a `GOTO` to the last line instead of `END`. The self-test (9.6) decides.

### 9.4 Zero X/Y

The window writes a short `vd_run.sbp` with `&vd_more = 1`. The values come from the table coordinates of the feature, at run time, because the operator may have jogged since probing:

```
&vd_zx = %(1) + %(6) - <Ox - jx>
&vd_zy = %(2) + %(7) - <Oy - jy>
VA, &vd_zx, &vd_zy
```

After `VA`, the file checks that `%(6)` and `%(7)` equal Ox - jx and Oy - jy within 0.001. If they do not, a MSGBOX tells the operator to zero X and Y by hand. Aligned files made before the zero stop at their runtime check, as designed. *Zero X/Y* is not built until the self-test confirms that `VA` changes `%(6)`/`%(7)`.

### 9.5 Align files to a frame

`run_alignment` gets an entry point that takes a `Fit` instead of probe data (`thick-stock-plan.md` section 10 already plans this split). The rewriter and the `_VD` header do not change. The runtime check uses the zero and the units of the last feature run. If the operator zeroes again after that, the aligned file stops, and the operator aligns again.

### 9.6 Self-test before any feature code: `vd_selftest_loop.sbp`

Ships in v2.7. Move mode, no motion commands. It uses the folder that INSTALL.bat writes into it, as `vd_selftest.sbp` does. It writes its own child files, then checks:

1. Does the caller see a variable that an FP'd file set?
2. Does the caller continue after `END` in an FP'd file? And after a file that ends without `END`?
3. Does `VA` with variables change `%(1)`/`%(2)`? Does it change `%(6)`/`%(7)`? This also answers the open item in AGENTS.md section 5: the runtime zero check depends on `%(6)`/`%(7)` changing when X/Y is zeroed again.

Each stage writes its own small result file, so a stop shows which stage stopped. At the end the file puts the zero back where it was and checks this. If the zero is not back, a MSGBOX tells the operator to zero X and Y. The VM learns FP and `END` only after these results (AGENTS.md section 7).

## 10. Data

Feature data `vd_feature_data.csv` (one feature per run in step 1):

```
VF,1,units,sim,tip,pin,trig,passes,spin180     header
CFG,depth,margin,backoff,fast,slow,zspd
DT,<%(146)> <%(147)>
FEAT,id,kind,choice,n2,spacing,inset,park,search    kind 1 edge, 2 corner, 3 bore or pocket;
                                                choice: direction or corner 1-4 (-X, +X, -Y, +Y as the slot open codes;
                                                corners FL, FR, BL, BR); n2: touches on the second face
T,id,orient,pass,axis,sign,x,y,z,bx,by          as now; the hole field is the feature id
END,1
ERR,code,...                                    as now; new code 10: hit something between touches
```

- `vd_features.json`: the log. One entry per feature: id, kind, choice, units, date, result in table coordinates, "calibrated" flag, data id. It is kept until the operator deletes entries.
- `vd_state.json`: session kind `features`.
- `vd_machine.json`, `calibration`: new `radius` with +X, -X, +Y, -Y, for the ball setting it was measured with.
- `.gitignore`: `vd_feature_data.csv`, `vd_features.json`, `vd_selftest_loop*.txt`.

## 11. Safety invariants (new)

- In a feature cycle, every XY move below the top is guarded. A trip that is not a touch aborts with a lift (code 10).
- The feature routine never changes the zero. Only the *Zero X/Y* run file does, after the operator clicks, and it checks the result.
- A feature run never touches probe data, outputs or calibration.
- *Zero X/Y* is built only after the self-test confirms `VA` and `%(6)`/`%(7)`.
- The custom-cut loop stops on every failure (9.2).

## 12. Testing

- VM: a block model in `tests/sbp_vm.py` - a rectangle at any angle, with an optional bore or pocket and an optional clamp in the path. Each cycle runs end to end, in all 4 directions and corners, with 1 and 2 touches. Error paths: a miss, a hit between touches (the clamp), a graze, a trip at arming, an unplugged probe. Spin-180 averaging.
- VM, after the self-test: FP with shared variables and `END` as the machine showed; then the loop with the custom-cut text.
- Python: unit tests for each calculation in section 8. Frame -> `Fit` -> rewriter checked with `sim_sbp.compare` on both fixtures at several angles. The `VA` formula.
- Window: the tab builds; tiles select; the default button moves; the log survives a reopen; *Zero X/Y* refuses above the limit.
- Static checks that exist already cover the new `.sbp` code: THEN actions, PAUSE, quoted text, variables used before assignment, line length.

## 13. Relation to the thick-stock edge mode

`thick-stock-plan.md` section 10 designs a four-corner edge mode for flipped thick stock (same physical corner on both sides, probing heights in a band). The corner cycle here is its single-corner step and is built first. The edge mode can later become a preset that probes four corners and compares the sides. Its error table applies here too: edge tilt times the difference in touch height, lobing, stylus runout.

## 14. Limits and risks

- **Accuracy ceiling:** `%(1)` reads to 0.001 in; `ON INP` latency; gantry repeatability. We can match the features of MASSO, Centroid and QtDragon, not the accuracy of a Renishaw probe on a machining centre.
- **Wood:** edges and corners are worse references than drilled holes (fuzz, tear-out, sheet edges out of square). Holes stay the accurate method. The log shows spread and squareness so the operator can see the quality.
- **Edge tilt:** the result is at the touch height. Touch at the height that matters for the cut.
- **Clamps:** they are in the park row and column. Guarded moves stop the probe; keep that path free.
- **Two-feature frames** have no check (5, rules).
- **Round trip:** one window start (about 1 to 2 s) per feature.

## 15. To confirm on the machine

1. The self-test (9.6): FP variables, `END` in an FP'd file, `VA` with variables, `%(6)`/`%(7)` after `VA`.
2. The v2.6 graze fix.
3. The window at the shop PC's display scaling (a screenshot).
4. The defaults: Park 0.3, Inset 0.5, Spacing 1.0, Search; the zero limit 0.05 deg.
