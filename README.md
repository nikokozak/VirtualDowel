# Virtual Dowel: flip-milling alignment for ShopBot (SB3)

Copyright (c) 2026 Nikolai Kozak. All rights reserved. Proprietary - see LICENSE.txt.

Virtual Dowel replaces dowel pins with two reference holes and the ShopBot digitizing probe.

1. On side A, the job cuts two reference holes through the waste.
2. After you flip the board, the probing routine finds the centres of those holes to within a few thousandths of an inch.
3. The alignment script then rotates and shifts your side-B `.sbp` files so they match where the board actually lies.

Everything happens in one **Virtual Dowel window**, opened from ShopBot with the custom cut **C80**: choose the files, holes and probe settings, click Start, probe, and the same window shows the results.

You don't square the board to the machine, and you don't zero X or Y on side B.

```
VirtualDowel\
  vd_probe.sbp     probing routine - the window fills in its settings (template)
  vd_align.py      alignment script (Python 3, standard library only)
  vd_app.py        the Virtual Dowel window
  vd_align.bat     double-click to open the window on the last probe data
  vd_selftest.sbp  offline check of SB3 behaviour (Preview mode, no motion)
  vd_install.bat   double-click once: points the .sbp files at this folder
made by vd_install.bat / the window:
  venv\            the Python that SB3 starts (pythonw.exe: no console window)
  vd_start.sbp     opens the window, then runs vd_run.sbp (same as the custom cut)
  vd_run.sbp       the probing run the window prepared - do not edit
  C:\SbParts\Custom\Custom80.sbc   the custom cut C80
  README.md        this file
  tests\           automated tests: python -m unittest discover -s tests
```

---

## 1. Design rules for side A (read this first)

Put **two reference holes** in the waste. Follow all of these rules:

| Rule | Why |
|---|---|
| **Make the two holes different sizes**, e.g. **0.500 in and 0.625 in**. | Two identical holes look the same after a half turn. A board flipped end-over-end instead of side-over-side would then pass every check and cut side B in the wrong place. With different sizes, the probe can tell which hole is which. The script **fails** on symmetric layouts unless you override it. |
| **Put both holes on the flip axis**: the centreline the board turns over on. | A point on the flip axis has the same X,Y on both sides, so you can't type side-A numbers by mistake. With two off-axis holes, the side-A and side-B numbers both fit the probe data, so the script **fails** unless you use 3 holes that are not in a line, or override it. |
| **Space them as far apart as possible**, e.g. one at each end of the centreline. | The accuracy of the rotation scales with 1/spacing. |
| **Cut them through the board plus about 0.02 in into the spoilboard**, as circle pockets or inside profiles with a sharp end mill. | You need clean, vertical walls. The probe works about 0.2 in below the surface on side B, where tear-out from the side-A exit hole is usually gone. |
| Keep each hole at least **0.25 in larger than the probe ball**. | This leaves room to centre the probe. |

Example: a 24 × 30 in blank flipped left-to-right (about the vertical centreline X = 12) with the origin at the bottom-left corner:

- hole 1 at **X 12, Y 1, Ø 0.500**
- hole 2 at **X 12, Y 29, Ø 0.625**

These are the same numbers in the side-A and side-B files.

---

## 1a. Fusion 360 setups

The script doesn't care where Setup 2's origin is. It needs one thing: the hole coordinates **measured in Setup 2's WCS**.

- **Setup 2 WCS:** your usual convention works. Put the origin at the stock-box corner that sits **front-left once the board is flipped**, with Z and one horizontal axis inverted.
- **Which axis you invert must match how you physically flip the board:**

| Setup 2 inverts | Physical flip | Flip axis (put the holes on it) | Example, 24 × 30 in stock |
|---|---|---|---|
| **Y** and Z | end over end | **Y = H/2** | (1, 15, Ø0.500) and (23, 15, Ø0.625) |
| **X** and Z | side over side | **X = W/2** | (12, 1, Ø0.500) and (12, 29, Ø0.625) |

  With the holes on that line, Setup 1 and Setup 2 give the same hole coordinates. If you flip the other way from what Setup 2 assumes, the different hole sizes catch it and the script stops.
