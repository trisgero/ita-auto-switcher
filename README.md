# Verse Auto-switcher — Filipino Zoom → vMix

Replaces the OBS Pixel Matcher. Watches the monitor where the Filipino Zoom
feed runs full-screen, recognizes which verse layout is on screen, and keeps
vMix aligned through its HTTP API.

| detected layout | vMix input |
|---|---|
| `FULL` | 3 |
| `LEFT` | 8 |
| `BIG_LEFT` | 49 — `3 BRO VERSE L` |
| `DUAL`, `TRIPLE` | 2 (recognized and logged, no dedicated template yet) |
| `NONE` — everything else | 2 |

`RIGHT → 9` is wired up but disabled until there's a screenshot to calibrate
it. Wiring DUAL/TRIPLE up once they have an input is **a number in
`config.json`**, no code involved.

> **Note on input 49**: it has no keyboard shortcut in the current template,
> so it was never triggered by automation before now. The first time, try it
> in `--dry-run` and check in the log that `Merge -> input 49` shows up when,
> and only when, the source shows the 3-camera layout.

> **Provisional calibration.** Thresholds are measured on screenshots of the
> **Italian output**. The project's premise is that both sides share the same
> geometry — that's the whole reason the automation exists — but it should be
> confirmed on **Filipino** frames. When you have them: put them in
> `__screenshots` with the layout's name (`full.png`, `left.png`, …) and run
> `test_detection.py`. If they pass, nothing needs touching; if not, the
> numbers to fix are in each probe's `_note` in `config.json`.

## Why it doesn't get stuck anymore

The OBS Pixel Matcher is **open-loop**: it sends a keystroke when the match
fires and then stops caring. If that keystroke is lost, or the exit condition
never fires, nobody notices — that's how you end up with "the full verse
stuck on".

This is **closed-loop**. On every tick (~8/s):

1. captures a frame of the Zoom monitor;
2. classifies it as `NONE` / `FULL` / `LEFT` / `DUAL` / ...;
3. reads vMix's **actual** state (`<active>` from the API);
4. if it doesn't match the desired state, sends `Merge`.

If a command is dropped, the next tick resends it. `test_integration.py`
reproduces exactly this: it starts with vMix stuck on input 3, drops 3
commands in a row, and verifies it still recovers to input 2.

## How it recognizes layouts

It doesn't count pixels of a color and doesn't read the text. It looks at
**where the dark panel** of the card is: an almost-monochrome navy blue
(H=111, S=188, V=67-80) on a light blue background (V>200). Six probes
separate all the layouts:

| probe | where | full | left | big_left | dual | triple | camera |
|---|---|---|---|---|---|---|---|
| `card_left` | left card body | 0.69 | 0.84 | 0.83 | 0.83 | 0.52 | **0.02** |
| `col_right` | card's right edge | 0.62 | **0.13** | 0.99 | 1.00 | 0.85 | 0.03 |
| `far_right` | right-hand band | 0.75 | 0.02 | 0.12 | **0.99** | 0.51 | 0.10 |
| `low_left` | bottom-left | **0.96** | 0.05 | 0.37 | 0.00 | 0.01 | 0.04 |
| `low_right` | bottom-right | **1.00** | 0.09 | 0.05 | 0.00 | 0.00 | 0.00 |
| `center_low` | bottom-center card | 0.94 | 0.00 | 0.00 | 0.00 | **0.77** | 0.00 |

The rules become readable:

- **FULL** = the card reaches the bottom on both sides
- **TRIPLE** = there's a bottom-center card, but not the side ones
- **DUAL** = a card on the left and a full right-hand band, neither reaches the bottom
- **BIG_LEFT** = the card extends past `col_right` but not to `far_right` (3 narrow cameras)
- **LEFT** = the card ends before `col_right` (2 wide cameras)
- **otherwise NONE** → input 2

Across the 5 measured layouts **every rule is mutually exclusive**: each one
matches only one rule, so evaluation order is not load-bearing. This is
verified by a test, so it stays true once you add RIGHT and QUAD too.

The difference between `LEFT` and `BIG_LEFT` is entirely in the card's right
edge (x≈0.62 vs x≈0.70): margin 0.13 vs 0.99. If a third variant with the
edge somewhere in between ever shows up, that's where a new probe needs to
go.

Four details that remove false positives:

- The metric uses **hue**, not just brightness. A camera in dim light has the
  same brightness values as the card but not its color: `navy` rejects it at
  any gain, a brightness-only threshold doesn't. (The first round of testing
  actually failed on this exact case — see point 2 of `test_detection.py`.)
