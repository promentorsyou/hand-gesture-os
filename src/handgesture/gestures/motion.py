"""Motion gesture recognition.

Phase 1 classified single frames. Motion gestures are different in kind:
they only exist across time, so they need a history buffer and a velocity
estimate rather than a pose score.

Three families live here:

* **Swipes** — a fast, straight, committed travel in one direction. Speed
  alone is not enough; a swipe must also be *straight* and must *stop*,
  otherwise ordinary hand repositioning fires one constantly.
* **Scroll** — sustained two-finger drift. Unlike a swipe this is
  continuous and reports a delta every frame.
* **Zoom / rotate** — derived from two hands, or from wrist roll.

Everything is timestamp-driven so it is testable without sleeping.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from ..types import Frame, Hand, Point
from . import features as feat
from .vocabulary import Gesture


@dataclass(slots=True)
class MotionConfig:
    """Tunables for motion detection.

    Defaults lean conservative: an accidental swipe navigates you away from
    what you were doing, which is far more disruptive than a swipe that
    needs repeating.
    """

    #: Seconds of history retained. Long enough to measure a swipe, short
    #: enough that stale movement does not leak into the next gesture.
    history_seconds: float = 0.5
    #: Minimum travel (in palm spans) for a swipe to count.
    swipe_min_distance: float = 1.4
    #: Minimum speed in palm spans per second.
    swipe_min_speed: float = 3.2
    #: How straight the path must be, 0..1 (net displacement / path length).
    #: A wandering path is repositioning, not a swipe.
    swipe_min_straightness: float = 0.82
    #: How strongly one axis must dominate for a directional swipe.
    swipe_axis_ratio: float = 1.7
    #: Lockout after a swipe fires, so one motion cannot fire twice.
    swipe_cooldown_seconds: float = 0.6
    #: Minimum per-frame movement (palm spans) before scroll reports.
    scroll_deadzone: float = 0.04
    #: Multiplier from palm-span movement to scroll units.
    scroll_gain: float = 12.0
    #: Minimum change in two-hand separation (palm spans) to report zoom.
    zoom_deadzone: float = 0.05
    #: Minimum wrist-roll change in radians to report rotation.
    rotate_deadzone: float = 0.06


@dataclass(frozen=True, slots=True)
class MotionResult:
    """What the motion tracker saw this frame."""

    #: A discrete motion gesture that fired this frame, if any.
    gesture: Gesture = Gesture.NONE
    confidence: float = 0.0
    #: Continuous scroll delta in scroll units (dx, dy).
    scroll: tuple[float, float] = (0.0, 0.0)
    #: Zoom delta: positive = zooming in (hands apart).
    zoom: float = 0.0
    #: Wrist rotation delta in radians this frame.
    rotate: float = 0.0
    #: Current hand speed in palm spans per second, for UI feedback.
    speed: float = 0.0


@dataclass(slots=True)
class _Sample:
    t: float
    pos: Point
    span: float


class MotionTracker:
    """Detects motion gestures from a stream of frames."""

    def __init__(self, config: MotionConfig | None = None) -> None:
        self.config = config or MotionConfig()
        self._history: deque[_Sample] = deque()
        self._last_swipe: float = -999.0
        self._last_two_hand_distance: float | None = None
        self._last_roll: float | None = None

    def reset(self) -> None:
        """Drop history. Called on emergency stop, mode change, hand loss."""
        self._history.clear()
        self._last_two_hand_distance = None
        self._last_roll = None
        # Deliberately keep _last_swipe: the cooldown should survive a reset
        # so a swipe that ends in tracking loss cannot immediately refire.

    def _trim(self, now: float) -> None:
        cutoff = now - self.config.history_seconds
        while self._history and self._history[0].t < cutoff:
            self._history.popleft()

    def update(self, frame: Frame, active_gesture: Gesture | None = None) -> MotionResult:
        """Feed one frame and get whatever motion it produced.

        ``active_gesture`` is the currently held static gesture; motion is
        interpreted relative to it (two fingers held = scroll, for example).
        """
        now = frame.timestamp

        if frame.hand_count == 0:
            self._history.clear()
            self._last_two_hand_distance = None
            self._last_roll = None
            return MotionResult()

        hand = frame.hands[0]
        self._history.append(_Sample(now, hand.palm_center, hand.palm_span))
        self._trim(now)

        zoom = self._zoom(frame)
        rotate = self._rotate(hand)
        speed = self._speed()

        # Two fingers extended and moving = scroll. Checked before swipe so
        # a deliberate scroll never registers as a swipe.
        if active_gesture is Gesture.PEACE:
            dx, dy = self._scroll_delta()
            if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                gesture = (
                    Gesture.SCROLL_HORIZONTAL
                    if abs(dx) > abs(dy)
                    else Gesture.SCROLL_VERTICAL
                )
                return MotionResult(
                    gesture=gesture, confidence=1.0, scroll=(dx, dy), speed=speed
                )
            return MotionResult(scroll=(0.0, 0.0), speed=speed)

        swipe = self._swipe(now)
        if swipe is not Gesture.NONE:
            return MotionResult(
                gesture=swipe, confidence=1.0, zoom=zoom, rotate=rotate, speed=speed
            )

        if abs(zoom) > 1e-6:
            return MotionResult(
                gesture=Gesture.SPREAD if zoom > 0 else Gesture.CONVERGE,
                confidence=1.0,
                zoom=zoom,
                speed=speed,
            )

        if abs(rotate) > 1e-6:
            return MotionResult(
                gesture=Gesture.ROTATE, confidence=1.0, rotate=rotate, speed=speed
            )

        return MotionResult(speed=speed)

    # --- Individual detectors -----------------------------------------

    def _speed(self) -> float:
        """Current speed in palm spans per second."""
        if len(self._history) < 2:
            return 0.0
        a, b = self._history[-2], self._history[-1]
        dt = b.t - a.t
        if dt <= 0:
            return 0.0
        return (b.pos.distance_to(a.pos) / max(1e-6, b.span)) / dt

    def _swipe(self, now: float) -> Gesture:
        """Detect a completed directional swipe.

        Requires travel, speed, straightness, and a dominant axis. All four
        together are what separate an intentional swipe from a hand simply
        moving across the frame.
        """
        cfg = self.config
        if now - self._last_swipe < cfg.swipe_cooldown_seconds:
            return Gesture.NONE
        if len(self._history) < 4:
            return Gesture.NONE

        first, last = self._history[0], self._history[-1]
        dt = last.t - first.t
        if dt <= 0:
            return Gesture.NONE

        span = max(1e-6, last.span)
        dx = (last.pos.x - first.pos.x) / span
        dy = (last.pos.y - first.pos.y) / span
        net = math.hypot(dx, dy)
        if net < cfg.swipe_min_distance:
            return Gesture.NONE
        if net / dt < cfg.swipe_min_speed:
            return Gesture.NONE

        # Straightness: net displacement over total path travelled. A hand
        # wandering back and forth covers path length without displacement.
        path = 0.0
        for a, b in zip(self._history, list(self._history)[1:], strict=False):
            path += b.pos.distance_to(a.pos) / span
        if path <= 0 or (net / path) < cfg.swipe_min_straightness:
            return Gesture.NONE

        adx, ady = abs(dx), abs(dy)
        if adx > ady * cfg.swipe_axis_ratio:
            direction = Gesture.SWIPE_LEFT if dx < 0 else Gesture.SWIPE_RIGHT
        elif ady > adx * cfg.swipe_axis_ratio:
            direction = Gesture.SWIPE_UP if dy < 0 else Gesture.SWIPE_DOWN
        else:
            return Gesture.NONE  # diagonal — ambiguous, ignore

        self._last_swipe = now
        self._history.clear()  # consumed; don't re-detect the same motion
        return direction

    def _scroll_delta(self) -> tuple[float, float]:
        """Per-frame scroll delta in scroll units."""
        cfg = self.config
        if len(self._history) < 2:
            return (0.0, 0.0)
        a, b = self._history[-2], self._history[-1]
        span = max(1e-6, b.span)
        dx = (b.pos.x - a.pos.x) / span
        dy = (b.pos.y - a.pos.y) / span
        if math.hypot(dx, dy) < cfg.scroll_deadzone:
            return (0.0, 0.0)
        # Screen scroll is conventionally inverted relative to hand movement:
        # pushing the hand up scrolls the content up (i.e. view moves down).
        return (dx * cfg.scroll_gain, -dy * cfg.scroll_gain)

    def _zoom(self, frame: Frame) -> float:
        """Change in two-hand separation since the last frame."""
        if frame.hand_count < 2:
            self._last_two_hand_distance = None
            return 0.0

        a, b = frame.hands[0], frame.hands[1]
        span = max(1e-6, (a.palm_span + b.palm_span) / 2.0)
        distance = a.palm_center.distance_to(b.palm_center) / span

        previous = self._last_two_hand_distance
        self._last_two_hand_distance = distance
        if previous is None:
            return 0.0

        delta = distance - previous
        return delta if abs(delta) >= self.config.zoom_deadzone else 0.0

    def _rotate(self, hand: Hand) -> float:
        """Change in wrist roll since the last frame, in radians."""
        roll = feat.wrist_roll(hand)
        previous = self._last_roll
        self._last_roll = roll
        if previous is None:
            return 0.0

        # Take the shorter way round the circle so wrapping past pi does not
        # register as a huge rotation.
        delta = ((roll - previous) + math.pi) % (2 * math.pi) - math.pi
        return delta if abs(delta) >= self.config.rotate_deadzone else 0.0
