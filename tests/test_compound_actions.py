"""Compound pointer intents and OS dispatch.

The compound tests pin down the click/double-click/hold/drag disambiguation.
The dispatch tests prove the safety invariants hold at the one place where
gestures become real OS effects.
"""

from __future__ import annotations

import pytest

from handgesture.control.actions import ActionDispatcher
from handgesture.control.safety import ConfirmationGate, EmergencyStop, StopReason
from handgesture.gestures.compound import (
    ClickAction,
    ClickEvent,
    CompoundConfig,
    CompoundDetector,
)
from handgesture.gestures.debounce import EventType, GestureEvent
from handgesture.gestures.motion import MotionResult
from handgesture.gestures.vocabulary import Gesture, Mode
from handgesture.osadapter.null import NullAdapter
from handgesture.types import Point


def begin(gesture, t):
    return GestureEvent(EventType.BEGIN, gesture, 1.0, t)


def end(gesture, t):
    return GestureEvent(EventType.END, gesture, 1.0, t)


def actions(events):
    return [e.action for e in events]


# --- Compound: click vs double vs hold vs drag -----------------------------

def test_quick_pinch_becomes_a_click_after_the_double_window():
    d = CompoundDetector()
    d.update([begin(Gesture.PINCH, 0.0)], 0.0)
    d.update([end(Gesture.PINCH, 0.1)], 0.1)

    # Still pending: it might yet become a double-click.
    assert actions(d.update([], 0.2)) == []

    out = d.update([], 0.6)
    assert actions(out) == [ClickAction.CLICK]


def test_two_quick_pinches_become_a_double_click():
    d = CompoundDetector()
    d.update([begin(Gesture.PINCH, 0.0)], 0.0)
    d.update([end(Gesture.PINCH, 0.1)], 0.1)
    out = d.update([begin(Gesture.PINCH, 0.25)], 0.25)

    assert ClickAction.DOUBLE_CLICK in actions(out)


def test_double_click_does_not_also_emit_a_single():
    """The classic bug: firing click, click, double-click for one gesture."""
    d = CompoundDetector()
    d.update([begin(Gesture.PINCH, 0.0)], 0.0)
    d.update([end(Gesture.PINCH, 0.1)], 0.1)
    d.update([begin(Gesture.PINCH, 0.25)], 0.25)
    seen = actions(d.update([end(Gesture.PINCH, 0.3)], 0.3))
    seen += actions(d.update([], 1.0))

    assert ClickAction.CLICK not in seen


def test_slow_second_pinch_is_two_separate_clicks():
    d = CompoundDetector()
    d.update([begin(Gesture.PINCH, 0.0)], 0.0)
    d.update([end(Gesture.PINCH, 0.1)], 0.1)
    first = actions(d.update([], 0.7))

    d.update([begin(Gesture.PINCH, 1.0)], 1.0)
    d.update([end(Gesture.PINCH, 1.1)], 1.1)
    second = actions(d.update([], 1.7))

    assert first == [ClickAction.CLICK]
    assert second == [ClickAction.CLICK]


def test_held_pinch_becomes_click_and_hold():
    d = CompoundDetector()
    d.update([begin(Gesture.PINCH, 0.0)], 0.0)
    out = d.update([], 0.5)
    assert actions(out) == [ClickAction.HOLD_BEGIN]
    assert d.holding


def test_releasing_a_hold_emits_hold_end():
    d = CompoundDetector()
    d.update([begin(Gesture.PINCH, 0.0)], 0.0)
    d.update([], 0.5)
    out = d.update([end(Gesture.PINCH, 0.8)], 0.8)
    assert actions(out) == [ClickAction.HOLD_END]
    assert not d.holding


def test_a_long_hold_is_not_also_a_click():
    d = CompoundDetector()
    d.update([begin(Gesture.PINCH, 0.0)], 0.0)
    d.update([], 0.5)
    seen = actions(d.update([end(Gesture.PINCH, 0.9)], 0.9))
    seen += actions(d.update([], 2.0))
    assert ClickAction.CLICK not in seen


