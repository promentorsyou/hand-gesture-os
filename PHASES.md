# Build phases

Status tracker for the incremental build. Each phase is only marked complete
once it has been **run and verified**, and every claim below says how it was
verified — or states plainly that it was not.

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Hand tracking, recognition, calibration, emergency stop | **Complete** |
| 2 | Core UI control (click, drag, scroll, zoom) | **Complete** |
| 3 | Spatial interface (floating windows, multitasking) | **Complete** |
| 4 | Applications (files, browser, media, keyboard) | Not started |
| 5 | Mobile companion | Not started |
| 6 | Production hardening | Not started |

---

## Verification policy

This project is developed in a headless Linux container with **no webcam, no
display, and no desktop session**. That constrains what can honestly be
claimed:

- **Verifiable here:** the gesture pipeline (features, recognition,
  debouncing, safety, cursor mapping, calibration), the server and its
  WebSocket protocol, and the browser UI driven by synthetic landmarks.
- **NOT verifiable here:** real webcam capture, real MediaPipe hand
  detection on live video, and every platform adapter that moves a real
  cursor, presses real keys, or manages real windows.

Anything in the second category is marked **UNVERIFIED** and stays that way
until run on target hardware.

---

## Phase 1 — Hand tracking (complete)

### What works, and how it was verified

**Gesture recognition** — 123 automated tests pass (`pytest`). Poses are
generated from a reference hand geometry, so recognition is tested without a
camera:

- `point`, `pinch`, `peace`, `fist`, `open_palm`, `palm_forward`,
  `thumbs_up` each recognised at confidence 1.0
- features verified **scale-invariant** (same pose at 0.10/0.18/0.30 scale)
  and **translation-invariant**
- recognition holds across ±0.5 rad of hand rotation
- ambiguous half-curled poses correctly rejected

**Two-hand emergency stop** — crossed hands detected; uncrossed hands and
swapped-but-far-apart hands correctly rejected.

**Debouncing** — 16 tests covering the accidental-action guarantees: a single
frame never fires, frame count alone never fires without the time floor,
low-confidence frames are ignored, flicker between poses confirms nothing, a
brief dropout does not cancel a hold, and cooldown blocks immediate refire.

**Cursor mapping** — active region maps to the full screen, clamps at edges,
mirrors for a mirrored self-view, never leaves the screen over 200 random
inputs, and precision mode measurably reduces travel.

**One Euro filter** — jitter on a still hand suppressed to <0.012 spread
from ±0.02 input noise, while fast movement still tracks within 0.08.

**Calibration** — full 5-step wizard runs to completion; derived pinch
threshold verified to sit between the user's measured closed and open
extremes; partial calibration still yields a usable profile.

**Server + protocol** — live ASGI tests connect over a real WebSocket, run
frames through the pipeline, and get gesture state back. Malformed JSON and
truncated landmark lists are handled without dropping the connection.

**End-to-end, in a real browser** — Chromium loaded the served UI, its own
WebSocket reported `connected`, all 8 mode buttons rendered, and synthetic
landmarks pushed over the socket produced `point` (with cursor coordinates),
`fist`, `open_palm`, and crossed-hands → `emergencyStopped: true`.

**CLI** — `handgesture simulate` runs the whole pipeline on synthetic
gestures and prints 31 events ending in an emergency stop. `handgesture
doctor` reports platform capabilities.

### Explicitly NOT verified

- **Live webcam hand tracking.** MediaPipe Hands is wired into the UI and
  loads from CDN, but no camera exists here. The browser-side tracking path
  has never processed a real hand.
- **All three OS adapters** (`windows.py`, `macos.py`, `linux.py`). Written
  against documented APIs; never executed. Cursor movement, clicks, key
  presses, window management, screenshots, volume, and locking are all
  unverified. `get_adapter()` falls back to the null adapter here.
- **Gesture thresholds against real hands.** Every threshold is calibrated
  against synthetic geometry. Real hands vary; expect to run calibration and
  possibly adjust `config/gestures.yaml`.

### How to test Phase 1 yourself

```bash
git clone https://github.com/promentorsyou/hand-gesture-os
cd hand-gesture-os
pip install -e ".[server,dev]"

pytest                      # 123 tests, no camera needed
handgesture doctor          # what your machine supports
handgesture simulate        # pipeline on synthetic gestures
handgesture serve           # then open http://127.0.0.1:8000/
```

In the browser: click **Start tracking**, allow the camera, and you should
see your hand skeleton drawn over the video with the recognised gesture and
confidence in the top-right panel.

