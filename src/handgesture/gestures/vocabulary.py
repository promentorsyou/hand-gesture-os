"""The gesture vocabulary and interaction modes.

Kept separate from the recogniser so the config layer, the UI, and the tests
can all refer to gestures without importing recognition logic.
"""

from __future__ import annotations

from enum import Enum


class Gesture(str, Enum):
    """Every gesture the system can recognise.

    Phase 1 implements the static poses and the two-hand pair; the motion
    gestures (swipes, rotations) are declared here so the config, UI, and
    mode tables are complete, and are filled in during Phase 2.
    """

    NONE = "none"

    # --- Static single-hand poses (Phase 1) ---
    POINT = "point"                  # index only -> move cursor
    PINCH = "pinch"                  # thumb+index -> click
    PEACE = "peace"                  # index+middle -> right click
    FIST = "fist"                    # closed -> grab / select
    OPEN_PALM = "open_palm"          # flat hand -> pause interaction
    PALM_FORWARD = "palm_forward"    # palm square to camera -> home (held)
    THUMBS_UP = "thumbs_up"          # confirm

    # --- Two-hand poses (Phase 1) ---
    CROSSED_HANDS = "crossed_hands"  # emergency stop
    FRAME = "frame"                  # both hands framing -> screenshot
    SPREAD = "spread"                # hands apart -> zoom in
    CONVERGE = "converge"            # hands together -> zoom out

    # --- Motion gestures (Phase 2) ---
    SWIPE_LEFT = "swipe_left"
    SWIPE_RIGHT = "swipe_right"
    SWIPE_UP = "swipe_up"
    SWIPE_DOWN = "swipe_down"
    SCROLL_VERTICAL = "scroll_vertical"
    SCROLL_HORIZONTAL = "scroll_horizontal"
    ROTATE = "rotate"

    # --- Derived / compound (Phase 2) ---
    DOUBLE_PINCH = "double_pinch"    # -> double click
    PINCH_HOLD = "pinch_hold"        # -> click and hold
    PINCH_DRAG = "pinch_drag"        # -> drag and drop
    PRECISION = "precision"          # one hand still + other pointing


#: Gestures that must never fire from a single frame — they represent
#: destructive or mode-changing actions and always require hold/confirm.
DELIBERATE_GESTURES = frozenset(
    {
        Gesture.CROSSED_HANDS,
        Gesture.PALM_FORWARD,
        Gesture.FRAME,
    }
)


class Mode(str, Enum):
    """Interaction modes. The active mode gates which gestures are live,
    which is the main defence against accidental actions."""

    NAVIGATION = "navigation"
    TYPING = "typing"
    MEDIA = "media"
    WINDOW = "window"
    FILE = "file"
    BROWSER = "browser"
    PRECISION = "precision"
    PAUSED = "paused"


#: Which gestures each mode listens to. ``PAUSED`` deliberately accepts only
#: the gestures that can bring the system back to life.
MODE_GESTURES: dict[Mode, frozenset[Gesture]] = {
    Mode.PAUSED: frozenset({Gesture.OPEN_PALM, Gesture.CROSSED_HANDS}),
    Mode.NAVIGATION: frozenset(
        {
            Gesture.POINT,
            Gesture.PINCH,
            Gesture.DOUBLE_PINCH,
            Gesture.PINCH_HOLD,
            Gesture.PINCH_DRAG,
            Gesture.PEACE,
            Gesture.FIST,
            Gesture.OPEN_PALM,
            Gesture.PALM_FORWARD,
            Gesture.SCROLL_VERTICAL,
            Gesture.SCROLL_HORIZONTAL,
            Gesture.SWIPE_LEFT,
            Gesture.SWIPE_RIGHT,
            Gesture.SWIPE_UP,
            Gesture.SWIPE_DOWN,
            Gesture.SPREAD,
            Gesture.CONVERGE,
            Gesture.CROSSED_HANDS,
            Gesture.FRAME,
        }
    ),
    Mode.TYPING: frozenset(
        {Gesture.POINT, Gesture.PINCH, Gesture.OPEN_PALM, Gesture.CROSSED_HANDS}
    ),
    Mode.MEDIA: frozenset(
        {
            Gesture.POINT,
            Gesture.PINCH,
            Gesture.ROTATE,
            Gesture.SWIPE_LEFT,
            Gesture.SWIPE_RIGHT,
            Gesture.OPEN_PALM,
            Gesture.CROSSED_HANDS,
        }
    ),
    Mode.WINDOW: frozenset(
        {
            Gesture.POINT,
            Gesture.FIST,
            Gesture.PINCH_DRAG,
            Gesture.SPREAD,
            Gesture.CONVERGE,
            Gesture.SWIPE_UP,
            Gesture.SWIPE_DOWN,
            # Phase 3: app switching, the home screen, and the
            # quick-settings shade are all reachable from window mode.
            Gesture.SWIPE_LEFT,
            Gesture.SWIPE_RIGHT,
            Gesture.PALM_FORWARD,
            Gesture.PEACE,
            Gesture.OPEN_PALM,
            Gesture.CROSSED_HANDS,
        }
    ),
    Mode.FILE: frozenset(
        {
            Gesture.POINT,
            Gesture.PINCH,
            Gesture.DOUBLE_PINCH,
            Gesture.PEACE,
            Gesture.PINCH_DRAG,
            Gesture.FIST,
            Gesture.OPEN_PALM,
            Gesture.CROSSED_HANDS,
        }
    ),
    Mode.BROWSER: frozenset(
        {
            Gesture.POINT,
            Gesture.PINCH,
            Gesture.PEACE,
            Gesture.SCROLL_VERTICAL,
            Gesture.SWIPE_LEFT,
            Gesture.SWIPE_RIGHT,
            Gesture.SPREAD,
            Gesture.CONVERGE,
            Gesture.OPEN_PALM,
            Gesture.CROSSED_HANDS,
        }
    ),
    Mode.PRECISION: frozenset(
        {Gesture.POINT, Gesture.PINCH, Gesture.OPEN_PALM, Gesture.CROSSED_HANDS}
    ),
}


def gesture_allowed(mode: Mode, gesture: Gesture) -> bool:
    """Whether ``gesture`` is live in ``mode``."""
    if gesture is Gesture.NONE:
        return True
    return gesture in MODE_GESTURES.get(mode, frozenset())
