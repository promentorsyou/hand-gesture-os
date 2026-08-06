# hand-gesture-os

Control your computer with webcam hand gestures — no mouse, no keyboard.

Hand tracking runs in the browser (MediaPipe Hands via WebAssembly); gesture
recognition and OS automation run in Python. The browser already has a
permission-managed, cross-platform camera API, so this split avoids per-OS
webcam driver problems entirely while keeping OS control where it has to be.

> **Current status: Phase 1 of 6.** Hand tracking, gesture recognition,
> calibration, and the emergency stop work and are covered by 123 passing
> tests. Real OS control (moving your actual cursor, clicking, managing
> windows) is **written but unverified** — see [PHASES.md](PHASES.md) for
> exactly what has and has not been run.

---

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/promentorsyou/hand-gesture-os
cd hand-gesture-os
pip install -e ".[server]"
```

Then add your platform's automation extra (needed from Phase 2 onward):

```bash
pip install -e ".[windows]"   # or ".[macos]" / ".[linux]"
```

**Linux** also needs `xdotool` (and `wmctrl` for window management):
```bash
sudo apt install xdotool wmctrl
```
Wayland blocks synthetic input from ordinary applications — X11 is the
supported path.

**macOS** requires granting **Accessibility** and **Screen Recording**
permission to your terminal in System Settings → Privacy & Security.
Without them macOS silently ignores synthetic input.

---

## Run

```bash
handgesture serve
```

Open <http://127.0.0.1:8000/>, click **Start tracking**, and allow camera
access. You should see your hand skeleton drawn over the video feed with the
recognised gesture and its confidence.

Other commands:

```bash
handgesture doctor             # report what your machine actually supports
handgesture simulate           # run the pipeline on synthetic gestures, no camera
handgesture serve --simulate   # UI + pipeline with OS control disabled
```

`--simulate` is the safe way to try gestures: everything runs, but the null
adapter records actions instead of performing them.

---

## Gestures

| Gesture | Action |
|---------|--------|
| Point (index finger) | Move cursor |
| Pinch (thumb + index) | Click |
| Pinch twice quickly | Double-click |
| Pinch and hold | Click and hold |
| Pinch while moving | Drag and drop |
| Two fingers (index + middle) | Right-click |
| Two fingers moving vertically | Scroll |
| Swipe left / right | Back / forward, switch app or tab |
| Swipe up | Multitasking view |
| Swipe down | Minimize |
| Open palm | Pause interaction |
| Closed fist | Grab / select |
| Rotate wrist | Volume, or rotate object |
| Two hands apart / together | Zoom in / out |
| Frame with both hands | Screenshot |
| Palm forward, 2 seconds | Home |
| **Cross both hands** | **Emergency stop** |

All mappings live in [`gestures.yaml`](src/handgesture/configuration/defaults/gestures.yaml) and are
editable.

**Keyboard fallbacks** (development and emergencies only): `Esc` engages the
emergency stop, `Space` releases it.

---

## Interaction modes

Gestures are filtered by the active mode, which is the main defence against
accidental actions — a fist means "grab a window" in window mode and nothing
at all in typing mode.

`navigation` · `typing` · `media` · `window` · `file` · `browser` ·
`precision` · `paused`

The active mode is always shown in the UI.

---

## Reliability

Gesture input is unusable if it fires when you did not mean it, so several
layers sit between "a pose was seen" and "a command ran":

- **One Euro filtering** — smooths hard when your hand is still (killing
  jitter) and barely at all when it moves fast (killing lag)
- **Hold requirement** — a gesture must persist for both a minimum number of
  frames *and* a minimum wall-clock time, so a fast camera cannot fire
  instantly
- **Confidence floor** — low-confidence frames are ignored entirely; they
  neither confirm a gesture nor break one in progress
- **Weakest-frame scoring** — a single strong frame cannot rescue a shaky hold
- **Cooldowns** — after a gesture ends, the same one is locked out briefly
- **Dropout grace** — one dropped tracking frame does not cancel a
  deliberate hold
- **Hand-loss protection** — losing tracking mid-gesture releases it cleanly
  and (by default) trips the emergency stop, so a drag is never dropped
  somewhere random

Tune all of it in a `gestures.yaml` and pass it with
`handgesture serve --config path/to/gestures.yaml`, or start from a shipped
profile: `handgesture serve --profile high_stability` if you are getting
misfires. With no `--config`, the server looks for `./handgesture.yaml`,
then `./config/gestures.yaml`, then `~/.config/handgesture/gestures.yaml`,
and falls back to the copy shipped inside the package.

Problems in a config file are printed at startup rather than swallowed: an
unknown key, a wrong type, or an out-of-range value is reported and the
default kept.

---

## Safety

**Emergency stop.** Cross both hands, press `Esc`, or click the button. All
gesture input stops until you explicitly release it. It is deliberately not
releasable by a gesture — a kill switch you can trip by accident is not a
kill switch.

**Confirmation gate.** Destructive operations never execute straight from a
gesture. They raise a confirmation panel needing an explicit hold:

- *Critical* (long hold): delete files, permanent moves, purchases, install
  software, run terminal commands, enter passwords, shut down, restart
- *Confirm* (short hold): send messages, submit forms, close unsaved work

Only one confirmation can be outstanding at a time, and stale ones expire.

This project does **not** bypass OS permissions, browser security, or
application restrictions.

---

## Calibration

Hands differ in size, people sit at different distances, and everyone pinches
with a different comfortable gap. Click **Calibrate** and follow the five
prompts (open hand, fist, pinch closed, pinch open, reach the corners).

The wizard derives your personal pinch threshold from your measured closed
and open extremes — placed nearer the closed end, because a missed pinch is a
much smaller annoyance than a phantom click.

Partial calibration still produces a usable profile.

---

## Architecture

```
Browser                          Python
-------                          ------
webcam -> MediaPipe Hands
              |
         landmarks --WebSocket--> frame parsing
                                       |
                                  recognition   (scale-invariant features)
                                       |
                                  debouncing    (did they mean it?)
                                       |
                                  mode filter
                                       |
                                  safety gate   (emergency stop, confirms)
                                       |
                                  OS adapter    (windows / macos / linux / null)
