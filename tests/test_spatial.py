"""The spatial interface: windows, grabs, overlays, multitasking.

Every test here runs headlessly. That is the point of keeping the workspace
a pure state machine — the parts a container cannot exercise (real OS
windows) are deliberately not in this layer.
"""

from __future__ import annotations

import pytest

from handgesture.gestures.debounce import EventType, GestureEvent
from handgesture.gestures.motion import MotionResult
from handgesture.gestures.vocabulary import Gesture, Mode, gesture_allowed
from handgesture.pipeline import PipelineState
from handgesture.spatial import (
    GrabKind,
    Overlay,
    Rect,
    SpatialController,
    Window,
    WindowState,
    Workspace,
)
from handgesture.spatial.window import (
    MIN_HEIGHT,
    MIN_WIDTH,
    RESIZE_HANDLE,
    TITLE_BAR_HEIGHT,
)
from handgesture.types import Point


def p(x, y):
    return Point(x, y)


# --- Rect ------------------------------------------------------------------

def test_rect_contains_its_own_area():
    r = Rect(0.2, 0.2, 0.4, 0.3)
    assert r.contains(p(0.3, 0.3))
    assert not r.contains(p(0.7, 0.3))
    assert not r.contains(p(0.3, 0.6))


def test_clamped_keeps_the_title_bar_reachable():
    """A window dragged past the top would be impossible to drag back."""
    pushed_up = Rect(0.2, -0.5, 0.3, 0.3).clamped()
    assert pushed_up.y >= 0.0

    pushed_down = Rect(0.2, 5.0, 0.3, 0.3).clamped()
    assert pushed_down.y <= 1.0 - TITLE_BAR_HEIGHT


def test_clamped_allows_horizontal_overhang_but_keeps_a_handle():
    far_right = Rect(9.0, 0.3, 0.4, 0.3).clamped()
    assert far_right.x <= 1.0 - RESIZE_HANDLE
    far_left = Rect(-9.0, 0.3, 0.4, 0.3).clamped()
    assert far_left.right == pytest.approx(RESIZE_HANDLE)


def test_resize_respects_the_minimum():
    small = Rect(0.1, 0.1, 0.5, 0.5).resized(0.001, 0.001)
    assert small.width == pytest.approx(MIN_WIDTH)
    assert small.height == pytest.approx(MIN_HEIGHT)


# --- Window ----------------------------------------------------------------