- **Cut the holes in Setup 1** with a 2D Pocket, Bore or Contour, through the stock plus 0.02 in.
- **Post** with Autodesk's ShopBot (OpenSBP) post. It doesn't write the stock size into the file, so the script asks once for the **stock box in Setup 2's coordinates**: `xmin, ymin, xmax, ymax`.
  - The prompt shows the X/Y range the file's toolpaths cover, to help you choose.
  - **With the origin at the back-left corner**, the stock lies at negative Y. A 15 × 5.5 in stock is then `0, -5.5, 15, 0`, and the flip-axis line is Y = −2.75.
  - `W, H` alone means the origin is at the lower-left corner.
  - A box that doesn't cover the toolpaths fails with a clear message, rather than feeding wrong numbers into the other checks.
  - The answer is remembered with the hole coordinates.
- **Kept as-is:**
  - The post's spindle-lockout `ON INPUT … MSGBOX` line.
  - The final `JH`, which returns to **your current X/Y zero** at safe height, not the part origin. The unaligned file would do the same.
- **Z:** zero where Setup 2's WCS says, using the cutter. Look at the numbers, not the header.
  - Your B-Side-Test export cuts down to Z 0.025 with 1.45 in of material, so its Z zero is the **stock bottom (spoilboard)**. That holds even though the header line says `&PWZorigin = Part Surface`.
  - A spoilboard Z zero is the better choice for flip work anyway, because variation in board thickness doesn't change the through-cut depths.

---

## 2. Install (once)

