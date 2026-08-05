"""Server payload parsing and session command handling.

Deliberately tests the protocol layer directly rather than through a live
socket, so these run without FastAPI installed.
"""

from __future__ import annotations

import pytest

from handgesture.capture.simulation import pose_open_palm, pose_point
from handgesture.gestures.vocabulary import Gesture, Mode
from handgesture.osadapter.null import NullAdapter
from handgesture.server.app import Session, frame_from_payload
from handgesture.types import Handedness


def payload_from_hand(hand, timestamp=0.0):
    return {
        "timestamp": timestamp,
        "hands": [
            {
                "handedness": hand.handedness.value,
                "confidence": hand.detection_confidence,
                "landmarks": [{"x": p.x, "y": p.y, "z": p.z} for p in hand.landmarks],
            }
        ],
    }


# --- Payload parsing -------------------------------------------------------

def test_frame_parses_from_browser_payload():
    hand = pose_point()
    frame = frame_from_payload(payload_from_hand(hand, timestamp=1.5))
    assert frame.hand_count == 1
    assert frame.timestamp == 1.5
    assert frame.hands[0].handedness is Handedness.RIGHT


def test_malformed_hand_is_dropped_not_fatal():
    """A truncated landmark list must not take the server down."""
    frame = frame_from_payload(
        {"hands": [{"landmarks": [{"x": 0.1, "y": 0.1}]}], "timestamp": 0.0}
    )
    assert frame.hand_count == 0


def test_empty_payload_yields_empty_frame():
    assert frame_from_payload({}).hand_count == 0


def test_landmark_values_survive_the_round_trip():
    hand = pose_point()
    frame = frame_from_payload(payload_from_hand(hand))
    assert frame.hands[0][0].x == pytest.approx(hand[0].x)
    assert frame.hands[0][8].y == pytest.approx(hand[8].y)


# --- State serialisation ---------------------------------------------------

def test_state_payload_is_json_friendly():
    import json

    session = Session(NullAdapter())
    out = None
    for i in range(5):
        out = session.handle_frame(payload_from_hand(pose_point(), timestamp=i * 0.05))

    json.dumps(out)  # must not raise
    assert out["type"] == "state"
    assert "gesture" in out and "confidence" in out


def test_gesture_reaches_the_payload():
    session = Session(NullAdapter())
    out = None
    for i in range(6):
        out = session.handle_frame(payload_from_hand(pose_point(), timestamp=i * 0.05))
    assert out["gesture"] == Gesture.POINT.value
    assert out["cursor"] is not None


# --- Commands --------------------------------------------------------------

def test_set_mode_command():
    session = Session(NullAdapter())
    out = session.handle_command({"command": "set_mode", "mode": "browser"})
    assert out["type"] == "ack"
    assert session.pipeline.mode is Mode.BROWSER


def test_unknown_mode_is_rejected():
    session = Session(NullAdapter())
    out = session.handle_command({"command": "set_mode", "mode": "nonsense"})
    assert out["type"] == "error"


def test_unknown_command_is_rejected():
    session = Session(NullAdapter())
    assert session.handle_command({"command": "fly"})["type"] == "error"


def test_emergency_stop_and_release_commands():
    session = Session(NullAdapter())
    session.handle_command({"command": "emergency_stop", "timestamp": 1.0})
    assert session.pipeline.emergency_stop.engaged

    out = None
    for i in range(6):
        out = session.handle_frame(payload_from_hand(pose_point(), timestamp=2 + i * 0.05))
    assert out["emergencyStopped"]
    assert out["events"] == []

    session.handle_command({"command": "release_stop"})
    assert not session.pipeline.emergency_stop.engaged


def test_calibration_flow_takes_over_the_stream():
    session = Session(NullAdapter())
    out = session.handle_command({"command": "start_calibration"})
    assert out["type"] == "calibration"

    out = session.handle_frame(payload_from_hand(pose_open_palm()))
    assert out["type"] == "calibration"
    assert 0.0 <= out["progress"] <= 1.0


def test_calibration_can_be_cancelled():
    session = Session(NullAdapter())
    session.handle_command({"command": "start_calibration"})
    session.handle_command({"command": "cancel_calibration"})
    out = session.handle_frame(payload_from_hand(pose_point()))
    assert out["type"] == "state"


def test_get_profile_returns_a_profile():
    session = Session(NullAdapter())
    out = session.handle_command({"command": "get_profile"})
    assert out["type"] == "profile"
    assert "pinch_threshold" in out["profile"]


