"""Compound gestures: patterns of pinches over time.

A single pinch is ambiguous at the moment it happens. It might be a click,
the first half of a double-click, the start of a click-and-hold, or the
start of a drag. You cannot know which until you see what follows.

This resolves that by *delaying* the decision:

* a pinch that releases quickly and is not followed by another → **click**
* two quick pinches within the double window → **double-click**
* a pinch held past the hold threshold → **click-and-hold**
* a held pinch that then moves → **drag**

The cost is latency on a single click (it must wait out the double-click
window). That is the standard trade every double-click implementation
makes, and it is preferable to firing two single clicks when the user meant
one double.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..types import Point
from .debounce import EventType, GestureEvent
from .vocabulary import Gesture


class ClickAction(str, Enum):
    """A resolved pointer intent, ready for the OS adapter."""

    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    HOLD_BEGIN = "hold_begin"        # button pressed, still down
    HOLD_END = "hold_end"            # button released
    DRAG_BEGIN = "drag_begin"
    DRAG_MOVE = "drag_move"
    DRAG_END = "drag_end"


@dataclass(frozen=True, slots=True)
class ClickEvent:
    action: ClickAction
    timestamp: float
    #: Cursor position at the moment of the action, when known.
    position: tuple[float, float] | None = None


@dataclass(slots=True)
class CompoundConfig:
    #: A pinch releasing within this window is a candidate click rather
    #: than a hold.
    click_max_seconds: float = 0.45
    #: A second pinch beginning within this window of the first release
    #: makes a double-click.
    double_window_seconds: float = 0.40
    #: A pinch held beyond this becomes click-and-hold.
    hold_seconds: float = 0.45
    #: Movement (in normalised frame units) while holding that promotes
    #: the hold into a drag.
    drag_threshold: float = 0.035


class CompoundDetector:
    """Resolves pinch patterns into concrete pointer actions.

    Driven by the debounced gesture events from Phase 1, so it never sees
    the noise the debouncer already filtered out.
    """

    def __init__(self, config: CompoundConfig | None = None) -> None:
        self.config = config or CompoundConfig()
        self._pinch_start: float | None = None
        self._pinch_origin: Point | None = None
        self._holding = False
        self._dragging = False
        #: Timestamp of the last completed short pinch, awaiting a possible
        #: partner to become a double-click.
        self._pending_click: float | None = None
        self._pending_position: tuple[float, float] | None = None

    def reset(self) -> None:
        self._pinch_start = None
        self._pinch_origin = None
        self._holding = False
        self._dragging = False
        self._pending_click = None
        self._pending_position = None

    def release(
        self, now: float, cursor: tuple[float, float] | None = None
    ) -> list[ClickEvent]:
        """Terminate any in-flight hold or drag and clear all state.

        Distinct from :meth:`reset`, which drops state silently. This one
        emits the events needed to *undo* a held button, so an emergency
        stop or a lost hand cannot leave the mouse pressed down — the exact
        failure the emergency stop exists to prevent.
        """
        out: list[ClickEvent] = []
        if self._dragging:
            out.append(ClickEvent(ClickAction.DRAG_END, now, cursor))
        elif self._holding:
            out.append(ClickEvent(ClickAction.HOLD_END, now, cursor))
        self.reset()
        return out

    @property
    def holding(self) -> bool:
        return self._holding

    @property
    def dragging(self) -> bool:
        return self._dragging

    def update(
        self,
        events: list[GestureEvent],
        now: float,
        hand_position: Point | None = None,
        cursor: tuple[float, float] | None = None,
    ) -> list[ClickEvent]:
        """Process this frame's gesture events into pointer actions."""
        cfg = self.config
        out: list[ClickEvent] = []

        for event in events:
            if event.gesture is Gesture.PEACE and event.type is EventType.BEGIN:
                out.append(ClickEvent(ClickAction.RIGHT_CLICK, now, cursor))
                continue

            if event.gesture is not Gesture.PINCH:
                continue

            if event.type is EventType.BEGIN:
                self._pinch_start = now
                self._pinch_origin = hand_position
                # A second pinch inside the double window resolves the
                # pending single click into a double instead.
                if (
                    self._pending_click is not None
                    and now - self._pending_click <= cfg.double_window_seconds
                ):
                    self._pending_click = None
                    self._pending_position = None
                    out.append(ClickEvent(ClickAction.DOUBLE_CLICK, now, cursor))
                    # Mark this pinch as consumed so its release does not
                    # also emit a click.
                    self._pinch_start = None

            elif event.type is EventType.END:
                out.extend(self._on_release(now, cursor))

        # Promote a sustained pinch into hold, then into drag.
        if self._pinch_start is not None and not self._holding:
            if now - self._pinch_start >= cfg.hold_seconds:
                self._holding = True
                out.append(ClickEvent(ClickAction.HOLD_BEGIN, now, cursor))

        if self._holding and hand_position is not None and self._pinch_origin is not None:
            moved = hand_position.distance_to(self._pinch_origin)
            if not self._dragging and moved >= cfg.drag_threshold:
                self._dragging = True
                out.append(ClickEvent(ClickAction.DRAG_BEGIN, now, cursor))
            elif self._dragging:
                out.append(ClickEvent(ClickAction.DRAG_MOVE, now, cursor))

        # A pending single click times out into an actual click.
        if (
            self._pending_click is not None
            and now - self._pending_click > cfg.double_window_seconds
        ):
            position = self._pending_position
            self._pending_click = None
            self._pending_position = None
            out.append(ClickEvent(ClickAction.CLICK, now, position))

        return out

    def _on_release(
        self, now: float, cursor: tuple[float, float] | None
    ) -> list[ClickEvent]:
        out: list[ClickEvent] = []

        if self._dragging:
            out.append(ClickEvent(ClickAction.DRAG_END, now, cursor))
        elif self._holding:
            out.append(ClickEvent(ClickAction.HOLD_END, now, cursor))
        elif self._pinch_start is not None:
            duration = now - self._pinch_start
            if duration <= self.config.click_max_seconds:
                # Hold it back: it may yet turn into a double-click.
                self._pending_click = now
                self._pending_position = cursor

        self._pinch_start = None
        self._pinch_origin = None
        self._holding = False
        self._dragging = False
        return out