def test_moving_while_holding_starts_a_drag():
    d = CompoundDetector()
    origin = Point(0.5, 0.5)
    d.update([begin(Gesture.PINCH, 0.0)], 0.0, hand_position=origin)
    d.update([], 0.5, hand_position=origin)          # hold begins
    out = d.update([], 0.6, hand_position=Point(0.6, 0.5))

    assert ClickAction.DRAG_BEGIN in actions(out)
    assert d.dragging


def test_drag_end_on_release():
    d = CompoundDetector()
    origin = Point(0.5, 0.5)
    d.update([begin(Gesture.PINCH, 0.0)], 0.0, hand_position=origin)
    d.update([], 0.5, hand_position=origin)
    d.update([], 0.6, hand_position=Point(0.6, 0.5))
    out = d.update([end(Gesture.PINCH, 0.9)], 0.9, hand_position=Point(0.6, 0.5))

    assert actions(out) == [ClickAction.DRAG_END]


def test_holding_still_does_not_start_a_drag():
    """Hand tremor while holding must not turn a hold into a drag."""
    d = CompoundDetector()
    origin = Point(0.5, 0.5)
    d.update([begin(Gesture.PINCH, 0.0)], 0.0, hand_position=origin)
    d.update([], 0.5, hand_position=origin)
    out = d.update([], 0.6, hand_position=Point(0.502, 0.501))

    assert ClickAction.DRAG_BEGIN not in actions(out)


def test_peace_gesture_is_a_right_click():
    d = CompoundDetector()
    out = d.update([begin(Gesture.PEACE, 0.0)], 0.0)
    assert actions(out) == [ClickAction.RIGHT_CLICK]


def test_reset_clears_pending_state():
    d = CompoundDetector()
    d.update([begin(Gesture.PINCH, 0.0)], 0.0)
    d.update([end(Gesture.PINCH, 0.1)], 0.1)
    d.reset()
    assert actions(d.update([], 1.0)) == []


def test_hold_threshold_is_configurable():
    d = CompoundDetector(CompoundConfig(hold_seconds=2.0))
    d.update([begin(Gesture.PINCH, 0.0)], 0.0)
    assert actions(d.update([], 0.6)) == []
    assert actions(d.update([], 2.5)) == [ClickAction.HOLD_BEGIN]


# --- Dispatch --------------------------------------------------------------

@pytest.fixture
def rig():
    adapter = NullAdapter()
    stop = EmergencyStop()
    gate = ConfirmationGate()
    return adapter, stop, ActionDispatcher(adapter, stop, gate), gate


def test_click_reaches_the_adapter(rig):
    adapter, _stop, dispatcher, _gate = rig
    dispatcher.dispatch_clicks([ClickEvent(ClickAction.CLICK, 0.0)])
    assert adapter.count("click") == 1


def test_double_click_sends_two_clicks(rig):
    adapter, _stop, dispatcher, _gate = rig
    dispatcher.dispatch_clicks([ClickEvent(ClickAction.DOUBLE_CLICK, 0.0)])
    assert adapter.last("click").kwargs["count"] == 2


def test_right_click_uses_the_right_button(rig):
    adapter, _stop, dispatcher, _gate = rig
    dispatcher.dispatch_clicks([ClickEvent(ClickAction.RIGHT_CLICK, 0.0)])
    assert adapter.last("click").args == ("right",)


def test_hold_presses_and_releases_once(rig):
    """A drag must not press the button twice — that would drop the item."""
    adapter, _stop, dispatcher, _gate = rig
    dispatcher.dispatch_clicks(
        [
            ClickEvent(ClickAction.HOLD_BEGIN, 0.0),
            ClickEvent(ClickAction.DRAG_BEGIN, 0.1),
            ClickEvent(ClickAction.DRAG_MOVE, 0.2),
            ClickEvent(ClickAction.DRAG_END, 0.3),
        ]
    )
    assert adapter.count("mouse_down") == 1
    assert adapter.count("mouse_up") == 1


def test_emergency_stop_blocks_every_click(rig):
    adapter, stop, dispatcher, _gate = rig
    stop.engage(StopReason.GESTURE, now=0.0)
    dispatcher.dispatch_clicks([ClickEvent(ClickAction.CLICK, 0.0)])
    assert adapter.actions() == []