1. Install **Python 3** from python.org, keeping the "py launcher" option ticked. Tkinter is included by default.
2. Unzip the folder to **`C:\SbParts\VirtualDowel\`**.
   - The folder path may only contain letters, digits, `_` and `.`: no spaces, `-`, `~` or other symbols. ShopBot's SHELL changes those characters when it starts the script. On your SB3 it put spaces around `/` and deleted `~`. The installer refuses a path that breaks this rule.
   - Watch for a doubled folder (`VirtualDowel\VirtualDowel`) after unzipping.
3. **Double-click `vd_install.bat`** in that folder. It:
   - points `vd_probe.sbp` and `vd_selftest.sbp` at the folder they're in;
   - makes a small Python environment in `venv`, whose `pythonw.exe` has a plain path ShopBot can start;
   - writes `vd_start.sbp` and the custom cut **`C:\SbParts\Custom\Custom80.sbc`**. If Custom80 is someone else's file, it uses the next free number up to 89; it never overwrites a custom cut that isn't Virtual Dowel's. The output says which number it used.
   - Run it again whenever you move the folder, reinstall Python, or update Virtual Dowel (it rewrites the custom cut and `vd_start.sbp`).
4. **Calibrate the probe once** (section 3a). It finds the probe's input and measures it; the settings are saved for every later run.

You no longer edit `vd_probe.sbp`. The window keeps the probe settings in `vd_machine.json`; on first use it starts from whatever `vd_probe.sbp` contains, so earlier hand edits carry over.

---

## 3. Each job

1. **Side A:** run your side-A file, which includes the two holes. Flip the board over the planned axis and clamp it anywhere, at any angle, as long as it is within about ±5°.
2. Put the **probe** in the collet and plug in its cable. You don't need to zero X or Y.
3. **Type `C80`** in ShopBot (Move mode). The Virtual Dowel window opens at step 1, **Prepare**:
   - **Side-B job files:** *Add files...* and select all of them at once, e.g. one per tool.
   - **Reference holes:** X, Y and diameter in side-B design coordinates. *+ Add hole* for a third. They are remembered per file and pre-filled next time.
   - **Stock box:** only if a file doesn't state its stock size (Fusion). The note under it says whether it's needed and shows the toolpaths' extent.
   - **Probe:** input, deflected state, ball diameter, fine passes, spin-180, finger test. *Advanced...* has the depths and speeds. *Dry run* probes nothing and asks you to type the hole positions (use it in Preview).
   - *Check* runs every check that doesn't need the probe on every file: hole layout, stock box, side-A/side-B ambiguity, files the script can't rewrite. A file that would fail even with perfect probing is marked **FAIL** before you probe.
   - **Start probing** checks again, removes aligned files and probe data from earlier runs (the board has moved), writes `vd_run.sbp` with your settings, and closes. ShopBot runs it straight away. Its first message names the time it was prepared, so an old run file is easy to spot.
   - Files in mm work too: hole values are in the file's units, and every selected file must use the same units.
   - *Close* without Start runs nothing.
4. **Probing** (step 2) runs as before: finger test, then for each hole the keypad opens. Park the tip over the hole centre about 0.1 in above the surface and press ESC.
5. When probing finishes the window reopens at step 3, **Align**, with your files and holes filled in, and aligns them all at once:
   - The table shows **PASS/FAIL**, rotation, spacing error and output file per file. Click a file to see its full report.
   - A file that fails writes nothing and doesn't stop the others. You can edit a hole value and click *Align* again.
   - *Probe data* shows each measured centre, diameter and repeatability.
6. Swap the probe for the first file's cutter and **zero Z only**, e.g. with C2. **Do not re-zero X or Y.**
7. `FP` → **`<job>_VD.sbp`**. It first checks that nothing has changed since probing: X/Y zero, units, and no FP offset or scale. Then it cuts.
8. **Next file:** change the tool, zero **Z only**, `FP` its `_VD.sbp`. All files aligned from one probing run stay valid until you probe again.

To align again from the last probe data without probing (e.g. another file), double-click `vd_align.bat`.

## 3a. Probe calibration

Do this once, and again whenever you change or re-seat the probe.

1. You need a round hole of known size with a clean wall: a ring gauge, or a hole bored in aluminium or acrylic and measured with calipers. Clamp it anywhere.
2. Type **`C80`**, click **Calibrate probe...**, enter the measured diameter **in inches**, and click *Start calibration*. Defaults: 10 passes, spindle turned 180° halfway, find the probe input first.
3. ShopBot asks you to **hold the probe tip over for about 3 seconds**: it reads all 8 inputs, sees which one changes, and records it and its deflected state. Then park over the hole as usual, and turn the spindle by hand when asked.
4. The window shows:
   - **Probe input** and what it reads when deflected.
   - **Repeatability**: how far one pass's centre lands from the average. Under 0.001 in is good.
   - **Effective ball**: the ball diameter as the probe sees it, including trigger travel. Saving it makes the measured hole diameters true.
   - **Ball off spindle**: the stylus offset. Over about 0.001 in, turn on spin-180 for jobs, or re-seat / straighten the probe. It changes every time the probe is re-seated.
5. **Save to probe settings** stores the input, deflected state and effective ball for every later run, and writes them into `vd_probe.sbp` too. A dry-run calibration never changes the settings, and the effective ball is only saved when repeatability and roundness are good.

Calibration writes its own data file (`vd_cal_data.csv`). It never touches the job's probe data or your aligned files.

**Report lines to glance at:**
- **Rotation:** how skewed the board was.
- **Spacing error:** under about 0.005 in is normal. Larger means the board moved, the design numbers are imprecise, or the machine scale is off.
- **Probe size bias:** normally a small positive number, the same every time.
- **repeat:** under about 0.001 in is normal.

---

## 3b. Thick stock: half slots

Use this when the board is too thick to drill the reference holes through: the machine cannot lift a cutter as long as the board is thick above the board. The full guide, with drawings, is `docs\guide.html` (the **Thick-stock guide** button in the window opens it).

**The idea.** The two reference holes move out to the edges of the board and become U-shaped slots that open to the outside: half slots. Side A cuts them from face A to a little past mid-thickness, from outside the board. On side B, a short bay cut from face B opens a path down to each slot. Then the probe comes in from the side and probes each slot like a hole. Virtual Dowel never makes toolpaths: you make the slots, the bays and the side-B job in Fusion.

**Sizes.** Get the minimum slot and bay sizes for your probe and stock first: click **Slot and bay sizes...** in the window (a live drawing of side B and of one slot and bay; anything too small turns red), or in a command window in the VirtualDowel folder (all values in inches; add `--mm` for millimetres):

```
py -3 vd_align.py sizes --ball 0.125 --stem 0.08 --free 1 --body 0.8 --length 3 --thick 3 --slot-depth 1.75 --widths 0.625,0.75 --slot-length 1.25 --bay-width 2.3 --bay-reach 1.2
```

`--free` is from the ball centre to where the probe body gets wider; `--length` is from the ball centre to the collet nut. It prints OK or the problems, and the height to park the ball at.

**Each job.**

1. Side A: two slots on the flip axis, one in each end of the board, different widths, cut inward from outside to a round end. Enter the X and Y of each round-end centre in side-B coordinates. Zero X/Y at the Setup 1 WCS corner and run side A.
2. Flip and clamp. Keep the slot openings free.
3. Fit the bay cutter. Type `C80`, choose **Half slots** under *Locate side B with*, add the side-B files, enter the slots and the stock box, choose the bay cut file, and click **Start rough locate (bay cutter in)**. Jog the bit tip over each slot opening (slot 1, then slot 2; the key notch last, if you cut one).
4. The window says which file to run: `<bay file>_VDROUGH.sbp`. Look at it in Preview, zero Z with the bay cutter, and run it. Do not zero X or Y.
5. Vacuum the slots. Fit the probe. Type `C80` and click **Start probing (probe in)**. At each slot, jog the probe ball about 0.3 in outside the opening, in line with the slot, at the park height from the size calculator.
6. The window aligns the side-B files as usual. Fit each cutter, zero Z only, and run each `_VD.sbp`.

**Through slots** (a bit as long as the board is thick, but not the Z travel to lift it over the board): cut the slots through from outside on side A, choose **Through slots**, and go straight to step 5.

### First tests on the machine (half slots)

Nothing of the half-slot mode has run on the machine yet. Please do these in order and send me the files listed.

**H0. Install (2 min).** Copy the new VirtualDowel folder to the shop PC and double-click `vd_install.bat`. It writes the new custom cut.

**H1. Dry run in Preview (10 min, no motion).**
1. In Preview, type `C80`. Under *Locate side B with*, choose **Half slots**. Add `docs\example\sideB_example.sbp` as the side-B file (it states its 24 x 30 in stock size). Enter slot 1 as X 1.5, Y 15, width 0.625 and slot 2 as X 22.5, Y 15, width 0.75. Choose `docs\example\bays_example.sbp` as the bay file. Tick **Dry run** and click **Start rough locate**.
2. When asked, type the X and Y of each slot opening and `0`: `0.05, 15.1, 0` for slot 1 and `24.03, 15.2, 0` for slot 2.
3. A window should say "Bay file placed" and name a `_VDROUGHSIM.sbp` file (Preview only).
4. Type `C80` again, keep Dry run, click **Start probing**, and type each slot's round-end centre and width when asked: `1.5, 15, 0.625` and `22.5, 15, 0.75`. The window should align with PASS and write `sideB_example_VDSIM.sbp`.
5. Send `vd_launch.log`, `vd_rough_data.csv`, `vd_probe_data.csv` and the reports. If SB3 stops on a line, send the line number and the message.

**H2. Probe two slots, no flip (20 min).** In a scrap board, cut two U-slots open to the edges, full depth (a thin board is fine), with the sizes from the calculator. Do not move the board. Fit the probe, `C80`, **Through slots**, enter the slots, **Start probing**. Watch: at each slot the probe moves in a little, touches both walls, goes to the end, makes its passes and backs out. Send the probe data and the report; the spacing error should be under 0.005 in.

**H3. Half-slot flip test on thick scrap.** Side A: the two half slots plus a test feature cut half from each side (for example a 0.25 in slot). Flip with a small turn, rough locate, bay cut, probe, align, cut side B. The step where the two halves of the feature meet is the registration. Send the files and the step you measure.

---

## 3c. Any part by two references: centre and line

Use this for a part that is not flip-milled and whose size is not exact: a 3D print, a casting, a found object. There is no flip. A print shrinks or grows by up to about one percent, a casting more, so the distance between two references in the part is not the design distance. Virtual Dowel does not check that distance here. It puts the job's design midpoint between the two references on the measured midpoint, and turns the job so that the design line through the references lies on the measured line. The report gives the size difference as a percentage. A feature near the midpoint lands where it was designed. A feature at a distance r from the midpoint is off by about that percentage times r.

Design rules:
1. Make two references in the part: bores in the top face (through, or blind and deeper than the probe plunge depth under Advanced) or notches in an edge (open to the top and to the side). Make them different sizes, for example 0.5 and 0.625 in. With equal sizes a part placed half a turn round passes unnoticed, and the job is cut turned.
2. Put them on one line through the job's X0 Y0, with X0 Y0 halfway between them. The report warns when the design midpoint is not at X0 Y0.
3. The job files are in the part's own coordinates. No side A, no side B.

Each job:
1. Clamp the part to the table, square within 5 degrees. Do not zero X or Y.
2. Type C80. Under "Locate the part with" choose "Centre and line", then "Bores in the top face" or "Notches in the edge".
3. Add the job files. Enter the two references: X and Y of the centre (notch: of its round end) and the diameter (notch: the width). Notches need the stock box: the part's outline in job coordinates.
4. Click Check. Fit the probe and click Start probing. Bores: jog the probe over each bore. Notches: jog the ball about 0.3 in outside the opening, in line with the notch.
5. The results window shows the rotation and the spacing error; the report gives the shrink as a percentage. PASS writes `<job>_VD.sbp`. Zero Z and run it.

Checked: the reference sizes (within 0.06 in of the design), roundness and repeatability of each reference, rotation within 5 degrees, and the runtime checks of every `_VD` file. Not checked: the spacing between the references. Command line: add `--centre` to the align command.

Not yet run on the machine. The first test: a 3D print with two bores, a job that only drills a small mark at X0 Y0, and a ruler.

## 4. Safety checks built in

**Probe routine**
- The probe must read idle before every guarded move; a trigger only counts when the input changes.
- It stops if a trigger arrives between arming and moving.
- It rejects a "touch" that didn't stop short of its target or didn't leave the tip deflected.
- A guarded plunge stops if the tip lands on the surface. You are then asked to jog again.
- The search distance shrinks to *this* hole's measured radius plus the margin after the first pair of touches.
- On any error, it lifts out at the hole centre.
- The data file is truncated at the start, so an aborted run never leaves old data to align against.
- Every new probing run first deletes the aligned files from earlier runs, so an old `_VD.sbp` can't be run on a new board.

**Script**, which fails and writes nothing when:
- the data is incomplete or more than 2 h old (the GUI asks before using old data)
- the layout is symmetric
- hole sizes don't match the design in any order
- the rotation is more than 5°
- the spacing error is more than 0.02 in
- hole scatter or out-of-roundness is too large
- a hole lies outside the material box in the job header
- the hole numbers could be the side-A numbers: the mirrored layout fits the probe data just as well and would shift the job by more than 0.1 in
  - Smaller shifts, e.g. holes a few hundredths off the centreline, only warn.
  - If the job file has no material size and you skip the stock-size question, any 2-hole layout fails this check.
- the job has bare-CR or mixed line endings, or contains no X/Y moves
- the units don't match
- the job contains anything it can't transform exactly

**Commands the script rejects:** G-code, CR, CC, CP, CA, FP, X/Y re-zeroing, variables in X/Y, ellipses, and similar. Any file offset, X/Y proportion or temporary tool offset also stops the job. `JH`/`MH` are kept, with a warning: they go to your current X/Y zero.

**Aligned file**
- At the start, and after every custom cut, operator pause, message box or keypad call, it checks that X/Y zero, units and the FP offset/proportion are unchanged. If not, it stops.
- In Preview mode the zero check is skipped.

**Not caught:** anything the probe can't see, such as a board that moves after probing or a wrong Z zero.

---

## 5. Accuracy: what limits it

- **Probe repeatability.** You measure this in Stage 2 below.
  - The rotation error is about √2 × repeatability ÷ hole spacing. For example, 0.001 in over 28 in is about 0.003°, or 0.0014 in across 24 in.
  - Latency and backlash cancel out in the centres, because each centre is the midpoint of two opposite touches.
- **Probe stylus runout.** An off-centre stylus shifts the *whole job* by that amount; it has no effect on the rotation.
  - Set `&vd_spin180 = 1` once to measure it. The routine asks you to turn the spindle 180° by hand at each hole and averages both readings.
  - If the report shows runout under a few thousandths, you can leave it off.
- **Hole quality**, the board moving between sides, and the machine's own positioning error while cutting.

---

## 6. Staged first test on the machine (please do this in order)

Everything here was tested against a simulated machine and probe: 80 automated tests, including a Fusion-style job, plus three independent review passes. Your real Fusion B-side export was also transformed exactly in a local run. The **SB3 interpreter itself was not available**, so these stages check the things only your machine can confirm.

### Confirmed on your SB3 (self-test, 2026-09-28)

**These work:**
- file writing with both literal and variable paths
- `%(22)` = 1 in Preview
- `%(61)`/`%(62)` = 0 and `%(66)`/`%(67)` = 1, so the FP offset/scale check won't block normal runs
- the three-way message-box answers
- INPUT with three values
- 6-deep subroutine nesting
- arming an input handler with variables
- fractional PAUSE
- SHELL starts programs, even in Preview, **but it alters the command text**. Typing `echo %cmdcmdline%` in the window it opened showed `cmd.exe  / c copy  / y nul C:\Users\ITPSTU1\DOWNLO1\...`: spaces had been put around every `/` and every `~` was gone. So 8.3 short names (`DOWNLO~1`) and switches (`/c`, `--flag`) break. So the command uses plain paths and plain words only, and the installer refuses folder paths with other characters.
- **SHELL starts `.exe` programs but did not start a `.bat` file.** The self-test's SHELL call returned, but `vd_launch.log`, which the `.bat` writes to first, got no entry. Since v1.6, SB3 starts `venv\Scripts\python.exe vd_align.py newsession` directly, and at the end `venv\Scripts\python.exe vd_align.py`.
- **Quoted text containing `- + * / =` is worked out as arithmetic** in an assignment: `&m = "a-b+c"` stops with "Undefined variable: A". None of the routines, and no aligned file, puts those characters inside quoted text except in MSGBOX, PRINT and INPUT prompts, which your self-test showed are fine. A test enforces this.

- **`PAUSE` under 1 second stops the file** in Move mode until you continue it by hand, although the handbook allows decimals. Preview doesn't wait, so the self-test couldn't show this. Every `PAUSE` in the routines is now a whole number of seconds; a test enforces it.
- **`%(6)`/`%(7)` ("Base Coord") don't follow the tool.** They hold the table position of the current X/Y zero. Touch positions therefore come from `%(1)`/`%(2)`, at 0.001 in resolution. The aligned files' zero check compares `%(6)`/`%(7)` with their values at probing. That they change when X/Y is re-zeroed is inferred, not yet confirmed: run the self-test, re-zero X/Y elsewhere, run it again and compare the SV6/SV7 lines.

**Handled since:**
- **Positions are rounded.** SB3 rounds `%(1)`/`%(2)` to 0.001 in. All maths now uses the full-precision table-base values `%(6)`/`%(7)` plus the constant offset.
- **The date contains commas** ("Monday, September 28, 2026"). The data file already handled that.

### Without the machine (SB3 in Preview mode, nothing connected)

**Offline A: self-test (2 min).**
1. `FP` → `vd_selftest.sbp` in **Preview**. It contains no motion commands.
2. Click the buttons it asks for, type `1.5, -2, 0.6` when asked, then send me the `vd_selftest_result.txt` it opens.

It records the system variables the routine and the aligned files depend on:
- `%(22)` in Preview
- `%(61)`, `%(62)`, `%(66)`, `%(67)` (the FP offset/scale checks)
- the date and time formats
- how SB3 writes numbers to the data file

It also checks:
- 6-deep subroutine nesting
- arming and disarming an input handler with variables
- message-box answers
- INPUT with three values
- starting the script with SHELL exactly as `vd_probe.sbp` does. Before running, delete `vd_launch.log` and `vd_state.json` from the VirtualDowel folder if they exist. Afterwards, both should be back: `vd_state.json` means Python ran, and `vd_launch.log` shows how it was started.

**If SB3 stops on a line, send me the line number and the message.**

**Offline B: preview guard (30 s).** Run `vd_probe.sbp` with `&vd_sim = 0` in Preview. It should stop right away with "ShopBot is in PREVIEW mode" and do nothing else.

**Offline C: Python on the PC (1 min, optional).** In the VirtualDowel folder, run `py -3 -m unittest discover -s tests`. Expect `OK`.

**Offline D: Stage 0 below.** It runs entirely in Preview.

### With the machine

**Stage 0: the v2.0 window, dry run, no motion (10 min).** New in v2.0 and not yet seen on your machine: the custom cut, `FP` from inside it, and `pythonw.exe` (no console window).
1. In **Preview**, type `C80`. The window should open in front of ShopBot.
2. Add a real side-B file, enter its holes (and the stock box if asked), tick **Dry run**, click *Check*, then **Start probing**.
3. ShopBot should go straight on to the probing run. When asked, type the probed positions as `X, Y, diameter`: move both holes together, e.g. `4.30, 2.85, 0.6` and `11.3197, 2.9113, 0.8` for holes at (4.2, 2.8) and (11.22, 2.8), a 0.5° turn.
4. The window should reopen at Align with PASS and a `_VDSIM.sbp`, which only runs in Preview.
5. Type `C80` again and *Close* the window: nothing should run.

If any step doesn't happen, the troubleshooting table has the fallback; send me `vd_launch.log`. **If SB3 reports an error on any line, send me the line number and the message.**

**Stage 1: your real files (5 min).**
1. Using the Stage 0 data, align two or three of your real side-B jobs.
2. Look at the rotated toolpath of each `_VDSIM.sbp` in Preview.

Anything the script refuses, it tells you why. Send me those lines.

**Stage 2: probe basics (15 min, nothing is cut).**
1. Run the finger test. If it times out even though the input light changes, set `&vd_test = 0` and tell me.
2. Cut two test holes (0.500 and 0.625 in) in scrap.
3. Probe with `&vd_passes = 10`. The report's `repeat` and `Probe size bias` describe your probe.
   - If you get "triggered without a solid contact" on good holes, tell me. It means the input doesn't stay deflected after a slow stop on your probe.
4. Repeat once with `&vd_spin180 = 1` to measure runout.

**Stage 3: air cut.**
1. After aligning, zero Z about 1 in **above** the board.
2. Run `_VD.sbp` and watch that the path follows the board's skew.

**Stage 4: real flip test on scrap.**
1. On side A, cut the holes plus a reference feature, e.g. a 0.25 in through-slot.
2. Flip the board with a deliberate skew of about 1–2°, probe, align, and cut the matching feature from side B.
3. The step you can measure where the two features meet is your real-world registration.

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| C80 does nothing, or "Invalid command" | The custom cut wasn't installed (see the installer output) or SB3 looks elsewhere for custom cuts. Use `FP` → `vd_start.sbp` instead; it does the same. |
| The window opens, but probing doesn't start after *Start probing* | ShopBot didn't run `FP` from inside the custom cut. Run `FP` → `vd_run.sbp` by hand, and tell me. |
| No window after probing | Double-click `vd_align.bat`. It uses the new probe data. `vd_launch.log` in the folder records every start and why it stopped; send it to me. |
| A command prompt opens and stays open during a ShopBot file | SHELL passed a changed command (see "Confirmed on your SB3"). Type `echo %cmdcmdline%`, send me what it prints, then type `exit`. |
| "probe reads DEFLECTED or unplugged" | Check the cable, then run the probe calibration to find the input and its state. |
| "No contact within the search distance" | `&vd_hole` is too small, or the probe isn't triggering. |
| "triggered … at the very end of the search" | Park closer to the hole centre, or raise `&vd_margin` a little. |
| "touched something while lowering" | Not over the hole, or the hole is shallower than `&vd_depth` plus the park height. |
| Aligned file stops with "file offset or X/Y proportion" | An offset or proportion is set in the FP fill-in sheet. Clear it: offset off, proportions 1. On your SB3, these read 0 and 1 when nothing is set. |
| "side-A numbers fit the probe data just as well" | Move the holes onto the flip axis, or add a third hole that is not in line with the other two. The override `{"allow_offaxis_ambiguity": 1}` only warns. |
| "bare CR" or "mixes CRLF and LF" | Re-save the job from VCarve, or in SbEdit or Notepad, with Windows line endings. |
| "layout looks the same after a half turn" | Use different hole sizes, or add `{"allow_symmetric_layout": 1}`. You then accept the risk of a wrong flip. |

Settings in `vd_settings.json`, placed next to the script, override the thresholds at the top of `vd_align.py`, e.g.:

```json
{ "spacing_fail": 0.03, "max_data_age_min": 240 }
```

**Command line:**

```
py -3 vd_align.py vd_probe_data.csv --job C:\jobs\sideB.sbp --holes "12,1,0.5;12,29,0.625" --nogui
py -3 vd_align.py vd_probe_data.csv --job C:\jobs\fusion_B.sbp --holes "0.5,-2.75,0.5;14.5,-2.75,0.625" --stock "0,-5.5,15,0" --nogui
```

---

## License

Copyright (c) 2026 Nikolai Kozak. All rights reserved.

This is proprietary software, not open source. No permission is granted to copy, modify, distribute or use it without the author's prior written permission. See `LICENSE.txt` for the full terms, including the disclaimer: the software writes machine-control files for CNC equipment, and you use it at your own risk.

ShopBot and OpenSBP are trademarks of ShopBot Tools, Inc. Fusion is a trademark of Autodesk, Inc. Virtual Dowel is not affiliated with or endorsed by either company.
