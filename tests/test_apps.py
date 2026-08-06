"""The Phase 4 applications.

Every app is a pure state machine, so all of this runs headlessly. What is
*not* covered here is the real OS effect an app requests — those go out as
``OSRequest`` objects and are verified only as far as the recording
NullAdapter.
"""

from __future__ import annotations

import pytest

from handgesture.apps import (
    BrowserApp,
    FilesApp,
    KeyboardApp,
    KeyboardConfig,
    MusicApp,
    Repeat,
    SettingsApp,
    Track,
    VideoApp,
    ViewerApp,
    available_apps,
    create_app,
)
from handgesture.apps.keyboard import BACKSPACE, ENTER, LAYOUT, SHIFT, SPACE, Layout
from handgesture.control.safety import SENSITIVE_OPERATIONS, Sensitivity
from handgesture.types import Point

# --- Registry --------------------------------------------------------------

def test_every_app_is_registered_and_constructible():
    names = available_apps()
    assert {"files", "browser", "music", "video", "photos", "settings",
            "keyboard"} <= set(names)
    for name in names:
        app = create_app(name)
        assert app is not None
        assert isinstance(app.state(), dict)


def test_unknown_app_is_none_not_an_exception():
    assert create_app("nonexistent") is None


def test_unknown_actions_fail_rather_than_raise():
    for name in available_apps():
        result = create_app(name).action("definitely_not_an_action")
        assert not result.ok


# --- Keyboard --------------------------------------------------------------

def kb(**kwargs):
    return KeyboardApp(KeyboardConfig(**kwargs))


def center_of(app, label):
    for key in app.keys():
        if key.label == label:
            return Point(key.x + key.width / 2, key.y + key.height / 2)
    raise AssertionError(f"no key {label!r} in {app.layout}")


def test_keys_tile_the_panel_without_escaping_it():
    app = kb()
    for key in app.keys():
        assert 0.0 <= key.x and key.x + key.width <= 1.0 + 1e-9
        assert 0.0 <= key.y and key.y + key.height <= 1.0 + 1e-9


def test_keys_do_not_overlap_within_a_row():
    app = kb()
    rows: dict[float, list] = {}
    for key in app.keys():
        rows.setdefault(round(key.y, 4), []).append(key)
    for row in rows.values():
        row.sort(key=lambda k: k.x)
        for a, b in zip(row, row[1:], strict=False):
            assert a.x + a.width <= b.x + 1e-9


def test_dwell_types_only_after_resting_long_enough():
    app = kb(dwell_seconds=0.5)
    q = center_of(app, "q")
    assert app.update(q, 0.0) is None
    assert app.update(q, 0.3) is None
    press = app.update(q, 0.6)
    assert press is not None and press.char == "q"
    assert app.buffer == "q"


def test_travelling_across_the_keyboard_types_nothing():
    """The whole point of dwell: keys you pass over must not fire."""
    app = kb(dwell_seconds=0.5)
    labels = ["q", "w", "e", "r", "t"]
    now = 0.0
    for label in labels:
        now += 0.2  # each key is left well before the dwell completes
        assert app.update(center_of(app, label), now) is None
    assert app.buffer == ""


def test_leaving_and_returning_restarts_the_dwell():
    app = kb(dwell_seconds=0.5)
    q, w = center_of(app, "q"), center_of(app, "w")
    app.update(q, 0.0)
    app.update(q, 0.4)
    app.update(w, 0.45)       # left the key
    app.update(q, 0.5)        # came back: dwell starts over
    assert app.update(q, 0.8) is None
    assert app.update(q, 1.05) is not None


def test_holding_still_does_not_repeat_faster_than_the_cooldown():
    """A trembling hand resting on a key must not machine-gun it."""
    app = kb(dwell_seconds=0.3, cooldown_seconds=0.5)
    q = center_of(app, "q")

    presses = []
    now = 0.0
    while now <= 3.0:
        if app.update(q, now) is not None:
            presses.append(now)
        now += 0.05

    assert len(presses) >= 2, "a rested key should still repeat eventually"
    gaps = [b - a for a, b in zip(presses, presses[1:], strict=False)]
    assert min(gaps) >= app.config.cooldown_seconds - 1e-9, gaps


