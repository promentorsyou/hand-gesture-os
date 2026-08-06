"""The hand-controlled virtual keyboard.

Typing is the hardest thing to do with hands in the air, so this offers two
input methods and takes accidental keypresses seriously:

* **Dwell typing** — hover a key and hold still. A key only fires after the
  hand has been *inside the same key* for the dwell time, which means
  travelling across the keyboard to reach a key cannot type the keys you
  pass over. This is what makes one-handed typing possible at all.
* **Pinch typing** — pinch to commit the key under the cursor. Faster, and
  it is what most people will use once they are confident.

Two further defences against noise:

* a **cooldown** after each press, so a trembling hand cannot machine-gun
  the same key
* dwell progress **resets when the hand leaves the key**, so hesitating over
  a key and moving on types nothing

Keys are laid out in normalised 0..1 coordinates *within the keyboard
panel*, not the screen, so the panel can be drawn anywhere at any size and
hit-testing still works.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..types import Point
from .base import ActionResult, App, OSRequest, register


class Layout(str, Enum):
    LETTERS = "letters"
    NUMBERS = "numbers"
    SYMBOLS = "symbols"


#: Special key labels that are commands rather than characters.
SHIFT = "⇧"
BACKSPACE = "⌫"
ENTER = "⏎"
SPACE = "␣"
LAYOUT = "?123"
LETTERS_KEY = "ABC"

_ROWS: dict[Layout, tuple[tuple[str, ...], ...]] = {
    Layout.LETTERS: (
        tuple("qwertyuiop"),
        tuple("asdfghjkl"),
        (SHIFT, *"zxcvbnm", BACKSPACE),
        (LAYOUT, ",", SPACE, ".", ENTER),
    ),
    Layout.NUMBERS: (
        tuple("1234567890"),
        tuple("-/:;()$&@"),
        ("#+=", ".", ",", "?", "!", "'", '"', BACKSPACE),
        (LETTERS_KEY, SPACE, ENTER),
    ),
    Layout.SYMBOLS: (
        tuple("[]{}#%^*+="),
        tuple("_\\|~<>€£¥"),
        ("?123", ".", ",", "?", "!", "'", BACKSPACE),
        (LETTERS_KEY, SPACE, ENTER),
    ),
}

#: Keys that are wider than one unit, in units.
_WIDE = {SPACE: 5.0, SHIFT: 1.6, BACKSPACE: 1.6, ENTER: 1.8, LAYOUT: 1.6,
         LETTERS_KEY: 1.6, "#+=": 1.6, "?123": 1.6}


@dataclass(frozen=True, slots=True)
class Key:
    label: str
    #: Rectangle in normalised keyboard-panel space.
    x: float
    y: float
    width: float
    height: float

    def contains(self, point: Point) -> bool:
        return (
            self.x <= point.x <= self.x + self.width
            and self.y <= point.y <= self.y + self.height
        )


@dataclass(slots=True)
class KeyboardConfig:
    #: Seconds a hand must rest on a key before dwell typing fires it.
    dwell_seconds: float = 0.7
    #: Lockout after any key press.
    cooldown_seconds: float = 0.25
    #: Gap between keys, as a fraction of a key's width.
    gutter: float = 0.012
    #: Whether dwell typing is on. Off means pinch-only.
    dwell_enabled: bool = True


@dataclass(frozen=True, slots=True)
class KeyPress:
    label: str
    #: The character typed, empty for command keys.
    char: str = ""
    #: ``dwell`` or ``pinch``.
    source: str = "dwell"


@register
class KeyboardApp(App):
    """A virtual keyboard driven by a hand position."""

    name = "keyboard"
    title = "Keyboard"

    def __init__(self, config: KeyboardConfig | None = None) -> None:
        self.config = config or KeyboardConfig()
        self.layout = Layout.LETTERS
        self.shift = False
        self.caps_lock = False
        self.buffer = ""
        #: Bumped whenever the drawn key labels change. The layout is ~3KB
        #: of JSON and changes a handful of times a session, so it is fetched
        #: on demand rather than sent with every frame.
        self.layout_revision = 0
        self._hover: str | None = None
        self._hover_since: float = 0.0
        self._last_press: float = -999.0
        self._last_shift_tap: float = -999.0

    # --- Layout ---------------------------------------------------------

    def keys(self) -> list[Key]:
        """Every key in the current layout, in normalised panel space."""
        rows = _ROWS[self.layout]
        gutter = self.config.gutter
        row_height = 1.0 / len(rows)
        out: list[Key] = []

        for row_index, row in enumerate(rows):
            units = sum(_WIDE.get(label, 1.0) for label in row)
            unit_width = 1.0 / units
            x = 0.0
            for label in row:
                width = _WIDE.get(label, 1.0) * unit_width
                out.append(
                    Key(
                        label=self._display(label),
                        x=x + gutter / 2,
                        y=row_index * row_height + gutter / 2,
                        width=max(1e-6, width - gutter),
                        height=max(1e-6, row_height - gutter),
                    )
                )
                x += width
        return out

    def _display(self, label: str) -> str:
        """Apply shift/caps to a character label."""
        if len(label) == 1 and label.isalpha():
            return label.upper() if (self.shift or self.caps_lock) else label
        return label

    def key_at(self, point: Point) -> Key | None:
        for key in self.keys():
            if key.contains(point):
                return key
        return None

    # --- Input ----------------------------------------------------------

    def update(self, point: Point | None, now: float, *, pinching: bool = False):
        """Feed one frame of hand position. Returns a :class:`KeyPress` or ``None``.

        ``point`` is in normalised keyboard-panel space; ``None`` means the
        hand is not over the keyboard at all.
        """
        if point is None:
            self._hover = None
            return None

        key = self.key_at(point)
        if key is None:
            self._hover = None
            return None

        if key.label != self._hover:
            # Moved to a different key: dwell starts over. Without this,
            # travelling across the keyboard would type everything en route.
            self._hover = key.label
            self._hover_since = now

        if now - self._last_press < self.config.cooldown_seconds:
            return None

        if pinching:
            return self._commit(key.label, now, "pinch")

        if (
            self.config.dwell_enabled
            and now - self._hover_since >= self.config.dwell_seconds
        ):
            return self._commit(key.label, now, "dwell")

        return None

    def progress_at(self, now: float) -> float:
        """0..1 fill for the dwell indicator under the hovered key."""
        if self._hover is None or not self.config.dwell_enabled:
            return 0.0
        elapsed = now - self._hover_since
        return max(0.0, min(1.0, elapsed / max(1e-6, self.config.dwell_seconds)))

    def _commit(self, label: str, now: float, source: str) -> KeyPress:
        self._last_press = now
        # A press restarts the dwell so holding still does not repeat.
        self._hover_since = now
        press = self._apply(label, now)
        return KeyPress(label=label, char=press, source=source)

    def _apply(self, label: str, now: float) -> str:
        """Mutate the buffer for a key. Returns the character typed, if any."""
        before = (self.layout, self.shift, self.caps_lock)
        try:
            return self._apply_key(label, now)
        finally:
            if (self.layout, self.shift, self.caps_lock) != before:
                self.layout_revision += 1

    def _apply_key(self, label: str, now: float) -> str:
        if label == SHIFT:
            # Double-tapping shift within the dwell window is caps lock —
            # the same convention every phone keyboard uses.
            if now - self._last_shift_tap < 0.6:
                self.caps_lock = not self.caps_lock
                self.shift = False
            else:
                self.shift = not self.shift
            self._last_shift_tap = now
            return ""
        if label == BACKSPACE:
            self.buffer = self.buffer[:-1]
            return ""
        if label == ENTER:
            self.buffer += "\n"
            return "\n"
        if label == SPACE:
            self.buffer += " "
            char = " "
        elif label == LAYOUT:
            # Reached from letters (go to numbers) and from symbols (go back
            # to numbers) — same key, same destination.
            self.layout = Layout.NUMBERS
            return ""
        elif label == "#+=":
            self.layout = Layout.SYMBOLS
            return ""
        elif label == LETTERS_KEY:
            self.layout = Layout.LETTERS
            return ""
        else:
            char = label.upper() if (self.shift or self.caps_lock) else label
            self.buffer += char

        # Shift is one-shot; caps lock is not.
        if self.shift and not self.caps_lock:
            self.shift = False
        return char

    # --- App interface ---------------------------------------------------

    @property
    def actions(self) -> tuple[str, ...]:
        return ("type", "backspace", "clear", "send", "set_layout",
                "toggle_dwell", "layout")

    def state(self) -> dict[str, Any]:
        return {
            "layout": self.layout.value,
            "shift": self.shift,
            "capsLock": self.caps_lock,
            "buffer": self.buffer,
            "dwellEnabled": self.config.dwell_enabled,
            "dwellSeconds": self.config.dwell_seconds,
            "hover": self._hover,
            "layoutRevision": self.layout_revision,
        }

    def layout_payload(self) -> dict[str, Any]:
        """The drawn keys. Fetch when ``layoutRevision`` changes."""
        return {
            "layoutRevision": self.layout_revision,
            "keys": [
                {"label": k.label, "x": k.x, "y": k.y, "w": k.width, "h": k.height}
                for k in self.keys()
            ],
        }

    def action(self, name: str, /, **payload: Any) -> ActionResult:
        if name == "type":
            text = str(payload.get("text", ""))
            self.buffer += text
            return ActionResult(message=f"typed {len(text)} characters")

        if name == "backspace":
            self.buffer = self.buffer[:-1]
            return ActionResult()

        if name == "clear":
            self.buffer = ""
            return ActionResult()

        if name == "send":
            text, self.buffer = self.buffer, ""
            if not text:
                return ActionResult.fail("nothing to send")
            # Typing into whatever has focus is a real OS effect, so it goes
            # out as a request rather than being done here.
            return ActionResult(
                message=f"sending {len(text)} characters",
                requests=(
                    OSRequest("keyboard.type", f"type {len(text)} characters",
                              {"text": text}),
                ),
            )

        if name == "layout":
            return ActionResult(data=self.layout_payload())

        if name == "set_layout":
            try:
                layout = Layout(str(payload.get("layout", "")))
            except ValueError:
                return ActionResult.fail("unknown layout")
            if layout is not self.layout:
                self.layout = layout
                self.layout_revision += 1
            return ActionResult(message=self.layout.value)

        if name == "toggle_dwell":
            self.config.dwell_enabled = not self.config.dwell_enabled
            return ActionResult(
                message="dwell on" if self.config.dwell_enabled else "dwell off"
            )

        return self.unknown(name)
