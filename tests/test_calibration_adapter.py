"""Calibration wizard and the OS adapter abstraction."""

from __future__ import annotations

import pytest

from handgesture.calibration.calibrator import (
    SAMPLES_PER_STEP,
    CalibrationProfile,
    CalibrationStep,
    Calibrator,
)
from handgesture.capture.simulation import (
    pose_fist,
    pose_open_palm,
    pose_pinch,
)
from handgesture.osadapter.base import Capability, MouseButton, get_adapter
from handgesture.osadapter.null import NullAdapter
from handgesture.types import Handedness

# --- Calibration -----------------------------------------------------------

def test_calibrator_starts_idle():
    c = Calibrator()
    assert c.step is CalibrationStep.IDLE
    assert not c.active


def test_start_enters_first_step():
    c = Calibrator()
    assert c.start() is CalibrationStep.OPEN_HAND
    assert c.active
    assert c.prompt


def test_step_advances_once_full():
    c = Calibrator()
    c.start()
    for _ in range(SAMPLES_PER_STEP):
        c.add_sample(pose_open_palm())
    assert c.step is CalibrationStep.CLOSED_FIST


def test_full_run_completes():
    c = Calibrator()
    c.start()
    poses = {
        CalibrationStep.OPEN_HAND: lambda: pose_open_palm(),
        CalibrationStep.CLOSED_FIST: lambda: pose_fist(),
        CalibrationStep.PINCH_CLOSED: lambda: pose_pinch(gap=0.05),
        CalibrationStep.PINCH_OPEN: lambda: pose_pinch(gap=1.1),
        CalibrationStep.REACH_BOUNDS: lambda: pose_open_palm(center=(0.25, 0.3)),
    }
    guard = 0
    while c.active and guard < 500:
        c.add_sample(poses[c.step]())
        guard += 1

    assert c.step is CalibrationStep.COMPLETE
    assert c.progress == 1.0


def test_profile_threshold_sits_between_measured_extremes():
    """The whole point of calibration: use *this* user's pinch range."""
    c = Calibrator()
    c.start()
    for _ in range(SAMPLES_PER_STEP):
        c.add_sample(pose_open_palm())
    for _ in range(SAMPLES_PER_STEP):
        c.add_sample(pose_fist())

    closed = pose_pinch(gap=0.05)
    opened = pose_pinch(gap=1.1)
    for _ in range(SAMPLES_PER_STEP):
        c.add_sample(closed)
    for _ in range(SAMPLES_PER_STEP):
        c.add_sample(opened)
    for i in range(SAMPLES_PER_STEP):
        c.add_sample(pose_open_palm(center=(0.2 + i * 0.02, 0.3 + i * 0.01)))

    profile = c.build_profile()
    assert profile.calibrated

    from handgesture.gestures import features as feat

    lo = feat.extract(closed).pinch_distance
    hi = feat.extract(opened).pinch_distance
    assert lo < profile.pinch_threshold < hi


def test_partial_calibration_still_yields_usable_profile():
    c = Calibrator()
    c.start()
    for _ in range(5):
        c.add_sample(pose_open_palm())

    profile = c.build_profile()
    assert not profile.calibrated
    assert 0.0 < profile.pinch_threshold < 2.0


def test_reach_bounds_recorded():
    c = Calibrator()
    c.start()
    # Bounded rather than `while`: a wizard that fails to advance is a real
    # bug, and it should fail the test rather than hang the suite.
    for _ in range(SAMPLES_PER_STEP * len(CalibrationStep) + 10):
        if c.step is CalibrationStep.REACH_BOUNDS:
            break
        c.add_sample(pose_open_palm())
    assert c.step is CalibrationStep.REACH_BOUNDS, "wizard failed to advance"

    for x, y in [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)] * 5:
        c.add_sample(pose_open_palm(center=(x, y)))

    p = c.build_profile()
    assert p.reach_min_x < 0.4 < p.reach_max_x
    assert p.reach_min_y < 0.4 < p.reach_max_y


def test_profile_round_trips_through_dict():
    p = CalibrationProfile(
        pinch_threshold=0.31, dominant_hand=Handedness.LEFT, calibrated=True
    )
    restored = CalibrationProfile.from_dict(p.to_dict())
    assert restored.pinch_threshold == pytest.approx(0.31)
    assert restored.dominant_hand is Handedness.LEFT
    assert restored.calibrated


def test_cancel_returns_to_idle():
    c = Calibrator()
    c.start()
    c.add_sample(pose_open_palm())
    c.cancel()
    assert c.step is CalibrationStep.IDLE


# --- OS adapter ------------------------------------------------------------

def test_null_adapter_supports_everything():
    a = NullAdapter()
    for cap in Capability:
        assert a.supports(cap)


def test_null_adapter_records_without_acting():
    a = NullAdapter()
    a.move_cursor(100, 200)
    a.click(MouseButton.LEFT)
    a.scroll(0, 3)

    assert a.actions() == ["move_cursor", "click", "scroll"]
    assert a.cursor_position() == (100, 200)


def test_null_adapter_click_records_button_and_count():
    a = NullAdapter()
    a.click(MouseButton.RIGHT, count=2)
    call = a.last("click")
    assert call.args == ("right",)
    assert call.kwargs["count"] == 2


def test_clipboard_helpers_emit_the_right_chord():
    a = NullAdapter()
    a.copy()
    a.paste()
    assert a.last("press_keys").args == ("ctrl", "v")
    assert a.count("press_keys") == 2


def test_volume_is_clamped():
    a = NullAdapter()
    a.set_volume(5.0)
    assert a.get_volume() == 1.0
    a.set_volume(-1.0)
    assert a.get_volume() == 0.0


def test_adapter_clear_resets_the_log():
    a = NullAdapter()
    a.move_cursor(1, 1)
    a.clear()
    assert a.actions() == []


def test_get_adapter_falls_back_to_null_for_unknown_platform():
    """An unsupported OS must degrade to simulation, not crash on startup."""
    adapter = get_adapter("plan9")
    assert isinstance(adapter, NullAdapter)


def test_get_adapter_returns_something_on_this_host():
    """Whatever the host is, we must always get a usable adapter."""
    assert get_adapter() is not None
