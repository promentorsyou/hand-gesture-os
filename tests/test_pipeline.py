"""End-to-end pipeline behaviour.

These are the integration tests: frames in one end, gesture events out the
other, with every safety layer in between actually engaged.
"""

from __future__ import annotations

from handgesture.capture.simulation import (
    pose_fist,
    pose_open_palm,
    pose_peace,
    pose_pinch,
    pose_point,
)
from handgesture.control.safety import StopReason
from handgesture.gestures.debounce import DebounceConfig, EventType
from handgesture.gestures.vocabulary import Gesture, Mode
from handgesture.pipeline import (
    GesturePipeline,
    PipelineConfig,
    TrackingHealth,
)
from handgesture.types import Frame, Handedness


def cfg() -> PipelineConfig:
    return PipelineConfig(
        debounce=DebounceConfig(hold_frames=3, hold_seconds=0.05, cooldown_seconds=0.3)
    )


def run(pipe, hands_per_frame, start=0.0, dt=0.05):
    """Feed a sequence of hand tuples; return the final state and all events."""
    events = []
    state = None
    for i, hands in enumerate(hands_per_frame):
        hands_t = hands if isinstance(hands, tuple) else (hands,)
        frame = Frame(hands=hands_t, timestamp=start + i * dt)
        state = pipe.process(frame)
        events.extend(state.events)
    return state, events


def test_empty_frames_produce_nothing():
    pipe = GesturePipeline(cfg())
    state = pipe.process(Frame())
    assert state.gesture is Gesture.NONE
    assert state.health is TrackingHealth.NO_HANDS
    assert state.events == []


def test_sustained_point_fires_and_moves_cursor():
    pipe = GesturePipeline(cfg())
    state, events = run(pipe, [pose_point(center=(0.5, 0.5))] * 5)

    assert any(
        e.type is EventType.BEGIN and e.gesture is Gesture.POINT for e in events
    )
    assert state.cursor is not None


def test_cursor_tracks_hand_movement():
    pipe = GesturePipeline(cfg())
    run(pipe, [pose_point(center=(0.4, 0.5))] * 5)
    left_x = pipe.cursor.position[0]

    run(pipe, [pose_point(center=(0.6, 0.5))] * 10, start=1.0)
    right_x = pipe.cursor.position[0]

    assert left_x != right_x


def test_cursor_does_not_drift_on_non_cursor_gestures():
    """A held fist must not drag the pointer around."""
    pipe = GesturePipeline(cfg())
    run(pipe, [pose_point(center=(0.5, 0.5))] * 5)
    parked = pipe.cursor.position

    run(pipe, [pose_fist(center=(0.2, 0.2))] * 6, start=1.0)
    assert pipe.cursor.position == parked


def test_crossed_hands_engages_emergency_stop():
    pipe = GesturePipeline(cfg())
    crossed = (
        pose_open_palm(center=(0.35, 0.5), handedness=Handedness.LEFT),
        pose_open_palm(center=(0.60, 0.5), handedness=Handedness.RIGHT),
    )
    state, _ = run(pipe, [crossed] * 5)

    assert state.emergency_stopped
    assert pipe.emergency_stop.engaged
    assert state.stop_reason is StopReason.GESTURE


def test_stopped_pipeline_ignores_all_gestures():
    pipe = GesturePipeline(cfg())
    pipe.engage_stop(StopReason.API, now=0.0)

    state, events = run(pipe, [pose_point()] * 10, start=1.0)
    assert state.emergency_stopped
    assert events == []
    assert state.gesture is Gesture.NONE


def test_releasing_stop_restores_control():
    pipe = GesturePipeline(cfg())
    pipe.engage_stop(StopReason.API, now=0.0)
    pipe.release_stop()

    state, events = run(pipe, [pose_point()] * 5, start=1.0)
    assert not state.emergency_stopped
    assert any(e.type is EventType.BEGIN for e in events)


def test_paused_mode_blocks_normal_gestures():
    pipe = GesturePipeline(cfg())
    pipe.set_mode(Mode.PAUSED)

    _, events = run(pipe, [pose_point()] * 6)
    assert not any(e.gesture is Gesture.POINT for e in events)


def test_paused_mode_still_accepts_open_palm():
    """Something has to be able to bring the system back."""
    pipe = GesturePipeline(cfg())
    pipe.set_mode(Mode.PAUSED)

    _, events = run(pipe, [pose_open_palm()] * 6)
    assert any(
        e.type is EventType.BEGIN
        and e.gesture in (Gesture.OPEN_PALM, Gesture.PALM_FORWARD)
        for e in events
    )


def test_typing_mode_blocks_fist():
    pipe = GesturePipeline(cfg())
    pipe.set_mode(Mode.TYPING)

    _, events = run(pipe, [pose_fist()] * 6)
    assert not any(e.gesture is Gesture.FIST for e in events)


def test_mode_change_cancels_in_progress_gesture():
    pipe = GesturePipeline(cfg())
    run(pipe, [pose_fist()] * 2)          # building, not yet confirmed
    pipe.set_mode(Mode.TYPING)
    _, events = run(pipe, [pose_fist()] * 2, start=1.0)
    assert not any(e.type is EventType.BEGIN for e in events)