Try: point one finger (cursor follows), pinch, make a fist, open your palm,
and cross both hands (emergency stop — press **Space** or click **Resume**).
**Esc** is the keyboard emergency stop.

Please report which gestures misfire or fail to trigger — those thresholds
are the part most likely to need real-hand tuning.

### Files added in Phase 1

```
src/handgesture/
  types.py                      core data types
  pipeline.py                   end-to-end orchestration
  cli.py                        serve / simulate / doctor
  capture/base.py               landmark source interface
  capture/simulation.py         synthetic poses, replay, recording
  tracking/filters.py           One Euro filter
  gestures/features.py          scale-invariant pose features
  gestures/vocabulary.py        gesture + mode definitions
  gestures/recognizer.py        scoring-based recognition
  gestures/debounce.py          intent confirmation
  control/cursor.py             camera -> screen mapping
  control/safety.py             emergency stop, confirmation gate
  calibration/calibrator.py     per-user calibration
  osadapter/base.py             platform interface
  osadapter/null.py             recording no-op adapter
  osadapter/{windows,macos,linux}.py   UNVERIFIED
  server/app.py                 session + protocol
  server/asgi.py                FastAPI wiring
  server/static/index.html      live UI
config/gestures.yaml            gesture map
config/profiles/*.yaml          sample profiles
tests/                          123 tests
```

---

## Phase 2 — Core UI control (complete)

### What works, and how it was verified

**Motion gestures** (`gestures/motion.py`) — 22 tests. Swipes require four
independent conditions together: travel distance, speed, *straightness*, and
a dominant axis. Straightness is what stops ordinary hand repositioning from
firing a swipe; tests cover a wandering path, a slow sweep, and a diagonal
(ambiguous, ignored) all correctly producing nothing. Scroll, two-hand zoom,
and wrist rotation each have deadzones so a still hand emits nothing.

Verified scale-invariant in the meaningful sense: a sweep of constant
*hand-widths* fires at any apparent hand size. (A fixed screen-distance
sweep is deliberately **not** invariant — thresholds are in palm spans, so a
large hand near the camera must travel further on screen for the same
gesture.)

**Compound pointer intents** (`gestures/compound.py`) — 16 tests. A pinch is
ambiguous the moment it happens, so the decision is delayed: quick release +
nothing following → click; two pinches inside the double window →
double-click; held past the threshold → click-and-hold; held then moved →
drag. Tests specifically pin the classic bug where one double-click also
emits two singles.

**OS dispatch** (`control/actions.py`) — 17 tests. The single choke point
where gestures become real OS effects. Verified against the recording
`NullAdapter`: clicks, double-clicks, right-clicks, scroll, zoom chords,
browser navigation, media keys, and volume from wrist rotation all reach
the adapter with the right arguments. A drag presses and releases the button
exactly once each.

**Safety, re-verified at this layer** — the emergency stop blocks clicks,
motion, and operation requests; destructive operations still queue for
confirmation and reach no adapter until confirmed; the dispatch history is
bounded so a long session cannot grow it without limit.

**End-to-end** — 9 pipeline tests drive landmarks all the way to adapter
calls. A manual stack run (point → pinch → release → peace) produced 41
cursor moves and both a left and a right click.

### Bug found and fixed during this phase

**The emergency stop left the mouse button held down.** Two causes, both
real: the stop was engaged *before* the release was dispatched (so the
dispatcher blocked the very mouse-up that undoes it), and the compound
detector had no way to emit a terminating event without a real release
event. A stuck button after an emergency stop is precisely the failure the
stop exists to prevent. Fixed with an explicit `CompoundDetector.release()`
and by reordering the stop, with regression tests on both. The same fix
covers losing tracking mid-drag.

### Explicitly NOT verified

- **Every OS effect.** All dispatch tests run against `NullAdapter`, which
  records calls instead of performing them. That the *right call with the
  right arguments* is made is verified; that it moves your real cursor or
  scrolls your real window is **UNVERIFIED**.
- **Swipe/scroll thresholds against real hands.** Tuned against synthetic
  motion only. Expect to adjust `config/gestures.yaml`.
- **Zoom and multitask key chords** are best-guess per platform and have
  never been sent to a real desktop.

### How to test Phase 2 yourself

```bash
pip install -e ".[server,dev]"
pytest                      # 186 tests
handgesture serve --simulate   # safe: records actions, performs none
```