def test_pinch_types_immediately_without_waiting_for_dwell():
    app = kb(dwell_seconds=5.0)
    press = app.update(center_of(app, "q"), 0.0, pinching=True)
    assert press is not None and press.source == "pinch"


def test_dwell_can_be_turned_off_leaving_pinch_only():
    app = kb(dwell_seconds=0.2, dwell_enabled=False)
    q = center_of(app, "q")
    app.update(q, 0.0)
    assert app.update(q, 1.0) is None
    assert app.update(q, 1.1, pinching=True) is not None


def test_a_hand_off_the_keyboard_types_nothing():
    app = kb(dwell_seconds=0.1)
    assert app.update(None, 0.0) is None
    assert app.update(None, 1.0) is None
    assert app.buffer == ""


def test_shift_is_one_shot_and_caps_lock_is_not():
    app = kb(dwell_seconds=0.0, cooldown_seconds=0.0)
    app.update(center_of(app, SHIFT), 0.0)
    assert app.shift
    app.update(center_of(app, "Q"), 0.1)      # label is uppercase while shifted
    assert app.buffer == "Q"
    assert not app.shift

    # Double-tap for caps lock.
    app.update(center_of(app, SHIFT), 1.0)
    app.update(center_of(app, SHIFT), 1.2)
    assert app.caps_lock
    app.update(center_of(app, "A"), 1.4)
    app.update(center_of(app, "B"), 1.6)
    assert app.buffer.endswith("AB")


def test_backspace_space_and_enter():
    app = kb(dwell_seconds=0.0, cooldown_seconds=0.0)
    for i, label in enumerate(["h", "i", SPACE, "a", BACKSPACE, ENTER]):
        app.update(center_of(app, label), i * 0.1)
    assert app.buffer == "hi \n"


def test_layout_switching_round_trips():
    app = kb(dwell_seconds=0.0, cooldown_seconds=0.0)
    app.update(center_of(app, LAYOUT), 0.0)
    assert app.layout is Layout.NUMBERS
    app.update(center_of(app, "#+="), 0.1)
    assert app.layout is Layout.SYMBOLS
    app.update(center_of(app, "ABC"), 0.2)
    assert app.layout is Layout.LETTERS


def test_dwell_progress_reports_a_fraction():
    app = kb(dwell_seconds=1.0)
    q = center_of(app, "q")
    app.update(q, 0.0)
    assert app.progress_at(0.5) == pytest.approx(0.5)
    assert app.progress_at(2.0) == 1.0


def test_send_empties_the_buffer_and_asks_the_os_to_type():
    app = kb()
    app.action("type", text="hello")
    result = app.action("send")
    assert result.ok
    assert app.buffer == ""
    assert result.requests[0].operation == "keyboard.type"
    assert result.requests[0].payload["text"] == "hello"


def test_sending_an_empty_buffer_does_nothing():
    assert not KeyboardApp().action("send").ok


# --- Files -----------------------------------------------------------------

