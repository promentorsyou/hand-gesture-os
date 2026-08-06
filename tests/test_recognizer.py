"""Gesture recognition against synthetic poses.

These are the tests that matter most for Phase 1: they prove the recogniser
separates the poses reliably, without needing a camera or a human hand.
"""

from __future__ import annotations

import pytest

from handgesture.capture.simulation import (
    pose_fist,
    pose_open_palm,
    pose_peace,
    pose_pinch,
    pose_point,
    pose_thumbs_up,
    synth_hand,
)
from handgesture.gestures import features as feat
from handgesture.gestures.recognizer import GestureRecognizer
from handgesture.gestures.vocabulary import Gesture
from handgesture.types import Frame, Handedness


@pytest.fixture
def rec() -> GestureRecognizer:
    return GestureRecognizer(min_confidence=0.5)


# --- Feature extraction ----------------------------------------------------

def test_open_palm_has_all_fingers_extended():
    f = feat.extract(pose_open_palm())
    assert f.extended_fingers >= 4, f.extension
    assert f.curl < 0.3


def test_fist_has_no_fingers_extended():
    f = feat.extract(pose_fist())
    assert f.extended_fingers == 0, f.extension
    assert f.curl > 0.7


def test_point_extends_only_index():
    f = feat.extract(pose_point())
    _, index, middle, ring, pinky = f.extension
    assert index > 0.7
    assert max(middle, ring, pinky) < 0.4


def test_pinch_distance_tracks_the_gap():
    tight = feat.extract(pose_pinch(gap=0.05)).pinch_distance
    loose = feat.extract(pose_pinch(gap=0.9)).pinch_distance
    assert tight < loose
    assert tight < 0.42 < loose


def test_features_are_scale_invariant():
    """The same pose near and far from the camera must read the same."""
    near = feat.extract(pose_point(scale=0.30))
    far = feat.extract(pose_point(scale=0.10))
    for a, b in zip(near.extension, far.extension, strict=True):
        assert a == pytest.approx(b, abs=0.05)
    assert near.pinch_distance == pytest.approx(far.pinch_distance, abs=0.05)


def test_features_are_translation_invariant():
    a = feat.extract(pose_point(center=(0.2, 0.3)))
    b = feat.extract(pose_point(center=(0.8, 0.7)))
    for x, y in zip(a.extension, b.extension, strict=True):
        assert x == pytest.approx(y, abs=0.05)


# --- Recognition -----------------------------------------------------------

@pytest.mark.parametrize(
    "pose_fn,expected",
    [
        (pose_point, Gesture.POINT),
        (pose_fist, Gesture.FIST),
        (pose_peace, Gesture.PEACE),
        (pose_thumbs_up, Gesture.THUMBS_UP),
    ],
)
def test_recognises_distinct_poses(rec, pose_fn, expected):
    result = rec.recognize_hand(pose_fn())
    assert result.gesture is expected, f"got {result.gesture} scores={result.scores}"
    assert result.confidence >= 0.5


def test_recognises_pinch(rec):
    result = rec.recognize_hand(pose_pinch(gap=0.08))
    assert result.gesture is Gesture.PINCH
    assert result.confidence >= 0.5


def test_open_palm_recognised(rec):
    result = rec.recognize_hand(pose_open_palm())
    # PALM_FORWARD is a stricter superset, so either is a correct read here.
    assert result.gesture in (Gesture.OPEN_PALM, Gesture.PALM_FORWARD)


def test_empty_frame_yields_none(rec):
    assert rec.recognize(Frame()).gesture is Gesture.NONE


def test_recognition_is_scale_invariant(rec):
    for scale in (0.10, 0.18, 0.30):
        assert rec.recognize_hand(pose_point(scale=scale)).gesture is Gesture.POINT


def test_recognition_survives_rotation(rec):
    """A pointing hand tilted either way is still pointing."""
    for rotation in (-0.5, -0.2, 0.0, 0.2, 0.5):
        result = rec.recognize_hand(pose_point(rotation=rotation))
        assert result.gesture is Gesture.POINT, f"rotation={rotation}"


def test_ambiguous_pose_is_rejected(rec):
    """A partly-curled hand matches nothing well and must not fire.

    At curl 0.2 the fingers sit mid-way between extended and folded, which
    is exactly the transient state a hand passes through on the way to a
    real pose — precisely what must not trigger a command.
    """
    mush = synth_hand(curl=(0.2, 0.2, 0.2, 0.2, 0.2))
    assert rec.recognize_hand(mush).gesture is Gesture.NONE


def test_pinch_is_not_confused_with_point(rec):
    """Point and pinch share a finger pattern; only the thumb gap differs.

    Getting this wrong means every attempt to click just moves the cursor.
    """
    assert rec.recognize_hand(pose_pinch(gap=0.08)).gesture is Gesture.PINCH
    assert rec.recognize_hand(pose_point()).gesture is Gesture.POINT


def test_confidence_is_lower_for_sloppy_poses(rec):
    clean = rec.recognize_hand(pose_point())
    sloppy = rec.recognize_hand(synth_hand(curl=(0.7, 0.15, 0.75, 0.8, 0.8)))
    assert clean.confidence >= sloppy.confidence


# --- Two-hand --------------------------------------------------------------

def test_crossed_hands_detected(rec):
    """Emergency stop: hands swapped across the body midline."""
    left = pose_open_palm(center=(0.65, 0.5), handedness=Handedness.LEFT)
    right = pose_open_palm(center=(0.35, 0.5), handedness=Handedness.RIGHT)
    # Crossed means the right hand has moved to the left hand's side.
    crossed = Frame(hands=(left, right))
    left_x = crossed.hand_by(Handedness.LEFT).palm_center.x
    right_x = crossed.hand_by(Handedness.RIGHT).palm_center.x
    assert right_x < left_x  # sanity: this is the *uncrossed* arrangement

    swapped = Frame(
        hands=(
            pose_open_palm(center=(0.35, 0.5), handedness=Handedness.LEFT),
            pose_open_palm(center=(0.60, 0.5), handedness=Handedness.RIGHT),
        )
    )
    assert rec.recognize(swapped).gesture is Gesture.CROSSED_HANDS


def test_uncrossed_hands_are_not_emergency_stop(rec):
    frame = Frame(
        hands=(
            pose_open_palm(center=(0.70, 0.5), handedness=Handedness.LEFT),
            pose_open_palm(center=(0.30, 0.5), handedness=Handedness.RIGHT),
        )
    )
    assert rec.recognize(frame).gesture is not Gesture.CROSSED_HANDS


def test_far_apart_hands_do_not_cross(rec):
    """Swapped but far apart is two people / noise, not a deliberate cross."""
    frame = Frame(
        hands=(
            pose_open_palm(center=(0.05, 0.5), handedness=Handedness.LEFT),
            pose_open_palm(center=(0.95, 0.5), handedness=Handedness.RIGHT),
        )
    )
    assert rec.recognize(frame).gesture is not Gesture.CROSSED_HANDS