def test_low_light_suppresses_gestures():
    pipe = GesturePipeline(cfg())
    frames = [
        Frame(hands=(pose_point(),), timestamp=i * 0.05, brightness=0.03)
        for i in range(8)
    ]
    events = []
    for f in frames:
        state = pipe.process(f)
        events.extend(state.events)
        assert state.health is TrackingHealth.LOW_LIGHT

    assert not any(e.type is EventType.BEGIN for e in events)


def test_low_confidence_hands_are_reported():
    pipe = GesturePipeline(cfg())
    weak = pose_point(confidence=0.2)
    state = pipe.process(Frame(hands=(weak,), timestamp=0.0))
    assert state.health is TrackingHealth.LOW_CONFIDENCE


def test_hand_loss_ends_active_gesture():
    """Losing tracking mid-gesture must not leave it stuck on."""
    pipe = GesturePipeline(cfg())
    run(pipe, [pose_fist()] * 5)
    assert pipe.debouncer.active_gesture is Gesture.FIST

    # Hand gone, well past the loss threshold.
    state = pipe.process(Frame(hands=(), timestamp=5.0))
    assert pipe.debouncer.active_gesture is None
    assert state.emergency_stopped  # stop_on_hand_loss defaults to True


def test_hand_loss_without_active_gesture_does_not_stop():
    pipe = GesturePipeline(cfg())
    pipe.process(Frame(hands=(pose_point(),), timestamp=0.0))
    state = pipe.process(Frame(hands=(), timestamp=5.0))
    assert not state.emergency_stopped


def test_dominant_hand_drives_the_cursor():
    pipe = GesturePipeline(cfg())
    pipe.config.dominant_hand = Handedness.RIGHT

    both = (
        pose_point(center=(0.3, 0.3), handedness=Handedness.LEFT),
        pose_point(center=(0.7, 0.7), handedness=Handedness.RIGHT),
    )
    state, _ = run(pipe, [both] * 6)
    assert state.cursor is not None


def test_pinch_is_recognised_through_the_pipeline():
    pipe = GesturePipeline(cfg())
    _, events = run(pipe, [pose_pinch(gap=0.08)] * 6)
    assert any(
        e.type is EventType.BEGIN and e.gesture is Gesture.PINCH for e in events
    )


def test_toggle_pause_round_trips():
    pipe = GesturePipeline(cfg())
    pipe.set_mode(Mode.BROWSER)
    assert pipe.toggle_pause() is Mode.PAUSED
    assert pipe.toggle_pause() is Mode.BROWSER


# --- Phase 2: end-to-end through the OS adapter ----------------------------
#
# These drive the whole pipeline with an adapter attached, so they prove the
# wiring from landmarks all the way to OS calls — not just that each module
# works in isolation.

from handgesture.osadapter.null import NullAdapter  # noqa: E402


def rig(**overrides):
    adapter = NullAdapter()
    config = cfg()
    for key, value in overrides.items():
        setattr(config, key, value)
    return adapter, GesturePipeline(config, adapter=adapter)


def test_pipeline_without_adapter_still_resolves_intents():
    """Simulation mode: everything runs, nothing reaches an OS."""
    pipe = GesturePipeline(cfg())
    state, _ = run(pipe, [pose_point()] * 5)
    assert pipe.dispatcher is None
    assert state.cursor is not None


def test_cursor_movement_reaches_the_adapter():
    adapter, pipe = rig()
    run(pipe, [pose_point(center=(0.5, 0.5))] * 5)
    assert adapter.count("move_cursor") > 0


def test_pinch_produces_a_click_through_the_whole_stack():
    adapter, pipe = rig()
    # Pinch, release, then wait out the double-click window.
    run(pipe, [pose_pinch(gap=0.08)] * 5, start=0.0)
    run(pipe, [pose_point()] * 4, start=0.3)
    run(pipe, [pose_point()] * 2, start=1.2)
    assert adapter.count("click") >= 1


def test_peace_produces_a_right_click():
    adapter, pipe = rig()
    run(pipe, [pose_peace()] * 6)
    right = [c for c in adapter.calls if c.action == "click" and c.args == ("right",)]
    assert right


def test_emergency_stop_releases_a_held_button():
    """A stuck mouse button after an emergency stop would be dangerous."""
    adapter, pipe = rig()
    run(pipe, [pose_pinch(gap=0.08)] * 5)
    run(pipe, [pose_pinch(gap=0.08)] * 12, start=0.3)  # long enough to hold
    assert adapter.count("mouse_down") >= 1

    pipe.engage_stop(StopReason.API, now=5.0)
    assert adapter.count("mouse_up") >= 1


def test_stopped_pipeline_sends_nothing_to_the_adapter():
    adapter, pipe = rig()
    pipe.engage_stop(StopReason.API, now=0.0)
    adapter.clear()
    run(pipe, [pose_point()] * 10, start=1.0)
    assert adapter.actions() == []


def test_motion_state_is_exposed():
    adapter, pipe = rig()
    state, _ = run(pipe, [pose_point()] * 4)
    assert state.motion is not None


def test_mode_change_clears_motion_history():
    adapter, pipe = rig()
    run(pipe, [pose_point(center=(0.3, 0.5))] * 4)
    pipe.set_mode(Mode.BROWSER)
    # A single frame far away must not complete the earlier motion.
    state = pipe.process(
        Frame(hands=(pose_point(center=(0.9, 0.5)),), timestamp=10.0)
    )
    assert state.motion.gesture is Gesture.NONE
