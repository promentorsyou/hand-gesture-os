"""Smoothing filters and cursor mapping."""

from __future__ import annotations

import random

import pytest

from handgesture.control.cursor import CursorConfig, CursorController
from handgesture.tracking.filters import OneEuroFilter, PointFilter
from handgesture.types import Point

# --- One Euro filter -------------------------------------------------------

def test_filter_passes_first_sample_through():
    f = OneEuroFilter()
    assert f.filter(0.5, 0.0) == 0.5


def test_filter_suppresses_jitter_on_a_still_hand():
    """The core reason this filter exists: a still hand must read as still."""
    random.seed(7)
    f = OneEuroFilter(min_cutoff=0.5, beta=0.001)

    outputs = []
    for i in range(120):
        noisy = 0.5 + random.uniform(-0.02, 0.02)
        outputs.append(f.filter(noisy, i / 60.0))

    settled = outputs[40:]
    spread = max(settled) - min(settled)
    assert spread < 0.012, f"jitter not suppressed: spread={spread}"


def test_filter_tracks_fast_movement_without_excessive_lag():
    """Smoothing must not turn into lag when the hand actually moves."""
    f = OneEuroFilter(min_cutoff=1.0, beta=0.7)
    out = 0.0
    for i in range(60):
        target = i / 60.0
        out = f.filter(target, i / 60.0)
    assert out == pytest.approx(59 / 60.0, abs=0.08)


def test_filter_handles_non_monotonic_time():
    """A clock that jumps backwards must not blow up or divide by zero."""
    f = OneEuroFilter()
    f.filter(0.5, 1.0)
    assert f.filter(0.7, 0.5) == 0.7  # seeds rather than crashing


def test_reset_clears_history():
    f = OneEuroFilter()
    f.filter(0.9, 0.0)
    f.filter(0.9, 0.1)
    f.reset()
    assert f.filter(0.1, 0.0) == 0.1


def test_point_filter_smooths_all_axes():
    pf = PointFilter()
    pf.filter(Point(0.5, 0.5, 0.0), 0.0)
    out = pf.filter(Point(0.9, 0.1, 0.3), 0.1)
    assert 0.5 <= out.x <= 0.9
    assert 0.1 <= out.y <= 0.5


# --- Cursor mapping --------------------------------------------------------

def test_cursor_starts_centered():
    c = CursorController(CursorConfig(screen_width=1920, screen_height=1080))
    assert c.position == (960.0, 540.0)


def test_active_region_maps_to_full_screen():
    """Reaching the edge of the comfortable region must reach the screen edge.

    Without this you would have to move your hand out of frame to click
    anything in a corner.
    """
    cfg = CursorConfig(screen_width=1000, screen_height=1000, margin=0.2, mirror_x=False)
    c = CursorController(cfg)

    c.update(Point(0.2, 0.2), 0.0)  # top-left of the active region
    x, y = c.position
    assert x == pytest.approx(0.0, abs=1.0)
    assert y == pytest.approx(0.0, abs=1.0)

    c2 = CursorController(cfg)
    c2.update(Point(0.8, 0.8), 0.0)  # bottom-right
    x, y = c2.position
    assert x == pytest.approx(1000.0, abs=1.0)
    assert y == pytest.approx(1000.0, abs=1.0)


def test_cursor_clamps_outside_the_active_region():
    cfg = CursorConfig(screen_width=1000, screen_height=1000, margin=0.2, mirror_x=False)
    c = CursorController(cfg)
    state = c.update(Point(0.02, 0.02), 0.0)
    assert state.clamped
    assert c.position == (0.0, 0.0)


def test_mirroring_matches_a_mirrored_selfview():
    """Moving your hand right must move the cursor right on a mirrored feed."""
    cfg = CursorConfig(screen_width=1000, screen_height=1000, margin=0.0, mirror_x=True)
    c = CursorController(cfg)
    c.update(Point(0.9, 0.5), 0.0)
    assert c.position[0] == pytest.approx(100.0, abs=1.0)


def test_cursor_stays_on_screen():
    cfg = CursorConfig(screen_width=800, screen_height=600, margin=0.1)
    c = CursorController(cfg)
    random.seed(3)
    for i in range(200):
        c.update(Point(random.random(), random.random()), i / 30.0)
        x, y = c.position
        assert 0.0 <= x <= 800.0
        assert 0.0 <= y <= 600.0


def test_precision_mode_reduces_movement():
    """Same hand travel must cover less screen distance in precision mode."""
    cfg = CursorConfig(screen_width=1000, screen_height=1000, margin=0.1)

    normal = CursorController(cfg)
    normal.update(Point(0.5, 0.5), 0.0)
    normal.update(Point(0.6, 0.5), 0.1)
    normal_travel = abs(normal.position[0] - 500.0)

    fine = CursorController(cfg)
    fine.update(Point(0.5, 0.5), 0.0)
    fine.set_precision(True)
    fine.update(Point(0.6, 0.5), 0.1)
    fine_travel = abs(fine.position[0] - 500.0)

    assert fine_travel < normal_travel


def test_center_recenters_and_resets():
    c = CursorController(CursorConfig(screen_width=1000, screen_height=1000))
    c.update(Point(0.9, 0.9), 0.0)
    c.center()
    assert c.position == (500.0, 500.0)