@pytest.fixture
def sandbox(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.txt").write_text("notes")
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("bb")
    return tmp_path


def test_listing_puts_directories_first(sandbox):
    app = FilesApp(sandbox)
    names = [e.name for e in app.entries()]
    assert names == ["docs", "a.txt", "b.txt"]


def test_navigation_up_and_home(sandbox):
    app = FilesApp(sandbox)
    assert app.action("open", name="docs").ok
    assert app.relative_cwd == "/docs"
    assert app.action("up").ok
    assert app.relative_cwd == "/"
    assert not app.action("up").ok      # already at the root


def test_cannot_escape_the_root_with_dot_dot(sandbox):
    app = FilesApp(sandbox)
    result = app.action("open", name="..")
    assert not result.ok
    assert "outside" in result.message
    assert app.cwd == app.root


def test_cannot_escape_the_root_with_an_absolute_path(sandbox):
    app = FilesApp(sandbox)
    assert not app.action("open", name="/etc").ok


def test_a_symlink_pointing_outside_the_root_is_refused(sandbox, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.txt").write_text("secret")
    link = sandbox / "escape"
    link.symlink_to(outside)

    app = FilesApp(sandbox)
    result = app.action("open", name="escape")
    assert not result.ok
    assert "outside" in result.message


def test_delete_is_never_performed_directly(sandbox):
    """Deleting is on the sensitive list — it can only be *requested*."""
    app = FilesApp(sandbox)
    app.action("select", name="a.txt")
    result = app.action("delete")
    assert result.ok
    assert (sandbox / "a.txt").exists(), "the file must survive an unconfirmed delete"
    assert result.requests[0].operation == "file.delete"


def test_the_delete_operation_is_classified_critical():
    assert SENSITIVE_OPERATIONS["file.delete"] is Sensitivity.CRITICAL
    assert SENSITIVE_OPERATIONS["file.move_permanent"] is Sensitivity.CRITICAL


def test_a_confirmed_delete_actually_deletes(sandbox):
    app = FilesApp(sandbox)
    app.action("select", name="a.txt")
    request = app.action("delete").requests[0]
    assert app.execute_confirmed(request.operation, request.payload).ok
    assert not (sandbox / "a.txt").exists()


def test_a_confirmed_delete_still_refuses_paths_outside_the_root(sandbox, tmp_path_factory):
    """A payload that made a round trip must not be trusted on the way back."""
    outside = tmp_path_factory.mktemp("outside")
    victim = outside / "keepme.txt"
    victim.write_text("keep")

    app = FilesApp(sandbox)
    result = app.execute_confirmed("file.delete", {"paths": [str(victim)]})
    assert not result.ok
    assert victim.exists()


def test_copy_and_paste_duplicates_without_overwriting(sandbox):
    app = FilesApp(sandbox)
    app.action("select", name="a.txt")
    app.action("copy")
    app.action("open", name="docs")
    assert app.action("paste").ok
    assert (sandbox / "docs" / "a.txt").read_text() == "a"

    # Pasting again must not clobber the copy that is already there.
    app.action("select", name="a.txt")
    app.action("copy")
    result = app.action("paste")
    assert "skipped" in result.message


def test_cut_and_paste_asks_for_confirmation(sandbox):
    app = FilesApp(sandbox)
    app.action("select", name="a.txt")
    app.action("cut")
    app.action("open", name="docs")
    result = app.action("paste")
    assert result.requests[0].operation == "file.move_permanent"
    assert (sandbox / "a.txt").exists(), "nothing moves before confirmation"

    assert app.execute_confirmed(
        result.requests[0].operation, result.requests[0].payload
    ).ok
    assert not (sandbox / "a.txt").exists()
    assert (sandbox / "docs" / "a.txt").exists()


def test_rename_in_place_is_performed_directly(sandbox):
    app = FilesApp(sandbox)
    assert app.action("rename", name="a.txt", to="renamed.txt").ok
    assert (sandbox / "renamed.txt").exists()


def test_rename_refuses_a_path_separator(sandbox):
    app = FilesApp(sandbox)
    assert not app.action("rename", name="a.txt", to="../escaped.txt").ok
    assert (sandbox / "a.txt").exists()


def test_rename_refuses_to_overwrite(sandbox):
    app = FilesApp(sandbox)
    assert not app.action("rename", name="a.txt", to="b.txt").ok
    assert (sandbox / "b.txt").read_text() == "bb"


def test_new_folder(sandbox):
    app = FilesApp(sandbox)
    assert app.action("new_folder", name="fresh").ok
    assert (sandbox / "fresh").is_dir()
    assert not app.action("new_folder", name="fresh").ok


def test_pasting_an_empty_clipboard_fails_cleanly(sandbox):
    assert not FilesApp(sandbox).action("paste").ok


def test_selection_toggles(sandbox):
    app = FilesApp(sandbox)
    app.action("select", name="a.txt")
    app.action("select", name="a.txt")
    assert app.selection == set()
    app.action("select_all")
    assert len(app.selection) == 3
    app.action("clear_selection")
    assert app.selection == set()


# --- Music and video -------------------------------------------------------

def test_next_and_previous_walk_the_playlist():
    app = MusicApp()
    assert app.index == 0
    app.action("next")
    assert app.index == 1
    app.action("previous")
    assert app.index == 0


def test_previous_restarts_a_track_that_is_already_playing():
    app = MusicApp()
    app.action("next")
    app.action("seek", position=30)
    app.action("previous")
    assert app.index == 1 and app.transport.position == 0.0


def test_the_end_of_the_playlist_stops_unless_repeating():
    app = MusicApp()
    for _ in range(5):
        app.action("next")
    assert app.index == len(app.tracks) - 1
    assert not app.transport.playing

    app.action("repeat")                 # -> all
    assert app.repeat is Repeat.ALL
    app.action("next")
    assert app.index == 0


def test_repeat_one_stays_on_the_same_track():
    app = MusicApp()
    app.action("repeat")
    app.action("repeat")
    assert app.repeat is Repeat.ONE
    app.action("next")
    assert app.index == 0


def test_shuffle_never_picks_the_track_already_playing():
    app = MusicApp()
    app.action("shuffle")
    for _ in range(30):
        before = app.index
        app.action("next")
        assert app.index != before


def test_seek_is_clamped_to_the_track_length():
    app = MusicApp()
    app.action("seek", position=99999)
    assert app.transport.position == app.current.duration
    app.action("seek", position=-5)
    assert app.transport.position == 0.0


def test_volume_is_clamped_and_reaches_the_os_as_a_request():
    app = MusicApp()
    result = app.action("set_volume", level=5.0)
    assert app.transport.volume == 1.0
    assert result.requests[0].operation == "audio.set_volume"


def test_an_empty_playlist_fails_cleanly():
    app = MusicApp(tracks=[])
    assert not app.action("play").ok
    assert app.state()["current"] is None


def test_play_pause_toggles_and_asks_for_a_media_key():
    app = MusicApp()
    result = app.action("play_pause")
    assert app.transport.playing
    assert result.requests[0].operation == "media.play_pause"


def test_video_adds_fullscreen_subtitles_and_skip():
    app = VideoApp(tracks=[Track("clip", "", 100.0)])
    assert app.action("fullscreen").ok and app.fullscreen
    assert app.action("subtitles").ok and app.subtitles
    app.action("skip", seconds=30)
    assert app.transport.position == 30.0
    # Inherited behaviour still works.
    assert app.action("play_pause").ok


# --- Browser ---------------------------------------------------------------

def test_navigating_records_history():
    app = BrowserApp()
    app.action("navigate", url="https://example.com/a")
    app.action("navigate", url="https://example.com/b")
    assert app.current.url.endswith("/b")
    assert app.current.can_go_back


def test_back_and_forward():
    app = BrowserApp()
    app.action("navigate", url="https://a.test")
    app.action("navigate", url="https://b.test")
    app.action("back")
    assert app.current.url == "https://a.test"
    app.action("forward")
    assert app.current.url == "https://b.test"


def test_navigating_truncates_the_forward_history():
    """The classic bug: forward resurrecting a page you navigated away from."""
    app = BrowserApp()
    app.action("navigate", url="https://a.test")
    app.action("navigate", url="https://b.test")
    app.action("back")
    app.action("navigate", url="https://c.test")
    assert not app.current.can_go_forward
    assert not app.action("forward").ok


def test_back_at_the_start_of_history_fails_cleanly():
    assert not BrowserApp().action("back").ok


def test_tabs_open_close_and_cycle():
    app = BrowserApp()
    app.action("new_tab")
    app.action("new_tab")
    assert len(app.tabs) == 3
    app.action("next_tab")
    assert app.index == 0        # wraps
    app.action("close_tab")
    assert len(app.tabs) == 2


def test_the_last_tab_cannot_be_closed():
    app = BrowserApp()
    assert not app.action("close_tab").ok


def test_each_tab_keeps_its_own_history():
    app = BrowserApp()
    app.action("navigate", url="https://a.test")
    app.action("new_tab")
    assert not app.current.can_go_back
    app.action("select_tab", index=0)
    assert app.current.url == "https://a.test"


def test_history_is_bounded():
    app = BrowserApp()
    for i in range(300):
        app.action("navigate", url=f"https://a.test/{i}")
    from handgesture.apps.browser import MAX_HISTORY
    assert len(app.current.history) <= MAX_HISTORY


def test_browser_actions_emit_key_chords():
    app = BrowserApp()
    app.action("navigate", url="https://a.test")
    app.action("navigate", url="https://b.test")
    result = app.action("back")
    assert result.requests[0].operation == "keyboard.chord"
    assert result.requests[0].payload["keys"] == ["alt", "left"]


# --- Viewer ----------------------------------------------------------------

def test_zoom_is_clamped_to_its_range():
    app = ViewerApp()
    app.action("set_zoom", zoom=100)
    assert app.zoom == 8.0
    app.action("set_zoom", zoom=0.01)
    assert app.zoom == 1.0


def test_there_is_nothing_to_pan_at_one_times_zoom():
    app = ViewerApp()
    assert not app.action("pan", dx=0.3, dy=0.3).ok
    assert app.pan_x == 0.0


def test_panning_is_clamped_to_the_visible_area():
    """Panning past the edge would lose the image with no way back."""
    app = ViewerApp()
    app.action("set_zoom", zoom=2.0)
    app.action("pan", dx=99, dy=99)
    assert app.pan_x == pytest.approx(app.pan_limit)
    assert app.pan_y == pytest.approx(app.pan_limit)


def test_zooming_back_out_pulls_the_pan_back_into_range():
    app = ViewerApp()
    app.action("set_zoom", zoom=4.0)
    app.action("pan", dx=99, dy=0)
    app.action("set_zoom", zoom=1.5)
    assert abs(app.pan_x) <= app.pan_limit + 1e-9


def test_relative_zoom_compounds():
    app = ViewerApp()
    app.action("zoom", delta=1.0)      # x2
    assert app.zoom == pytest.approx(2.0)
    app.action("zoom", delta=1.0)
    assert app.zoom == pytest.approx(4.0)


def test_rotation_wraps_and_rejects_odd_angles():
    app = ViewerApp()
    for _ in range(4):
        app.action("rotate", degrees=90)
    assert app.rotation == 0
    assert not app.action("rotate", degrees=45).ok


def test_changing_image_resets_the_view():
    app = ViewerApp()
    app.action("set_zoom", zoom=3.0)
    app.action("rotate", degrees=90)
    app.action("next")
    assert app.zoom == 1.0 and app.rotation == 0 and app.pan_x == 0.0


def test_image_selection_wraps():
    app = ViewerApp()
    app.action("previous")
    assert app.index == len(app.images) - 1


# --- Settings --------------------------------------------------------------

def test_settings_start_at_their_defaults():
    app = SettingsApp()
    assert app.values["dominant_hand"] == "right"
    assert app.values["stop_on_hand_loss"] is True


def test_out_of_range_values_are_rejected_not_clamped():
    """A silently-clamped bad value hides the mistake; a rejection does not."""
    app = SettingsApp()
    result = app.action("set", key="min_gesture_confidence", value=5.0)
    assert not result.ok
    assert app.values["min_gesture_confidence"] == 0.55


def test_wrong_type_is_rejected():
    app = SettingsApp()
    assert not app.action("set", key="one_hand_mode", value="yes").ok
    assert not app.action("set", key="dominant_hand", value="middle").ok
    assert not app.action("set", key="cursor_gain", value="fast").ok


def test_valid_values_are_stored():
    app = SettingsApp()
    assert app.action("set", key="cursor_gain", value=2.0).ok
    assert app.values["cursor_gain"] == 2.0


def test_unknown_settings_are_rejected():
    assert not SettingsApp().action("set", key="warp_drive", value=True).ok


def test_reset_and_reset_all():
    app = SettingsApp()
    app.action("set", key="cursor_gain", value=2.0)
    app.action("reset", key="cursor_gain")
    assert app.values["cursor_gain"] == 1.0

    app.action("set", key="high_contrast", value=True)
    app.action("reset_all")
    assert app.values["high_contrast"] is False


def test_settings_apply_to_a_live_pipeline():
    from handgesture.pipeline import GesturePipeline
    from handgesture.types import Handedness

    pipe = GesturePipeline()
    app = SettingsApp()
    app.action("set", key="dominant_hand", value="left")
    app.action("set", key="cursor_gain", value=2.5)
    app.action("set", key="one_hand_mode", value=True)
    applied = app.apply_to(pipe)

    assert "dominant_hand" in applied
    assert pipe.config.dominant_hand is Handedness.LEFT
    assert pipe.config.cursor.gain == 2.5
    assert pipe.config.one_hand_mode is True
    assert pipe.recognizer.min_confidence == app.values["min_gesture_confidence"]


# --- Apps inside windows ---------------------------------------------------

def test_opening_a_window_starts_the_app_inside_it():
    from handgesture.spatial import Workspace

    ws = Workspace()
    window = ws.open("music")
    assert ws.app_for(window.id) is not None
    assert window.title == "Music"


def test_closing_a_window_disposes_of_its_app():
    from handgesture.spatial import Workspace

    ws = Workspace()
    window = ws.open("music")
    ws.force_close(window.id)
    assert ws.app_for(window.id) is None
    assert ws.apps == {}


def test_a_window_with_no_registered_app_still_works():
    from handgesture.spatial import Workspace

    ws = Workspace()
    window = ws.open("something_unregistered")
    assert ws.app_for(window.id) is None
    assert window in ws.windows


def test_the_snapshot_carries_the_focused_apps_state():
    from handgesture.spatial import Workspace

    ws = Workspace()
    ws.open("music")
    snap = ws.snapshot()
    assert snap["app"]["name"] == "music"
    assert "tracks" in snap["app"]["state"]


def test_the_keyboard_is_driven_by_the_hand_through_the_session():
    """End to end: landmarks in, a character typed out.

    Nothing here builds a PipelineState by hand — a synthetic pointing hand
    goes through the real pipeline, the cursor is mapped into the keyboard
    window, and the dwell timer runs on frame time.
    """
    from handgesture.apps.keyboard import KeyboardConfig
    from handgesture.capture.simulation import pose_point
    from handgesture.server.app import Session
    from handgesture.spatial import Rect

    session = Session()
    window = session.spatial.workspace.open(
        "keyboard",
        rect=Rect(0.0, 0.0, 1.0, 1.0),
        app_kwargs={"config": KeyboardConfig(dwell_seconds=0.3, cooldown_seconds=0.1)},
    )
    app = session.spatial.workspace.app_for(window.id)

    def payload(hand, t):
        return {
            "timestamp": t,
            "hands": [{
                "handedness": hand.handedness.value,
                "confidence": hand.detection_confidence,
                "landmarks": [{"x": p.x, "y": p.y, "z": p.z} for p in hand.landmarks],
            }],
        }

    # Hold a point steady long enough for the dwell to complete.
    replies = [session.handle_frame(payload(pose_point(center=(0.5, 0.5)), i * 0.1))
               for i in range(12)]

    assert app.buffer, "resting on a key should have typed something"
    assert any("typed" in r for r in replies)
    # And the key that fired is the one under the cursor.
    assert app.buffer[-1] in {k.label for k in app.keys()}
