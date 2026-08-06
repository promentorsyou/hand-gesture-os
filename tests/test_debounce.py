"""Debouncing: the layer that decides a gesture was *intended*.

These tests encode the accidental-click protections. If these regress,
the system fires commands the user did not mean — the worst failure mode
this project has.
"""

from __future__ import annotations

import pytest

from handgesture.gestures.debounce import (
    DebounceConfig,
    EventType,
    GestureDebouncer,
)
from handgesture.gestures.vocabulary import Gesture


@pytest.fixture
def cfg() -> DebounceConfig:
    return DebounceConfig(
        hold_frames=3,
        hold_seconds=0.08,
        cooldown_seconds=0.35,
        min_confidence=0.55,
        dropout_grace_seconds=0.12,
    )


@pytest.fixture
def deb(cfg) -> GestureDebouncer:
    return GestureDebouncer(cfg)


def feed(deb, gesture, confidence, times):
    """Feed a run of frames, returning every event produced."""
    events = []
    for t in times:
        events.extend(deb.update(gesture, confidence, t))
    return events


def test_single_frame_does_not_fire(deb):
    """One good frame is never enough — that is the whole point."""
    assert deb.update(Gesture.PINCH, 0.9, 0.0) == []
    assert deb.active_gesture is None


def test_two_frames_do_not_fire(deb):
    events = feed(deb, Gesture.PINCH, 0.9, [0.0, 0.033])
    assert not any(e.type is EventType.BEGIN for e in events)


def test_three_held_frames_fire_begin(deb):
    events = feed(deb, Gesture.PINCH, 0.9, [0.0, 0.05, 0.10])
    begins = [e for e in events if e.type is EventType.BEGIN]
    assert len(begins) == 1
    assert begins[0].gesture is Gesture.PINCH
    assert deb.active_gesture is Gesture.PINCH


def test_fast_frames_still_need_the_time_floor(deb):
    """Frame count alone must not confirm; a fast camera would fire instantly."""
    events = feed(deb, Gesture.PINCH, 0.9, [0.0, 0.005, 0.010])
    assert not any(e.type is EventType.BEGIN for e in events)


def test_low_confidence_never_confirms(deb):
    events = feed(deb, Gesture.PINCH, 0.3, [0.0, 0.05, 0.10, 0.15, 0.20])
    assert events == []
    assert deb.active_gesture is None


def test_confidence_run_uses_the_weakest_frame(deb):
    """A strong frame must not rescue an otherwise shaky hold."""
    deb.update(Gesture.PINCH, 0.99, 0.0)
    deb.update(Gesture.PINCH, 0.60, 0.05)
    events = deb.update(Gesture.PINCH, 0.99, 0.10)
    begin = next(e for e in events if e.type is EventType.BEGIN)
    assert begin.confidence == pytest.approx(0.60)


def test_flicker_between_gestures_does_not_confirm_either(deb):
    """Alternating poses is noise, not intent."""
    events = []
    for i, g in enumerate([Gesture.PINCH, Gesture.POINT] * 4):
        events.extend(deb.update(g, 0.9, i * 0.05))
    assert not any(e.type is EventType.BEGIN for e in events)


def test_hold_emits_hold_events(deb):
    feed(deb, Gesture.FIST, 0.9, [0.0, 0.05, 0.10])
    events = deb.update(Gesture.FIST, 0.9, 0.15)
    assert [e.type for e in events] == [EventType.HOLD]
    assert events[0].duration == pytest.approx(0.15)


def test_release_emits_end(deb):
    feed(deb, Gesture.FIST, 0.9, [0.0, 0.05, 0.10])
    events = deb.update(Gesture.NONE, 0.0, 0.40)
    assert [e.type for e in events] == [EventType.END]
    assert deb.active_gesture is None


def test_brief_dropout_does_not_end_the_gesture(deb):
    """A single dropped tracking frame must not cancel a deliberate hold."""
    feed(deb, Gesture.FIST, 0.9, [0.0, 0.05, 0.10])
    events = deb.update(Gesture.NONE, 0.0, 0.15)  # within grace
    assert events == []
    assert deb.active_gesture is Gesture.FIST

    events = deb.update(Gesture.FIST, 0.9, 0.18)
    assert [e.type for e in events] == [EventType.HOLD]


def test_dropout_past_grace_ends_the_gesture(deb):
    feed(deb, Gesture.FIST, 0.9, [0.0, 0.05, 0.10])
    events = deb.update(Gesture.NONE, 0.0, 0.40)  # well past grace
    assert [e.type for e in events] == [EventType.END]


def test_cooldown_blocks_immediate_refire(deb):
    """Two rapid pinches must not become a stream of clicks."""
    feed(deb, Gesture.PINCH, 0.9, [0.0, 0.05, 0.10])
    deb.update(Gesture.NONE, 0.0, 0.40)  # END, starts cooldown at 0.40

    events = feed(deb, Gesture.PINCH, 0.9, [0.45, 0.50, 0.55])
    assert not any(e.type is EventType.BEGIN for e in events)


def test_gesture_refires_after_cooldown(deb):
    feed(deb, Gesture.PINCH, 0.9, [0.0, 0.05, 0.10])
    deb.update(Gesture.NONE, 0.0, 0.40)

    events = feed(deb, Gesture.PINCH, 0.9, [0.80, 0.85, 0.90])
    assert any(e.type is EventType.BEGIN for e in events)


def test_switching_gesture_ends_old_and_starts_new(deb):
    feed(deb, Gesture.FIST, 0.9, [0.0, 0.05, 0.10])
    events = feed(deb, Gesture.POINT, 0.9, [0.15, 0.20, 0.25, 0.30])

    assert any(e.type is EventType.END and e.gesture is Gesture.FIST for e in events)
    assert any(e.type is EventType.BEGIN and e.gesture is Gesture.POINT for e in events)


def test_reset_clears_everything(deb):
    feed(deb, Gesture.FIST, 0.9, [0.0, 0.05, 0.10])
    assert deb.active_gesture is Gesture.FIST
    deb.reset()
    assert deb.active_gesture is None
    # Cooldowns are cleared too, so a gesture can fire again straight away.
    events = feed(deb, Gesture.FIST, 0.9, [0.2, 0.25, 0.30])
    assert any(e.type is EventType.BEGIN for e in events)


def test_stricter_config_requires_longer_hold():
    strict = GestureDebouncer(DebounceConfig(hold_frames=8, hold_seconds=0.3))
    events = feed(strict, Gesture.PINCH, 0.95, [i * 0.05 for i in range(6)])
    assert not any(e.type is EventType.BEGIN for e in events)

    events = feed(strict, Gesture.PINCH, 0.95, [i * 0.05 for i in range(6, 10)])
    assert any(e.type is EventType.BEGIN for e in events)