def test_session_uses_adapter_screen_size():
    """Cursor mapping must target the real screen, not a hardcoded default."""
    session = Session(NullAdapter(screen_width=2560, screen_height=1440))
    assert session.pipeline.config.cursor.screen_width == 2560
    assert session.pipeline.config.cursor.screen_height == 1440


# --- Live ASGI route (regression guard) ------------------------------------
#
# The unit tests above call Session directly, which passes even when the
# WebSocket route is broken. These drive the real ASGI app: FastAPI resolves
# route annotations against module globals, so importing WebSocket inside a
# factory silently turns the socket parameter into a query field and rejects
# every connection. Only an end-to-end connect catches that.

fastapi = pytest.importorskip("fastapi", reason="server extra not installed")


@pytest.fixture
def adapter():
    """The recording adapter the app under test drives.

    Shared by every session in one app, which is what lets a test assert on
    what actually reached the OS layer.
    """
    return NullAdapter()


@pytest.fixture
def client(adapter):
    from fastapi.testclient import TestClient

    from handgesture.server.app import create_app

    return TestClient(create_app(adapter))


def test_health_endpoint(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["adapter"] == "null"


def test_gestures_endpoint_lists_modes(client):
    body = client.get("/api/gestures").json()
    assert "point" in body["gestures"]
    assert "navigation" in body["modes"]


def test_websocket_route_accepts_connections(client):
    """Guards the annotation-resolution bug described above."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "set_mode", "mode": "browser"})
        assert ws.receive_json() == {
            "type": "ack",
            "command": "set_mode",
            "mode": "browser",
        }


def test_websocket_runs_the_pipeline(client):
    """Frames over the wire must produce real gesture state."""
    with client.websocket_connect("/ws") as ws:
        reply = None
        for i in range(6):
            ws.send_json(payload_from_hand(pose_point(), timestamp=i * 0.05))
            reply = ws.receive_json()
        assert reply["type"] == "state"
        assert reply["gesture"] == "point"
        assert reply["cursor"] is not None


def test_websocket_survives_malformed_json(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_text("{not json")
        assert ws.receive_json()["type"] == "error"
        # Connection must stay usable afterwards.
        ws.send_json({"type": "command", "command": "get_profile"})
        assert ws.receive_json()["type"] == "profile"


def test_index_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "hand-gesture-os" in r.text


# --- Phase 3: the spatial interface over the wire --------------------------


def test_open_app_creates_a_window(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "open_app", "app": "files"})
        reply = ws.receive_json()
        assert reply["type"] == "spatial"
        assert reply["workspace"]["windows"][0]["app"] == "files"
        assert reply["workspace"]["focused"] == reply["windowId"]


def test_closing_unsaved_work_over_the_wire_requires_confirmation(client):
    """The confirmation rule has to survive the protocol boundary."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {"type": "command", "command": "open_app", "app": "editor", "unsaved": True}
        )
        window_id = ws.receive_json()["windowId"]

        ws.send_json({"type": "command", "command": "close_window", "windowId": window_id})
        reply = ws.receive_json()
        assert reply["closed"] is False
        assert reply["needsConfirmation"] is True
        assert len(reply["workspace"]["windows"]) == 1

        ws.send_json({"type": "command", "command": "confirm_close", "windowId": window_id})
        assert ws.receive_json()["workspace"]["windows"] == []


def test_window_state_commands_round_trip(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "open_app", "app": "files"})
        window_id = ws.receive_json()["windowId"]

        ws.send_json(
            {"type": "command", "command": "window_state", "state": "maximize",
             "windowId": window_id}
        )
        assert ws.receive_json()["workspace"]["windows"][0]["state"] == "maximized"

        ws.send_json(
            {"type": "command", "command": "window_state", "state": "restore",
             "windowId": window_id}
        )
        assert ws.receive_json()["workspace"]["windows"][0]["state"] == "normal"


def test_unknown_window_state_is_rejected(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "window_state", "state": "explode"})
        assert ws.receive_json()["type"] == "error"


def test_overlay_and_quick_settings_toggle(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "set_overlay", "overlay": "home"})
        assert ws.receive_json()["workspace"]["overlay"] == "home"

        ws.send_json(
            {"type": "command", "command": "toggle_setting", "name": "one_hand_mode"}
        )
        reply = ws.receive_json()
        assert reply["value"] is True
        assert reply["workspace"]["quick_settings"]["one_hand_mode"] is True


def test_notifications_over_the_wire(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {"type": "command", "command": "notify", "app": "mail", "title": "Hello"}
        )
        assert ws.receive_json()["workspace"]["unread"] == 1

        ws.send_json({"type": "command", "command": "clear_notifications"})
        assert ws.receive_json()["workspace"]["notifications"] == []