- **High saturation** (`s_min=150`). The card is very saturated (S≈171-219);
  a dark blue jacket in a camera shot can reach S≈100-110 — dark and
  "bluish", but less vivid. With a low `s_min` this was enough to trigger
  `card_left` and misclassify NONE as BIG_LEFT: the real case is saved in
  `testdata/none_giacca_navy.png`. With `s_min=150` the jacket drops to
  ~0.02, while the hardest real layout (triple) stays at ~0.49.
- **Pure hue+saturation, no brightness-only tolerance.** An earlier version
  also counted bright, low-saturation pixels as "card" to tolerate long verse
  text overflowing a probe's box. That let a performer's white/cream outfit
  during a musical number get misread as a verse card (see
  `testdata/none_performance_bianco.png`) — a real false positive reported
  from the field. It turned out the plain hue+saturation thresholds already
  covered every known long-verse case on their own (margins ≥0.49 for
  genuine cards vs 0.00 for the performance shot), so the extra tolerance
  was removed rather than patched further.
- **Hysteresis** (a lower exit threshold than the entry one) and **debounce**
  (3 agreeing readings, ~375 ms) before accepting a change.

## Independent overlays: lower third, centered lower third, sign-language box

Separate from the verse-card state machine above: the lower third (blue bar
+ white panel banner, whatever text it holds), the centered lower third (the
same graphic family shown as a location caption over the camera panel, e.g.
"VILLAMOR, PASAY CITY") and the sign-language interpreter box (bottom-left)
are toggled independently through vMix's `OverlayInputN` API, in
`autoswitch/overlays.py` and reconciled every tick in `main.py` alongside the
verse-card program input, sharing one vMix state read per tick.

They're **not** part of the verse-card rules on purpose: production keeps
both on through many different segments — including performances that the
verse detector correctly reads as `NONE` (confirmed on real footage: both
overlays measured ON during the two false-positive performance frames in
`testdata/`). Trying to fold them into the same state machine would have
coupled two unrelated things.

The signal is much stronger than the verse-card probes, because the question
is binary presence/absence of a flat-color graphic against a busy
photographic image, not "which of several similar cards is this":

| probe | worst ON example | worst OFF/false example |
|---|---|---|
| `lis_box` (light-blue backdrop behind the interpreter) | 0.36 | 0.02 |
| `lower_third` (saturated blue + white panel, min of both) | 0.19 | 0.05 |
| `centered_lower_third` (same graphic, over the camera panel) | 0.28 | 0.17 |

`centered_lower_third` has only **one real ON example**
(`testdata_overlays/centered-lowerthird.png`) so its margin is much tighter
than the other two - see its `_note` in `config.json`.

**Only one real OFF example exists so far** (`testdata_overlays/houston_off.png`).
The margins above are wide enough to trust provisionally, but — same lesson
as everywhere else in this project — get more real OFF cases (different
events, different lighting) before trusting them the way the verse-card
thresholds are trusted after a dozen real examples. Add new cases straight
into `testdata_overlays/` and a case in `test_overlays.py`.

Toggling is safe with a plain toggle command (rather than an explicit on/off
call) only because the reconciliation loop always re-reads vMix's real
overlay state first and sends exactly one toggle when it disagrees with the
desired state — never a blind command chain. The one real bug this surfaced
during testing: on startup, each overlay's debounced state defaulted to
`"OFF"` regardless of what vMix actually showed, which briefly toggled an
already-correct ON overlay off and back on again. Fixed by seeding the
debouncer from vMix's real state on the first tick instead of guessing (see
`Switcher._seed_overlay_debouncers` and scenario 10 in `test_integration.py`).

## Prerequisites

In vMix: **Settings → Web Controller enabled**, port 8088. That's the
service that exposes `http://127.0.0.1:8088/api`.

The Filipino Zoom feed full-screen on the 1920x1080 monitor. On the original
development machine monitor `2` was the only 1920x1080 one — **verify on your
own machine which monitor is actually the Filipino Zoom one** before relying
on the default in `config.json`: during development, capturing the wrong
monitor once surfaced the Italian output instead (verses in `ITALIAN
RIVEDUTA LUZZI`). If the automation watches its own output instead of the
source, it feeds on itself. Check with `calibrate.py --shot test.png`, or use
the GUI's "Change monitor..." picker.

---

# How to test

> **With the GUI**: `.\gui.bat` (or the built exe), then "Verify all" does
> the same check as `test.bat` below but inside the window, no terminal
> needed. The sections below remain useful for command-line development and
> for understanding what happens underneath — the GUI calls them, it doesn't
> replace them.

## Test 1 — offline, no vMix and no live feed (30 seconds)

