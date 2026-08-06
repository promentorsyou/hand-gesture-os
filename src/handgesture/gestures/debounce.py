"""Turning noisy per-frame recognitions into intentional events.

A raw recogniser output is not a command. Hands wobble, MediaPipe drops a
frame, and a pose flickers on the way to the pose you actually meant. This
module is the gate between "a gesture was seen" and "the user meant it":

* a gesture must hold for ``hold_frames`` before it fires at all
* after firing, the same gesture is locked out for ``cooldown``
* low-confidence frames are ignored rather than breaking a hold
* a short dropout does not cancel an in-progress hold

Everything here is driven by an explicit timestamp so it is fully testable
without sleeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .vocabulary import Gesture


class EventType(str, Enum):
    BEGIN = "begin"      # gesture confirmed and started
    HOLD = "hold"        # still held, emitted each frame while active
    END = "end"          # gesture released


@dataclass(frozen=True, slots=True)
class GestureEvent:
    type: EventType
    gesture: Gesture
    confidence: float
    timestamp: float
    #: Seconds the gesture has been held, at emission time.
    duration: float = 0.0


@dataclass(slots=True)
class _Candidate:
    gesture: Gesture
    first_seen: float
    last_seen: float
    frames: int
    confidence: float


@dataclass(slots=True)
class DebounceConfig:
    """Tunables for the confirmation gate.

    Defaults are deliberately conservative: it is far worse to fire a click
    the user did not intend than to make them hold a beat longer.
    """

    #: Consecutive qualifying frames before a gesture is confirmed.
    hold_frames: int = 3
    #: Minimum wall-clock hold before confirmation, in seconds.
    hold_seconds: float = 0.08
    #: Lockout after a gesture ends before the same one can fire again.
    cooldown_seconds: float = 0.35
    #: Frames below this confidence are ignored (they neither confirm a
    #: gesture nor break an existing hold).
    min_confidence: float = 0.55
    #: How long a gesture may vanish without cancelling an active hold.
    #: Covers single dropped frames from the tracker.
    dropout_grace_seconds: float = 0.12


class GestureDebouncer:
    """Converts a stream of per-frame recognitions into discrete events."""

    def __init__(self, config: DebounceConfig | None = None) -> None:
        self.config = config or DebounceConfig()
        self._candidate: _Candidate | None = None
        self._active: Gesture | None = None
        self._active_since: float = 0.0
        self._active_confidence: float = 0.0
        self._last_seen_active: float = 0.0
        self._cooldowns: dict[Gesture, float] = {}

    @property
    def active_gesture(self) -> Gesture | None:
        return self._active

    def reset(self) -> None:
        """Drop all state. Used on emergency stop and on hand loss."""
        self._candidate = None
        self._active = None
        self._active_since = 0.0
        self._active_confidence = 0.0
        self._last_seen_active = 0.0
        self._cooldowns.clear()

    def _in_cooldown(self, gesture: Gesture, now: float) -> bool:
        until = self._cooldowns.get(gesture)
        return until is not None and now < until

    def update(
        self, gesture: Gesture, confidence: float, now: float
    ) -> list[GestureEvent]:
        """Feed one frame; get back whatever events it produced."""
        cfg = self.config
        events: list[GestureEvent] = []

        qualifies = gesture is not Gesture.NONE and confidence >= cfg.min_confidence

        # --- An gesture is already active ---
        if self._active is not None:
            if qualifies and gesture is self._active:
                self._last_seen_active = now
                self._active_confidence = confidence
                events.append(
                    GestureEvent(
                        EventType.HOLD,
                        self._active,
                        confidence,
                        now,
                        now - self._active_since,
                    )
                )
                return events

            # A different confident gesture, or a dropout that has outlasted
            # the grace window, ends the active one.
            different = qualifies and gesture is not self._active
            timed_out = (now - self._last_seen_active) > cfg.dropout_grace_seconds
            if different or timed_out:
                ended = self._active
                events.append(
                    GestureEvent(
                        EventType.END,
                        ended,
                        self._active_confidence,
                        now,
                        now - self._active_since,
                    )
                )
                self._cooldowns[ended] = now + cfg.cooldown_seconds
                self._active = None
                self._candidate = None
                # Fall through so a replacement gesture can start building
                # its own hold on this same frame.
            else:
                # Inside the grace window — hold the gesture open.
                return events

        # --- No active gesture: build toward confirmation ---
        if not qualifies:
            self._candidate = None
            return events

        if self._in_cooldown(gesture, now):
            return events

        cand = self._candidate
        if cand is None or cand.gesture is not gesture:
            self._candidate = _Candidate(gesture, now, now, 1, confidence)
            return events

        cand.frames += 1
        cand.last_seen = now
        # Track the weakest frame in the run, so a single strong frame can't
        # carry a shaky hold over the line.
        cand.confidence = min(cand.confidence, confidence)

        held_long_enough = (now - cand.first_seen) >= cfg.hold_seconds
        if cand.frames >= cfg.hold_frames and held_long_enough:
            self._active = gesture
            self._active_since = cand.first_seen
            self._active_confidence = cand.confidence
            self._last_seen_active = now
            self._candidate = None
            events.append(
                GestureEvent(
                    EventType.BEGIN, gesture, cand.confidence, now, now - cand.first_seen
                )
            )

        return events