def test_frames_carry_the_workspace_snapshot(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "open_app", "app": "files"})
        ws.receive_json()
        ws.send_json(payload_from_hand(pose_point(), timestamp=0.05))
        reply = ws.receive_json()
        assert reply["type"] == "state"
        assert len(reply["spatial"]["windows"]) == 1


# --- Phase 4: apps over the wire -------------------------------------------


def test_list_apps(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "list_apps"})
        apps = ws.receive_json()["apps"]
        assert "keyboard" in apps and "files" in apps


def test_app_action_reaches_the_app_in_a_window(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "open_app", "app": "music"})
        window_id = ws.receive_json()["windowId"]

        ws.send_json({
            "type": "command", "command": "app_action",
            "windowId": window_id, "action": "next",
        })
        reply = ws.receive_json()
        assert reply["type"] == "app"
        assert reply["ok"] is True
        assert reply["state"]["index"] == 1


def test_app_action_on_a_window_with_no_app_errors(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "app_action",
                      "windowId": 99999, "action": "next"})
        assert ws.receive_json()["type"] == "error"


def test_an_unknown_app_action_reports_failure_without_dropping_the_socket(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "open_app", "app": "music"})
        window_id = ws.receive_json()["windowId"]
        ws.send_json({"type": "command", "command": "app_action",
                      "windowId": window_id, "action": "explode"})
        reply = ws.receive_json()
        assert reply["ok"] is False
        # Socket still usable.
        ws.send_json({"type": "command", "command": "list_apps"})
        assert ws.receive_json()["type"] == "apps"


def test_an_app_os_request_reaches_the_adapter(client, adapter):
    """Media keys must actually get as far as the OS adapter."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "open_app", "app": "music"})
        window_id = ws.receive_json()["windowId"]
        adapter.clear()
        ws.send_json({"type": "command", "command": "app_action",
                      "windowId": window_id, "action": "play"})
        reply = ws.receive_json()
        assert reply["dispatched"][0]["executed"] is True
        assert "media_key" in adapter.actions()


def test_a_destructive_file_operation_waits_for_confirmation(client, adapter, tmp_path):
    """The whole safety chain, over the wire: request, gate, confirm, execute."""
    victim = tmp_path / "victim.txt"
    victim.write_text("x")

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "open_app", "app": "files",
                      "appKwargs": {"root": str(tmp_path)}})
        window_id = ws.receive_json()["windowId"]

        ws.send_json({"type": "command", "command": "app_action",
                      "windowId": window_id, "action": "select",
                      "args": {"name": "victim.txt"}})
        ws.receive_json()

        ws.send_json({"type": "command", "command": "app_action",
                      "windowId": window_id, "action": "delete"})
        reply = ws.receive_json()
        assert reply["dispatched"][0]["awaitingConfirmation"] is True
        assert reply["pendingConfirmation"] == "file.delete"
        assert victim.exists(), "nothing may be deleted before confirmation"

        # A confirm that is not held long enough must not go through.
        ws.send_json({"type": "command", "command": "confirm",
                      "timestamp": 1.0, "heldSeconds": 0.01})
        assert ws.receive_json()["type"] == "error"
        assert victim.exists()

        ws.send_json({"type": "command", "command": "confirm",
                      "timestamp": 1.0, "heldSeconds": 5.0})
        reply = ws.receive_json()
        assert reply["type"] == "confirmed"
        assert not victim.exists()


def test_a_cancelled_file_operation_never_runs(client, tmp_path):
    keeper = tmp_path / "keeper.txt"
    keeper.write_text("x")

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "open_app", "app": "files",
                      "appKwargs": {"root": str(tmp_path)}})
        window_id = ws.receive_json()["windowId"]
        ws.send_json({"type": "command", "command": "app_action",
                      "windowId": window_id, "action": "select",
                      "args": {"name": "keeper.txt"}})
        ws.receive_json()
        ws.send_json({"type": "command", "command": "app_action",
                      "windowId": window_id, "action": "delete"})
        ws.receive_json()

        ws.send_json({"type": "command", "command": "cancel_confirmation"})
        assert ws.receive_json()["operation"] == "file.delete"
        assert keeper.exists()


def test_the_emergency_stop_blocks_app_os_effects(client, adapter):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "command", "command": "open_app", "app": "music"})
        window_id = ws.receive_json()["windowId"]
        ws.send_json({"type": "command", "command": "emergency_stop"})
        ws.receive_json()

        adapter.clear()
        ws.send_json({"type": "command", "command": "app_action",
                      "windowId": window_id, "action": "play"})
        reply = ws.receive_json()
        assert reply["dispatched"][0]["executed"] is False
        assert adapter.actions() == []