def test_title_bar_grab_moves_and_body_grab_does_not():
    w = Window(app="files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    assert w.grab_kind(p(0.3, 0.21)) is GrabKind.MOVE
    assert w.grab_kind(p(0.3, 0.40)) is GrabKind.NONE


def test_corner_grab_resizes_even_though_it_is_inside_the_body():
    w = Window(app="files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    assert w.grab_kind(p(0.59, 0.59)) is GrabKind.RESIZE


def test_grab_outside_the_window_is_nothing():
    w = Window(app="files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    assert w.grab_kind(p(0.9, 0.9)) is GrabKind.NONE


def test_maximize_and_restore_round_trip():
    w = Window(app="files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    original = w.rect
    w.maximize()
    assert w.state is WindowState.MAXIMIZED
    assert w.rect == Rect(0.0, 0.0, 1.0, 1.0)
    w.restore()
    assert w.state is WindowState.NORMAL
    assert w.rect == original


def test_maximizing_twice_does_not_lose_the_restore_rect():
    """The classic bug: the second maximise overwrites the saved size."""
    w = Window(app="files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    original = w.rect
    w.maximize()
    w.maximize()
    w.restore()
    assert w.rect == original


def test_maximized_window_cannot_be_grabbed():
    w = Window(app="files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    w.maximize()
    assert w.grab_kind(p(0.5, 0.01)) is GrabKind.NONE


def test_minimize_hides_and_restore_brings_it_back():
    w = Window(app="files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    w.minimize()
    assert not w.visible
    w.restore()
    assert w.visible
    assert w.rect == Rect(0.2, 0.2, 0.4, 0.4)


# --- Workspace: lifecycle and focus ----------------------------------------

def test_opening_windows_cascades_and_focuses_the_newest():
    ws = Workspace()
    a = ws.open("files")
    b = ws.open("browser")
    assert ws.focused is b
    assert a.rect != b.rect


def test_window_at_returns_the_topmost():
    ws = Workspace()
    rect = Rect(0.2, 0.2, 0.4, 0.4)
    ws.open("files", rect=rect)
    top = ws.open("browser", rect=rect)
    assert ws.window_at(p(0.3, 0.3)) is top


def test_focus_raises_to_the_front():
    ws = Workspace()
    rect = Rect(0.2, 0.2, 0.4, 0.4)
    under = ws.open("files", rect=rect)
    ws.open("browser", rect=rect)
    ws.focus(under.id)
    assert ws.window_at(p(0.3, 0.3)) is under


def test_minimized_windows_are_not_hit_tested():
    ws = Workspace()
    w = ws.open("files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    ws.minimize(w.id)
    assert ws.window_at(p(0.3, 0.3)) is None


def test_focusing_a_minimized_window_restores_it():
    ws = Workspace()
    w = ws.open("files")
    ws.minimize(w.id)
    ws.focus(w.id)
    assert w.state is WindowState.NORMAL


def test_closing_the_focused_window_moves_focus_to_the_next():
    ws = Workspace()
    a = ws.open("files")
    b = ws.open("browser")
    assert ws.request_close(b.id).closed
    assert ws.focused is a


def test_closing_the_last_window_leaves_no_focus():
    ws = Workspace()
    w = ws.open("files")
    ws.request_close(w.id)
    assert ws.focused is None


def test_unsaved_work_is_never_closed_without_confirmation():
    """Closing unsaved work is on the sensitive-operations list."""
    ws = Workspace()
    w = ws.open("editor", unsaved=True)
    result = ws.request_close(w.id)
    assert not result.closed
    assert result.needs_confirmation
    assert ws.get(w.id) is w


def test_confirmed_close_of_unsaved_work_goes_through():
    ws = Workspace()
    w = ws.open("editor", unsaved=True)
    ws.request_close(w.id)
    assert ws.force_close(w.id)
    assert ws.get(w.id) is None


def test_cycle_walks_apps_in_open_order_and_wraps():
    ws = Workspace()
    a = ws.open("files")
    b = ws.open("browser")
    c = ws.open("music")
    assert ws.focused is c
    assert ws.cycle(1) is a       # wraps
    assert ws.cycle(1) is b
    assert ws.cycle(-1) is a


def test_cycle_with_no_windows_does_nothing():
    assert Workspace().cycle(1) is None


# --- Workspace: grab and resize --------------------------------------------

def test_grabbing_the_title_bar_moves_the_window():
    ws = Workspace()
    w = ws.open("files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    assert ws.begin_grab(p(0.30, 0.21)) is GrabKind.MOVE
    ws.update_grab(p(0.40, 0.31))
    assert w.rect.x == pytest.approx(0.30)
    assert w.rect.y == pytest.approx(0.30)


def test_grab_deltas_are_absolute_not_accumulated():
    """Moving out and back must land exactly where it started."""
    ws = Workspace()
    w = ws.open("files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    ws.begin_grab(p(0.30, 0.21))
    for x in (0.4, 0.5, 0.6, 0.45, 0.30):
        ws.update_grab(p(x, 0.21))
    assert w.rect.x == pytest.approx(0.2)


def test_grabbing_the_corner_resizes():
    ws = Workspace()
    w = ws.open("files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    assert ws.begin_grab(p(0.59, 0.59)) is GrabKind.RESIZE
    ws.update_grab(p(0.69, 0.69))
    assert w.rect.width == pytest.approx(0.5)
    assert w.rect.height == pytest.approx(0.5)
    assert w.rect.x == pytest.approx(0.2)  # anchored at the top-left


def test_grabbing_the_body_focuses_without_moving():
    ws = Workspace()
    rect = Rect(0.2, 0.2, 0.4, 0.4)
    under = ws.open("files", rect=rect)
    ws.open("browser", rect=Rect(0.6, 0.6, 0.3, 0.3))
    assert ws.begin_grab(p(0.30, 0.40)) is GrabKind.NONE
    assert not ws.grabbing
    assert ws.focused is under
    assert under.rect == rect


def test_a_grab_cannot_push_a_window_out_of_reach():
    ws = Workspace()
    w = ws.open("files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    ws.begin_grab(p(0.30, 0.21))
    ws.update_grab(p(0.30, 9.0))
    assert w.rect.y <= 1.0 - TITLE_BAR_HEIGHT


def test_ending_a_grab_stops_tracking_the_hand():
    ws = Workspace()
    w = ws.open("files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    ws.begin_grab(p(0.30, 0.21))
    ws.end_grab()
    assert not ws.grabbing
    ws.update_grab(p(0.90, 0.90))
    assert w.rect.x == pytest.approx(0.2)


def test_closing_a_grabbed_window_clears_the_grab():
    ws = Workspace()
    w = ws.open("files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    ws.begin_grab(p(0.30, 0.21))
    ws.force_close(w.id)
    assert not ws.grabbing
    assert ws.update_grab(p(0.5, 0.5)) is None


def test_maximizing_a_grabbed_window_clears_the_grab():
    ws = Workspace()
    w = ws.open("files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    ws.begin_grab(p(0.30, 0.21))
    ws.maximize(w.id)
    assert not ws.grabbing


# --- Workspace: overlays, cards, notifications -----------------------------

def test_overlays_toggle():
    ws = Workspace()
    assert ws.toggle_overlay(Overlay.HOME) is Overlay.HOME
    assert ws.toggle_overlay(Overlay.HOME) is Overlay.NONE


def test_showing_an_overlay_cancels_a_grab():
    ws = Workspace()
    ws.open("files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    ws.begin_grab(p(0.30, 0.21))
    ws.show_home()
    assert not ws.grabbing


def test_multitask_cards_are_most_recent_first():
    ws = Workspace()
    ws.open("files")
    ws.open("browser")
    music = ws.open("music")
    cards = ws.multitask_cards()
    assert [c.app for c in cards] == ["music", "browser", "files"]
    assert cards[0].focused
    assert cards[0].window_id == music.id


def test_selecting_a_card_focuses_it_and_dismisses_the_deck():
    ws = Workspace()
    files = ws.open("files")
    ws.open("browser")
    ws.show_multitask()
    assert ws.select_card(files.id) is files
    assert ws.overlay is Overlay.NONE


def test_minimized_windows_still_appear_as_cards():
    """You need a way back to a minimised app."""
    ws = Workspace()
    w = ws.open("files")
    ws.minimize(w.id)
    assert any(c.window_id == w.id for c in ws.multitask_cards())


def test_notifications_stack_newest_first_and_count_unread():
    ws = Workspace()
    ws.notify("mail", "One")
    ws.notify("chat", "Two")
    assert ws.notifications[0].title == "Two"
    assert ws.unread_count == 2
    assert ws.mark_all_read() == 2
    assert ws.unread_count == 0


def test_muting_notifications_drops_them():
    ws = Workspace()
    ws.toggle_setting("notifications")
    assert ws.notify("mail", "One") is None
    assert ws.unread_count == 0


def test_notification_history_is_bounded():
    ws = Workspace()
    for i in range(ws.max_notifications * 3):
        ws.notify("mail", f"n{i}")
    assert len(ws.notifications) == ws.max_notifications


def test_unknown_quick_setting_is_rejected():
    assert Workspace().toggle_setting("warp_drive") is None


def test_snapshot_is_serialisable_and_ordered_back_to_front():
    ws = Workspace()
    ws.open("files")
    top = ws.open("browser")
    ws.notify("mail", "hi")
    snap = ws.snapshot()
    assert snap["windows"][-1]["id"] == top.id
    assert snap["focused"] == top.id
    assert snap["unread"] == 1
    assert snap["quick_settings"]["gesture_control"] is True


# --- Controller ------------------------------------------------------------

def begin(gesture):
    return GestureEvent(type=EventType.BEGIN, gesture=gesture, confidence=1.0, timestamp=0.0)


def end(gesture):
    return GestureEvent(type=EventType.END, gesture=gesture, confidence=1.0, timestamp=0.0)


class FakeCursor:
    def __init__(self, x, y):
        self.x, self.y = x, y


def st(*, events=(), gesture=Gesture.NONE, cursor=None, motion=None,
       mode=Mode.WINDOW, stopped=False):
    state = PipelineState(mode=mode)
    state.events = list(events)
    state.gesture = gesture
    state.motion = motion or MotionResult()
    state.emergency_stopped = stopped
    if cursor is not None:
        state.cursor = FakeCursor(cursor[0] * 1920, cursor[1] * 1080)
    return state


def controller_with_window():
    ws = Workspace()
    w = ws.open("files", rect=Rect(0.2, 0.2, 0.4, 0.4))
    return SpatialController(ws), ws, w


def test_fist_on_the_title_bar_begins_a_move():
    ctl, ws, w = controller_with_window()
    actions = ctl.handle(st(events=[begin(Gesture.FIST)], gesture=Gesture.FIST,
                            cursor=(0.30, 0.21)))
    assert [a.name for a in actions] == ["grab_begin"]
    assert ws.grabbing


def test_held_fist_moves_the_window_and_releasing_stops():
    ctl, ws, w = controller_with_window()
    ctl.handle(st(events=[begin(Gesture.FIST)], gesture=Gesture.FIST, cursor=(0.30, 0.21)))
    ctl.handle(st(gesture=Gesture.FIST, cursor=(0.45, 0.36)))
    assert w.rect.x == pytest.approx(0.35)

    ctl.handle(st(events=[end(Gesture.FIST)], cursor=(0.45, 0.36)))
    assert not ws.grabbing
    ctl.handle(st(gesture=Gesture.FIST, cursor=(0.90, 0.90)))
    assert w.rect.x == pytest.approx(0.35)


def test_emergency_stop_drops_a_held_window():
    """The spatial equivalent of the stuck mouse button from Phase 2."""
    ctl, ws, w = controller_with_window()
    ctl.handle(st(events=[begin(Gesture.FIST)], gesture=Gesture.FIST, cursor=(0.30, 0.21)))
    actions = ctl.handle(st(stopped=True))
    assert not ws.grabbing
    assert actions[0].name == "grab_end"
    assert actions[0].detail == "emergency_stop"


def test_leaving_window_mode_drops_a_held_window():
    ctl, ws, w = controller_with_window()
    ctl.handle(st(events=[begin(Gesture.FIST)], gesture=Gesture.FIST, cursor=(0.30, 0.21)))
    ctl.handle(st(mode=Mode.NAVIGATION))
    assert not ws.grabbing


def test_the_controller_ignores_states_from_other_modes():
    ctl, ws, w = controller_with_window()
    ctl.handle(st(events=[begin(Gesture.FIST)], gesture=Gesture.FIST,
                  cursor=(0.30, 0.21), mode=Mode.BROWSER))
    assert not ws.grabbing


def test_spread_maximizes_and_converge_restores():
    ctl, ws, w = controller_with_window()
    ctl.handle(st(motion=MotionResult(gesture=Gesture.SPREAD, zoom=0.4)))
    assert w.state is WindowState.MAXIMIZED
    ctl.handle(st(motion=MotionResult(gesture=Gesture.CONVERGE, zoom=-0.4)))
    assert w.state is WindowState.NORMAL


def test_a_small_zoom_does_not_flip_window_state():
    ctl, ws, w = controller_with_window()
    ctl.handle(st(motion=MotionResult(gesture=Gesture.SPREAD, zoom=0.06)))
    assert w.state is WindowState.NORMAL


def test_zoom_is_ignored_while_a_window_is_grabbed():
    ctl, ws, w = controller_with_window()
    ctl.handle(st(events=[begin(Gesture.FIST)], gesture=Gesture.FIST, cursor=(0.30, 0.21)))
    ctl.handle(st(gesture=Gesture.FIST, cursor=(0.30, 0.21),
                  motion=MotionResult(gesture=Gesture.SPREAD, zoom=0.4)))
    assert w.state is WindowState.NORMAL


def test_swipe_up_shows_the_multitask_deck():
    ctl, ws, w = controller_with_window()
    ctl.handle(st(motion=MotionResult(gesture=Gesture.SWIPE_UP)))
    assert ws.overlay is Overlay.MULTITASK


def test_swipe_down_minimizes_the_focused_window():
    ctl, ws, w = controller_with_window()
    ctl.handle(st(motion=MotionResult(gesture=Gesture.SWIPE_DOWN)))
    assert w.state is WindowState.MINIMIZED


def test_horizontal_swipes_switch_apps():
    ws = Workspace()
    a = ws.open("files")
    ws.open("browser")
    ctl = SpatialController(ws)
    actions = ctl.handle(st(motion=MotionResult(gesture=Gesture.SWIPE_RIGHT)))
    assert ws.focused is a
    assert actions[0].name == "switch_app"


def test_palm_forward_toggles_the_home_screen():
    ctl, ws, w = controller_with_window()
    ctl.handle(st(events=[begin(Gesture.PALM_FORWARD)]))
    assert ws.overlay is Overlay.HOME
    ctl.handle(st(events=[begin(Gesture.PALM_FORWARD)]))
    assert ws.overlay is Overlay.NONE


def test_peace_toggles_quick_settings():
    ctl, ws, w = controller_with_window()
    ctl.handle(st(events=[begin(Gesture.PEACE)]))
    assert ws.overlay is Overlay.QUICK_SETTINGS


def test_window_mode_allows_the_phase_3_gestures():
    for gesture in (
        Gesture.SWIPE_LEFT,
        Gesture.SWIPE_RIGHT,
        Gesture.PALM_FORWARD,
        Gesture.PEACE,
        Gesture.FIST,
    ):
        assert gesture_allowed(Mode.WINDOW, gesture), gesture


def test_close_through_the_controller_still_needs_confirmation():
    ws = Workspace()
    w = ws.open("editor", unsaved=True)
    ctl = SpatialController(ws)
    assert ctl.request_close(w.id).needs_confirmation


# --- End to end: real landmarks all the way to a moved window --------------


def test_a_real_fist_moves_a_real_window_through_the_whole_stack():
    """No hand-built PipelineState here — synthetic landmarks in, window moved out.

    This is the test that would have caught the pointer not following a
    fist: in navigation mode a fist deliberately parks the cursor, so
    without the window-mode exception the grab has no position to use.
    """
    from handgesture.capture.simulation import pose_fist
    from handgesture.gestures.debounce import DebounceConfig
    from handgesture.pipeline import GesturePipeline, PipelineConfig
    from handgesture.types import Frame

    config = PipelineConfig(
        debounce=DebounceConfig(hold_frames=2, hold_seconds=0.02, cooldown_seconds=0.1)
    )
    pipe = GesturePipeline(config)
    pipe.set_mode(Mode.WINDOW)
    ctl = SpatialController(
        screen_width=config.cursor.screen_width,
        screen_height=config.cursor.screen_height,
    )
    # Where a centred fist actually puts the cursor: the palm centre sits
    # above the frame centre, and the active-region inset magnifies that.
    # The title bar is placed to straddle the measured landing point.
    window = ctl.workspace.open("files", rect=Rect(0.35, 0.32, 0.4, 0.4))
    origin = window.rect.x

    grabbed = moved = False
    cursor_seen = False
    for i in range(16):
        state = pipe.process(
            Frame(hands=(pose_fist(center=(0.5 - i * 0.01, 0.5)),), timestamp=i * 0.05)
        )
        cursor_seen = cursor_seen or state.cursor is not None
        for action in ctl.handle(state):
            grabbed = grabbed or action.name == "grab_begin"
            moved = moved or action.name == "window_move"

    assert cursor_seen, "a fist must drive the cursor in window mode"
    assert grabbed, "the fist never grabbed the title bar"
    assert moved and window.rect.x != origin, "the window did not follow the hand"


def test_a_fist_in_navigation_mode_still_parks_the_cursor():
    """The window-mode exception must not leak into ordinary pointing."""
    from handgesture.capture.simulation import pose_fist, pose_point
    from handgesture.gestures.debounce import DebounceConfig
    from handgesture.pipeline import GesturePipeline, PipelineConfig
    from handgesture.types import Frame

    pipe = GesturePipeline(
        PipelineConfig(
            debounce=DebounceConfig(hold_frames=2, hold_seconds=0.02, cooldown_seconds=0.1)
        )
    )
    for i in range(5):
        pipe.process(Frame(hands=(pose_point(center=(0.5, 0.5)),), timestamp=i * 0.05))
    parked = pipe.cursor.position
    for i in range(5, 14):
        pipe.process(Frame(hands=(pose_fist(center=(0.2, 0.2)),), timestamp=i * 0.05))
    assert pipe.cursor.position == parked
