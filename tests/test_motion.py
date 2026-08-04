"""Motion gestures: swipes, scroll, zoom, rotation.

The recurring theme: motion detection must separate *intent* from ordinary
hand movement. A hand crossing the frame to reposition is not a swipe, and
these tests pin that distinction down.
"""

from __future__ import annotations

import math

from handgesture.capture.simulation import pose_open_palm, pose_peace, pose_point
from handgesture.gestures.motion import MotionConfig, MotionTracker
from handgesture.gestures.vocabulary import Gesture
from handgesture.types import Frame, Handedness


def sweep(tracker, x0, x1, y0, y1, *, frames=8, duration=0.15, start=0.0, pose=pose_point):
    """Drive the tracker along a straight path.

    Returns the first result that carried a gesture, else the last one. A
    swipe fires as soon as its criteria are met — part-way through the
    motion, not at the end — so checking only the final frame would miss it.
    """
    result = None
    for i in range(frames):
        t = i / (frames - 1)
        hand = pose(center=(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
        result = tracker.update(
            Frame(hands=(hand,), timestamp=start + t * duration)
        )
        if result.gesture is not Gesture.NONE:
            return result
    return result


# --- Swipes ----------------------------------------------------------------

def test_fast_horizontal_sweep_is_a_swipe():
    t = MotionTracker()
    result = sweep(t, 0.3, 0.75, 0.5, 0.5)
    assert result.gesture is Gesture.SWIPE_RIGHT


def test_swipe_direction_follows_travel():
    t = MotionTracker()
    assert sweep(t, 0.75, 0.3, 0.5, 0.5).gesture is Gesture.SWIPE_LEFT

    t2 = MotionTracker()
    assert sweep(t2, 0.5, 0.5, 0.75, 0.3).gesture is Gesture.SWIPE_UP

    t3 = MotionTracker()
    assert sweep(t3, 0.5, 0.5, 0.3, 0.75).gesture is Gesture.SWIPE_DOWN


def test_slow_movement_is_not_a_swipe():
    """Repositioning your hand must not navigate away from your work."""
    t = MotionTracker()
    seen = []
    for i in range(30):
        f = i / 29
        seen.append(
            t.update(
                Frame(
                    hands=(pose_point(center=(0.3 + 0.45 * f, 0.5)),),
                    timestamp=f * 3.0,
                )
            ).gesture
        )
    assert Gesture.SWIPE_RIGHT not in seen


def test_short_movement_is_not_a_swipe():
    t = MotionTracker()
    result = sweep(t, 0.5, 0.53, 0.5, 0.5)
    assert result.gesture is Gesture.NONE


def test_wandering_path_is_not_a_swipe():
    """Straightness matters: a hand milling about covers distance fast."""
    t = MotionTracker()
    seen = []
    xs = [0.4, 0.6, 0.4, 0.6, 0.4, 0.6, 0.45, 0.62]
    for i, x in enumerate(xs):
        seen.append(
            t.update(
                Frame(hands=(pose_point(center=(x, 0.5)),), timestamp=i * 0.02)
            ).gesture
        )
    assert all(g is Gesture.NONE for g in seen), seen


def test_diagonal_movement_is_ambiguous_and_ignored():
    t = MotionTracker()
    result = sweep(t, 0.3, 0.75, 0.3, 0.75)
    assert result.gesture is Gesture.NONE


def test_swipe_cooldown_prevents_double_fire():
    t = MotionTracker()
    assert sweep(t, 0.3, 0.75, 0.5, 0.5).gesture is Gesture.SWIPE_RIGHT
    # Immediately repeating the same motion must not fire again.
    again = sweep(t, 0.3, 0.75, 0.5, 0.5, start=0.2)
    assert again.gesture is not Gesture.SWIPE_RIGHT


def test_swipe_fires_again_after_cooldown():
    t = MotionTracker()
    sweep(t, 0.3, 0.75, 0.5, 0.5)
    later = sweep(t, 0.3, 0.75, 0.5, 0.5, start=2.0)
    assert later.gesture is Gesture.SWIPE_RIGHT


def test_swipe_is_scale_invariant():
    """The same swipe fires whether the hand is near the camera or far.

    Thresholds are measured in palm spans, so the invariant being tested is
    a sweep of constant *hand-widths* — not constant screen distance. A big
    hand near the camera has to travel further in screen terms to make the
    same gesture, which is the intended behaviour.
    """
    for scale in (0.10, 0.18, 0.30):
        t = MotionTracker()
        seen = []
        # Travel 3 palm spans regardless of apparent hand size.
        travel = 3.0 * (scale * 0.85)
        for i in range(8):
            x = 0.5 - travel / 2 + travel * i / 7
            seen.append(
                t.update(
                    Frame(
                        hands=(pose_point(center=(x, 0.5), scale=scale),),
                        timestamp=i * 0.02,
                    )
                ).gesture
            )
        assert Gesture.SWIPE_RIGHT in seen, f"scale={scale}: {seen}"


def test_losing_the_hand_clears_history():
    t = MotionTracker()
    for i in range(4):
        t.update(Frame(hands=(pose_point(center=(0.3 + i * 0.05, 0.5)),), timestamp=i * 0.02))
    t.update(Frame(hands=(), timestamp=0.5))
    # History gone: a single new frame cannot complete the earlier motion.
    result = t.update(Frame(hands=(pose_point(center=(0.9, 0.5)),), timestamp=0.52))
    assert result.gesture is Gesture.NONE


# --- Scroll ----------------------------------------------------------------

def test_two_fingers_moving_produces_scroll():
    t = MotionTracker()
    t.update(Frame(hands=(pose_peace(center=(0.5, 0.5)),), timestamp=0.0))
    result = t.update(
        Frame(hands=(pose_peace(center=(0.5, 0.6)),), timestamp=0.05),
        active_gesture=Gesture.PEACE,
    )
    assert result.gesture is Gesture.SCROLL_VERTICAL
    assert result.scroll[1] != 0.0


def test_scroll_direction_is_inverted_like_a_real_scrollwheel():
    """Moving the hand down scrolls the view up."""
    t = MotionTracker()
    t.update(Frame(hands=(pose_peace(center=(0.5, 0.5)),), timestamp=0.0))
    down = t.update(
        Frame(hands=(pose_peace(center=(0.5, 0.62)),), timestamp=0.05),
        active_gesture=Gesture.PEACE,
    )
    assert down.scroll[1] < 0


def test_tiny_movement_is_below_the_scroll_deadzone():
    t = MotionTracker()
    t.update(Frame(hands=(pose_peace(center=(0.5, 0.5)),), timestamp=0.0))
    result = t.update(
        Frame(hands=(pose_peace(center=(0.5, 0.5005)),), timestamp=0.05),
        active_gesture=Gesture.PEACE,
    )
    assert result.scroll == (0.0, 0.0)


def test_scroll_does_not_fire_swipes():
    """A deliberate scroll must never navigate. Checks every frame, since a
    swipe would fire part-way through rather than at the end."""
    t = MotionTracker()
    seen = []
    for i in range(8):
        seen.append(
            t.update(
                Frame(hands=(pose_peace(center=(0.5, 0.3 + i * 0.06)),), timestamp=i * 0.02),
                active_gesture=Gesture.PEACE,
            ).gesture
        )
    assert Gesture.SWIPE_DOWN not in seen
    assert Gesture.SCROLL_VERTICAL in seen


# --- Zoom ------------------------------------------------------------------

def two_hands(distance, timestamp):
    mid = 0.5
    return Frame(
        hands=(
            pose_open_palm(center=(mid - distance / 2, 0.5), handedness=Handedness.LEFT),
            pose_open_palm(center=(mid + distance / 2, 0.5), handedness=Handedness.RIGHT),
        ),
        timestamp=timestamp,
    )


def test_hands_apart_zooms_in():
    t = MotionTracker()
    t.update(two_hands(0.20, 0.0))
    result = t.update(two_hands(0.40, 0.05))
    assert result.zoom > 0
    assert result.gesture is Gesture.SPREAD


def test_hands_together_zooms_out():
    t = MotionTracker()
    t.update(two_hands(0.40, 0.0))
    result = t.update(two_hands(0.20, 0.05))
    assert result.zoom < 0
    assert result.gesture is Gesture.CONVERGE


def test_steady_hands_do_not_zoom():
    t = MotionTracker()
    t.update(two_hands(0.30, 0.0))
    result = t.update(two_hands(0.30, 0.05))
    assert result.zoom == 0.0


def test_single_hand_produces_no_zoom():
    t = MotionTracker()
    t.update(two_hands(0.30, 0.0))
    result = t.update(Frame(hands=(pose_point(),), timestamp=0.05))
    assert result.zoom == 0.0


# --- Rotation --------------------------------------------------------------

def test_wrist_roll_is_reported():
    t = MotionTracker()
    t.update(Frame(hands=(pose_open_palm(rotation=0.0),), timestamp=0.0))
    result = t.update(Frame(hands=(pose_open_palm(rotation=0.5),), timestamp=0.05))
    assert abs(result.rotate) > 0


def test_steady_wrist_does_not_rotate():
    t = MotionTracker()
    t.update(Frame(hands=(pose_open_palm(rotation=0.3),), timestamp=0.0))
    result = t.update(Frame(hands=(pose_open_palm(rotation=0.3),), timestamp=0.05))
    assert result.rotate == 0.0


def test_rotation_takes_the_short_way_around():
    """Wrapping past pi must not read as a near-full rotation."""
    t = MotionTracker()
    t.update(Frame(hands=(pose_open_palm(rotation=math.pi - 0.05),), timestamp=0.0))
    result = t.update(
        Frame(hands=(pose_open_palm(rotation=-math.pi + 0.05),), timestamp=0.05)
    )
    assert abs(result.rotate) < 1.0


# --- Config ----------------------------------------------------------------

def test_stricter_config_rejects_a_borderline_swipe():
    strict = MotionTracker(MotionConfig(swipe_min_distance=6.0))
    assert sweep(strict, 0.3, 0.75, 0.5, 0.5).gesture is Gesture.NONE