> **Shell syntax.** The `.bat` files work anywhere: double-click, or
> `.\name.bat` from PowerShell. If you'd rather run Python by hand, in
> **PowerShell** you need `.\` before the path and `;` instead of `&&` —
> `cd <folder>; .\.venv\Scripts\python.exe test_detection.py`. The form
> `cd /d ... && ...` is `cmd` syntax and gives a parse error in PowerShell 5.1.

Nothing needs to be running. Checks recognition on the real screenshots in
this repo, the "no verse" case, recovery from FULL, dropped commands, and
hands-off. Double-click **`test.bat`**, or from the repo root:

```bash
.\test.bat
```

Runs both suites: both need to end with `ALL OK`. Re-run them every time you
touch the thresholds in `config.json`: they're the safety net.

**Grow the suite.** Every time the system gets something wrong live, take the
frame from `logs/frames/`, rename it `<STATE>_<note>.png` (e.g.
`none_dark_camera.png`) and put it in `testdata/`: `test_detection.py` picks
it up on its own and from then on that case can never regress. It already has
several such cases in it, captured from the field with different graphics,
backgrounds, or content than the calibration set (a jacket mistaken for the
card, a musical performance, a longer verse) — proof the probes aren't
stitched to a single template.

To see the numbers probe by probe on a single image:

```bash
.\calib.bat --image __screenshots\full.png --show
```

## Test 2 — cold, with real Zoom but without touching vMix

Put the Filipino Zoom feed full-screen on the configured monitor. Then:

```bash
.\calib.bat
```

Opens a window with the ROIs drawn over the captured frame: green = probe
on, red = off, and the recognized state at the top. Have the layout change on
the other end (or scroll through recorded verses) and check that the text at
the top follows: `FULL`, `LEFT`, `NONE`.

**What to look at**: the numbers should stay far from the thresholds. If, say,
`left_low` oscillates between 0.60 and 0.75 with `on=0.70`, the ROI is placed
badly — it's not a threshold problem. `q` to quit, `s` to save the current
frame.

If the ROIs turn out to be misaligned (Zoom with black bars, or a toolbar
always visible), redraw them with the mouse:

```bash
.\calib.bat --rois
```

## Test 3 — hot, with vMix, but commanding nothing

vMix open with the preset, Zoom full-screen. Then:

```bash
.\dryrun.bat
```

Reads, decides, and logs **without sending a single command**. In the
console you'll see lines like:

```
STATE: NONE -> FULL   (left_mid=0.691  right_mid=0.740  left_low=1.000  right_low=1.000)
Merge -> input 3 (FULL)   [program was 2]
```

Have the Filipino operator run through a full cycle — full, cleared, left,
cleared — and check that every `Merge -> input N` line is what you'd have
pressed by hand. This is the test to do **before** using it live.

If it reads vMix's state correctly, no connection error shows up at startup.
If `vMix unreachable` appears, the Web Controller is off.

## Test 4 — live, with a recovery test

Once the dry-run has convinced you:

```bash
.\run.bat
```

or double-click `run.bat`.

The one test worth doing once, live: while the source shows the **full
verse**, manually switch vMix's input to 2. Within a second the automation
must bring it back to 3. That's the closed-loop behavior — exactly what's
missing in an open-loop pixel matcher.

Then the hands-off counter-test: manually switch to an **unmanaged** input
(e.g. 41). The automation must stop and leave it there, logging
`HANDS-OFF`.

## If something goes wrong

`logs/autoswitch.log` has every transition with the probe values, every
command, and every correction. `logs/frames/` has the **exact frame** of
every state change: when it gets something wrong, look at the real image and
feed it to `calibrate.py --image` instead of guessing.

The warning `input N requested 5 times but vMix stays on M` means vMix
receives the command but doesn't change: almost always a transition in
progress or an input stuck in Paused state.

---

## Wiring BIG_LEFT, DUAL, TRIPLE to vMix inputs

They're **already recognized**. All that's missing is telling it which input
to send: in `config.json`, `states`, change the number and add it to
`managed_inputs`. Candidates read from the preset: `BIG_LEFT` → **49**
(`3 BRO VERSE L`), `DUAL` → 29, `TRIPLE` → 30. No code changes.

## Adding RIGHT and QUAD

**RIGHT** is already written in `detector.rules` with `"enabled": false`.
Save the screenshot as `__screenshots\right.png`, run `test_detection.py` to
see the numbers, then set `"enabled": true`.

**QUAD**: add the probe that distinguishes it and the matching rule.
`test_detection.py` checks on its own that it doesn't conflict with the
others.

## Files

| file | role |
|---|---|
| `config.json` | ROIs, thresholds, state→input map, timing. Everything gets touched here |
| `autoswitch/capture.py` | screen capture (mss) and resizing |
| `autoswitch/detect.py` | ROI metrics, hysteresis, rules, debounce |
| `autoswitch/vmix.py` | vMix API client (state reading + commands) |
| `autoswitch/main.py` | reconciliation loop for both the program input and the overlays (CLI, for development) |
| `autoswitch/overlays.py` | independent lower-third / sign-language-box on/off detectors |
| `autoswitch/paths.py` | folder resolution, correct from a packaged .exe too |
| `autoswitch/wizard.py` | guided monitor selection, vMix check |
| `autoswitch/regression.py` | case discovery, test execution, threshold computation — used by both `test_detection.py` and the GUI |
| `autoswitch/gui.py` | the GUI: templates, screenshots, inputs, verify, recalibrate |
| `app.py` | entry point: opens the GUI (or the old console menu with `--console`) |
| `calibrate.py` | manual ROI drawing, live viewer with the probes drawn on top |
| `test_detection.py` | verse-card regression tests (command line) |
| `test_overlays.py` | lower-third / sign-language-box regression tests |
| `test_integration.py` | full loop (program input + overlays) against a fake vMix |
| `test.bat` | runs all three suites |
| `gui.bat` | opens the GUI from source |
| `dryrun.bat` / `run.bat` | dry-run / live from the command line (no GUI) |
| `calib.bat` | low-level calibration viewer (accepts `calibrate.py`'s flags) |
| `build.bat` | rebuilds `dist/AutoSwitcher.exe` |

---

# The GUI: templates, screenshots, inputs

`app.py` (and therefore the built `.exe`) opens a window with:

- a **template list** on the left — one per recognizable state (`FULL`,
  `LEFT`, `BIG_LEFT`, `DUAL`, `TRIPLE`, `RIGHT`, plus any new ones);
- for the selected template: its associated **vMix input**, its **primary
  screenshot** (the calibration one), and a list of additional
  **verification screenshots**;
- **Verify all** — re-runs the detector on every saved screenshot and shows
  which ones get recognized correctly, in the window's log. It's the same
  engine as `test_detection.py`, callable with no terminal;
- **Recalibrate thresholds...** — for every probe, measures the value across
  all tagged screenshots and recomputes `on`/`off` with a margin, exactly the
  procedure done by hand during the initial calibration (minimum of the
  cases that must turn it on, maximum of those that must leave it off,
  threshold at the midpoint). If a probe no longer has enough margin, it says
  so clearly instead of writing unreliable thresholds — at that point the ROI
  needs to be redrawn, not just the number, and that's a job for whoever set
  up the system;
- **TEST / GO LIVE / STOP** — the same reconciliation loop as always, now
  with the log inside the window instead of the console;
- **Change monitor...** — a picture of every connected monitor, click the
  right one.

**An honest limit**: the GUI manages *which* screenshots represent a
template and *where* to send it (the vMix input). It doesn't design on its
own *how* to recognize a brand new template — that is, it doesn't draw new
ROIs. A template created with "New..." stays invisible to the automation
until a rule in the engine recognizes it (the red note in the GUI flags
this). Adding a rule for a layout never seen before is analysis work
(measuring where that layout differs from the others) more than
configuration.

---

# Distributing to other sites

If they use the **same vMix bundle** (same `vmixZip`, same shortcut
template), the `config.json` already tuned in this project works identically
on their PCs — no per-site calibration needed. If their graphics change (new
year, new background), the GUI itself has the tools to update the
screenshots and recalibrate the thresholds, without touching any file by
hand. The one thing that always differs from PC to PC is **which monitor is
the Zoom one**, and there's a visual picker for that.

No web app needed: the tool has to run **on the PC where vMix runs**,
because it captures the local screen and talks to `127.0.0.1:8088` — no
website can do that remotely for a PC that isn't its own. The distribution is
a standalone Windows executable (GUI, no black console window behind it), no
Python to install.

## Building the executable

```bash
.\build.bat
```

Produces `dist\AutoSwitcher.exe` (~75-80 MB, self-contained: engine, GUI, and
the reference screenshots/testdata currently in this repo are all bundled
in). It's built with `--windowed` (no console) via PyInstaller — see
`build.bat` for the exact flags.

The built exe is **not** committed to this repo (too large, and it's a
generated artifact) — distribute it separately, e.g. as a zipped folder
containing `AutoSwitcher.exe` + a copy of the plain-language instructions, or
as a GitHub Release asset.

On first double-click on a new PC, the exe creates, next to itself:
`config.json` (copied from the one bundled in), the `__screenshots/` and
`testdata/` folders (also copied from the bundle), and `logs/`. Everything
stays in the exe's own folder, portable, no installation. Rebuilding the exe
does **not** touch files already created on other PCs — those stay as they
were (monitor choice, templates); that's intentional, they're per-site
customizations.