```

Every platform-specific action goes through the `OSAdapter` interface.
Nothing above that layer imports `pyautogui`, `win32api`, `Quartz`, or
`Xlib`, which is what lets the whole pipeline be tested headlessly against
the recording `NullAdapter`.

---

## Mobile companion

A phone can pair with a running session to show status and act as a second
screen — most usefully, an emergency stop you can reach without making a
gesture the system may be misreading.

On the desktop page press **Pair a phone**. On the phone, open
`http://<your-machine>:8000/companion` and enter the six digits.

Codes last two minutes, work once, and are rate-limited — after a handful
of wrong guesses pairing locks out entirely. A redeemed code becomes a
long random token bound to that one session; **Unpair all** on the desktop
revokes it immediately, and a connected phone is kicked on its next
message.

Two things a companion deliberately **cannot** do:

- **Confirm a destructive operation.** Confirmation requires a visible
  gesture at the camera; a phone tap is not one. The phone can *cancel* a
  pending confirmation, which is always the safe direction.
- **Choose the file browser's sandbox root.**

> **Pairing authenticates the link. It does not encrypt it.** On anything
> but a trusted network, put the server behind TLS. This has not been
> tested over a real network — see `PHASES.md`.

## Testing

```bash
pip install -e ".[server,dev]"
pytest
```

402 tests, no camera or display required. Hand poses are generated from a
reference geometry, so recognition, debouncing, safety, cursor mapping, the
spatial workspace, every application, and the WebSocket protocol are all exercised
deterministically.

To capture a real session as a regression fixture, use `SessionRecorder` to
save landmarks to JSON and replay them with `RecordedSource`.

---

## Troubleshooting

**"Model failed to load"** — MediaPipe loads from jsDelivr; check your
network or firewall.

**Camera blocked / `NotAllowedError`** — check the browser's site permission
*and* your OS-level camera permission. If the page is served over anything
other than `localhost` you also need HTTPS; browsers refuse camera access on
insecure origins. A site-wide `Permissions-Policy: camera=()` header will
also block it regardless of user permission.

**Gestures fire when I didn't mean them** — run calibration, then raise
`debounce.hold_frames` and `recognition.min_confidence` in
your own `gestures.yaml`, or start from the `high_stability` profile.

**Gestures don't trigger at all** — lower `recognition.min_confidence`, check
the confidence readout in the UI to see what score your pose is actually
getting, and make sure the tracking indicator is green.

**Cursor is jittery** — lower `cursor.smoothing_min_cutoff` for heavier
smoothing. **Cursor lags** — raise it, or raise `cursor.smoothing_beta`.

**Can't reach screen corners** — lower `cursor.margin`, or recalibrate and
let it measure your actual reach.

**`doctor` says "null adapter"** — the platform extra is not installed, or
you are in a headless session. No real OS control will happen.

---

## License

MIT