def test_emergency_stop_blocks_motion(rig):
    adapter, stop, dispatcher, _gate = rig
    stop.engage(StopReason.API, now=0.0)
    dispatcher.dispatch_motion(MotionResult(scroll=(0.0, 5.0)), Mode.NAVIGATION)
    assert adapter.actions() == []


def test_scroll_reaches_the_adapter(rig):
    adapter, _stop, dispatcher, _gate = rig
    dispatcher.dispatch_motion(MotionResult(scroll=(0.0, 4.0)), Mode.NAVIGATION)
    assert adapter.count("scroll") == 1


def test_zoom_sends_a_modifier_chord(rig):
    adapter, _stop, dispatcher, _gate = rig
    dispatcher.dispatch_motion(MotionResult(zoom=0.3), Mode.BROWSER)
    assert adapter.last("press_keys").args == ("ctrl", "plus")


def test_browser_swipe_navigates(rig):
    adapter, _stop, dispatcher, _gate = rig
    dispatcher.dispatch_motion(MotionResult(gesture=Gesture.SWIPE_LEFT), Mode.BROWSER)
    assert adapter.last("press_keys").args == ("alt", "left")


def test_media_swipe_sends_media_keys(rig):
    adapter, _stop, dispatcher, _gate = rig
    dispatcher.dispatch_motion(MotionResult(gesture=Gesture.SWIPE_RIGHT), Mode.MEDIA)
    assert adapter.last("media_key").args == ("next",)


def test_rotation_adjusts_volume_in_media_mode(rig):
    adapter, _stop, dispatcher, _gate = rig
    adapter.set_volume(0.5)
    adapter.clear()
    dispatcher.dispatch_motion(MotionResult(rotate=0.5), Mode.MEDIA)
    assert adapter.count("set_volume") == 1
    assert adapter.get_volume() > 0.5


def test_rotation_does_nothing_outside_media_mode(rig):
    adapter, _stop, dispatcher, _gate = rig
    dispatcher.dispatch_motion(MotionResult(rotate=0.5), Mode.NAVIGATION)
    assert adapter.count("set_volume") == 0


def test_clipboard_operations(rig):
    adapter, _stop, dispatcher, _gate = rig
    dispatcher.clipboard("copy")
    assert adapter.last("press_keys").args == ("ctrl", "c")


# --- Confirmation gate ------------------------------------------------------

def test_safe_operation_executes_immediately(rig):
    adapter, _stop, dispatcher, _gate = rig
    result = dispatcher.request("system.screenshot", "take a screenshot", now=0.0)
    assert result.executed
    assert adapter.count("screenshot") == 1


def test_destructive_operation_waits_for_confirmation(rig):
    adapter, _stop, dispatcher, _gate = rig
    result = dispatcher.request("file.delete", "delete report.pdf", now=0.0)

    assert not result.executed
    assert result.awaiting_confirmation
    assert adapter.actions() == []


def test_confirmation_requires_the_full_hold(rig):
    _adapter, _stop, dispatcher, gate = rig
    dispatcher.request("system.shutdown", "shut down", now=0.0)

    assert dispatcher.confirm_pending(now=0.5, held_seconds=0.1) is None
    assert gate.pending is not None


def test_cancelling_a_confirmation_drops_it(rig):
    adapter, _stop, dispatcher, gate = rig
    dispatcher.request("file.delete", "delete", now=0.0)
    cancelled = dispatcher.cancel_pending()

    assert cancelled is not None
    assert not cancelled.executed
    assert gate.pending is None
    assert adapter.actions() == []


def test_emergency_stop_blocks_requests(rig):
    adapter, stop, dispatcher, _gate = rig
    stop.engage(StopReason.GESTURE, now=0.0)
    result = dispatcher.request("system.screenshot", "shot", now=0.0)

    assert not result.executed
    assert adapter.actions() == []


def test_unknown_operation_is_reported_not_crashed(rig):
    _adapter, _stop, dispatcher, _gate = rig
    result = dispatcher.request("does.not.exist", "???", now=0.0)
    assert not result.executed


def test_history_is_bounded(rig):
    """This runs for the life of a session; it must not grow without bound."""
    _adapter, _stop, dispatcher, _gate = rig
    for _ in range(500):
        dispatcher.dispatch_clicks([ClickEvent(ClickAction.CLICK, 0.0)])
    assert len(dispatcher.history) <= 200