Then, with real OS control (install your platform extra first):

```bash
handgesture serve
```

- **Point** and move — the cursor should follow.
- **Pinch quickly** — one click (note the brief delay: it is waiting out the
  double-click window).
- **Pinch twice quickly** — a double-click, and *not* two singles.
- **Pinch and hold ~0.5s, then move** — a drag.
- **Two fingers up, move up/down** — scroll.
- **Two hands apart / together** — zoom.
- **Fast straight sweep** — swipe (back/forward in browser mode).
- **Cross both hands mid-drag** — the button must release, not stick.

Most useful feedback: whether swipes fire when you did not mean them, and
whether the click delay feels wrong.

---

## Phase 3 — Spatial interface (complete)

### What works, and how it was verified

**Windows and rectangles** (`spatial/window.py`) — 11 tests. Windows live in
the same normalised 0..1 space as everything else, so the whole layer is a
pure state machine with no display involved. Grab targets are disambiguated
deliberately: the resize corner overlaps the window body and wins, the title
bar moves, and the body does neither (it focuses only). Clamping is tested
against the failure that matters — a window dragged past the top edge would
have an unreachable title bar and could never be dragged back.

Maximise/restore round-trips exactly, including the classic bug where a
second maximise overwrites the saved rectangle.

**Workspace** (`spatial/workspace.py`) — 25 tests. Focus and z-order,
hit-testing the topmost window, minimised windows dropping out of hit tests
but staying in the app switcher, focus falling through to the next window on
close, app cycling with wraparound, multitask cards ordered most-recent
first, notifications (newest first, unread counts, bounded history,
suppressed when muted), and quick settings.

Grabs apply deltas against the rectangle captured at grab time rather than
accumulating per frame, so a hand that wanders out and back lands exactly
where it started — verified.

**Gesture wiring** (`spatial/controller.py`) — 14 tests. Fist grabs, spread
maximises, converge restores, swipe up opens the multitasking deck, swipe
down minimises, horizontal swipes switch apps, palm-forward toggles home,
peace toggles quick settings. A zoom below threshold does not flip window
state, and zoom is ignored entirely while a window is held.

**End to end** — one test drives *synthetic landmarks* through the real
pipeline and out the other side as a moved window, with no hand-built
state object anywhere in it.

**In a real browser** — Chromium loaded the served UI, opened three
cascading windows through the page's own controls, rendered them with the
correct titles, focus ring and resize handle (screenshot inspected), showed
the home shade with its five app cards, and — over the live WebSocket —
had a close of unsaved work refused with `needsConfirmation: true`.

### Bug found and fixed during this phase

**A fist could not grab anything.** The pipeline only moves the cursor for
pointing and pinching, deliberately, so a held fist does not drag the
pointer around. But a fist *is* the grab gesture, so in window mode the
spatial layer had no position to work from and every grab silently did
nothing. Fixed by making the cursor follow a fist in `Mode.WINDOW` only;
a test pins that the exception does not leak back into navigation mode.

### Explicitly NOT verified

- **Real OS windows.** The workspace draws its own windows over the camera
  view. It does not move host-OS windows; `OSAdapter`'s window methods
  remain **UNVERIFIED**.
- **Gesture ergonomics.** Whether a fist is comfortable to hold while
  moving a window, and whether the spread/converge threshold feels right,
  can only be judged with real hands.

### How to test Phase 3 yourself

```bash
pip install -e ".[server,dev]"
pytest                          # 250 tests
handgesture serve --simulate
```

Switch to **window** mode, press **Open app** a few times, then:

- **Fist on a title bar**, move — the window follows; open the hand to drop.
- **Fist on the bottom-right corner** — resize.
- **Two hands apart / together** — maximise / restore.
- **Swipe up** — multitasking deck. **Swipe down** — minimise.
- **Swipe left / right** — switch apps.
- **Palm forward** — home. **Peace** — quick settings.
- **Cross both hands mid-grab** — the window must be dropped, not stuck.

### Files added in Phase 3

```
src/handgesture/spatial/window.py       window model, rects, grab targets
src/handgesture/spatial/workspace.py    windows, focus, overlays, notifications
src/handgesture/spatial/controller.py   gesture -> workspace mapping
tests/test_spatial.py                   50 tests
```

---

## Phase 4 — Applications (next)

Planned: a file browser, web-browser controls, music and video players, an
image viewer, a settings app, and the hand-controlled virtual keyboard.
These become the contents of the windows Phase 3 can already open, move,
and switch between.
