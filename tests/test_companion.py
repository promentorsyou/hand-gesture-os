"""Pairing and the mobile companion.

The pairing tests are the security-relevant ones in this project, so they
are written as adversarial cases rather than happy paths: guessing, replay,
expiry, and reaching a session you were not paired to.
"""

from __future__ import annotations

import pytest

from handgesture.companion.pairing import (
    CODE_TTL_SECONDS,
    PairingError,
    PairingService,
)
from handgesture.companion.remote import (
    COMPANION_COMMANDS,
    COMPANION_DENIED,
    CompanionBridge,
)
from handgesture.server.app import Session

# --- Codes -----------------------------------------------------------------

def test_a_code_is_six_digits_and_bound_to_its_session():
    service = PairingService()
    code = service.create_code("session-a", now=0.0)
    assert len(code.code) == 6 and code.code.isdigit()
    assert code.session_id == "session-a"


def test_codes_are_not_predictable():
    """Not a proof of randomness — a smoke test that it is not a counter."""
    service = PairingService()
    codes = {service.create_code(f"s{i}", now=0.0).code for i in range(50)}
    assert len(codes) > 40


def test_a_code_can_only_be_redeemed_once():
    service = PairingService()
    code = service.create_code("session-a", now=0.0).code
    assert service.redeem(code, now=1.0).session_id == "session-a"
    with pytest.raises(PairingError):
        service.redeem(code, now=2.0)


def test_a_code_expires():
    service = PairingService()
    code = service.create_code("session-a", now=0.0).code
    with pytest.raises(PairingError, match="invalid or expired"):
        service.redeem(code, now=CODE_TTL_SECONDS + 1)


def test_issuing_a_new_code_invalidates_the_old_one():
    """A forgotten code must not stay redeemable."""
    service = PairingService()
    first = service.create_code("session-a", now=0.0).code
    service.create_code("session-a", now=1.0)
    with pytest.raises(PairingError):
        service.redeem(first, now=2.0)


def test_cancelling_withdraws_the_code():
    service = PairingService()
    code = service.create_code("session-a", now=0.0).code
    assert service.cancel_code("session-a")
    with pytest.raises(PairingError):
        service.redeem(code, now=1.0)


def test_codes_for_different_sessions_coexist():
    service = PairingService()
    a = service.create_code("session-a", now=0.0).code
    b = service.create_code("session-b", now=0.0).code
    assert service.redeem(a, now=1.0).session_id == "session-a"
    assert service.redeem(b, now=1.0).session_id == "session-b"


# --- Brute force -----------------------------------------------------------

def test_guessing_locks_pairing_out():
    """Six digits is a million guesses — the lockout is what makes it safe."""
    service = PairingService(max_attempts=3, lockout_seconds=60.0)
    service.create_code("session-a", now=0.0)

    for _ in range(2):
        with pytest.raises(PairingError, match="invalid or expired"):
            service.redeem("000000", now=1.0)
    with pytest.raises(PairingError, match="locked"):
        service.redeem("000001", now=1.0)

    assert service.locked_out(now=2.0)


def test_a_locked_out_service_refuses_even_the_correct_code():
    service = PairingService(max_attempts=2, lockout_seconds=60.0)
    code = service.create_code("session-a", now=0.0).code
    for _ in range(2):
        with pytest.raises(PairingError):
            service.redeem("999999", now=1.0)
    with pytest.raises(PairingError, match="too many attempts"):
        service.redeem(code, now=2.0)


def test_the_lockout_lifts():
    service = PairingService(max_attempts=2, lockout_seconds=60.0)
    code = service.create_code("session-a", now=0.0).code
    for _ in range(2):
        with pytest.raises(PairingError):
            service.redeem("999999", now=1.0)
    assert not service.locked_out(now=200.0)
    # The code itself has expired by then, but pairing works again.
    fresh = service.create_code("session-a", now=200.0).code
    assert service.redeem(fresh, now=201.0)
    assert code != fresh


def test_a_successful_pairing_clears_the_failure_count():
    service = PairingService(max_attempts=3)
    code = service.create_code("session-a", now=0.0).code
    for _ in range(2):
        with pytest.raises(PairingError):
            service.redeem("000000", now=1.0)
    service.redeem(code, now=1.0)

    # Back to a full allowance rather than one guess from lockout.
    second = service.create_code("session-a", now=2.0).code
    for _ in range(2):
        with pytest.raises(PairingError, match="invalid or expired"):
            service.redeem("000000", now=3.0)
    assert service.redeem(second, now=3.0)


# --- Tokens ----------------------------------------------------------------

def test_a_token_is_long_and_verifies():
    service = PairingService()
    code = service.create_code("session-a", now=0.0).code
    token = service.redeem(code, now=1.0)
    assert len(token.token) >= 32
    assert service.verify(token.token).session_id == "session-a"


def test_unknown_and_empty_tokens_do_not_verify():
    service = PairingService()
    assert service.verify("nope") is None
    assert service.verify("") is None
    assert service.verify(None) is None


def test_revoking_a_token_ends_access():
    service = PairingService()
    code = service.create_code("session-a", now=0.0).code
    token = service.redeem(code, now=1.0)
    assert service.revoke(token.token)
    assert service.verify(token.token) is None


def test_revoking_a_session_drops_every_companion():
    service = PairingService()
    tokens = []
    for _ in range(3):
        code = service.create_code("session-a", now=0.0).code
        tokens.append(service.redeem(code, now=1.0))
    other = service.redeem(service.create_code("session-b", now=0.0).code, now=1.0)

    assert service.revoke_session("session-a") == 3
    assert all(service.verify(t.token) is None for t in tokens)
    assert service.verify(other.token) is not None


def test_device_labels_are_bounded():
    service = PairingService()
    code = service.create_code("session-a", now=0.0).code
    token = service.redeem(code, now=1.0, label="x" * 500)
    assert len(token.label) <= 40


# --- The restricted command surface ----------------------------------------

def bridge():
    return CompanionBridge(Session())


def test_a_companion_can_stop_but_not_confirm():
    """The design requires a gesture at the camera; a tap is not one."""
    b = bridge()
    assert b.command({"command": "emergency_stop"}).get("engaged") is True

    reply = b.command({"command": "confirm", "heldSeconds": 99})
    assert reply["type"] == "error"
    assert "gesture" in reply["message"]


def test_a_companion_can_cancel_a_confirmation():
    """Cancelling is always the safe direction, so it is allowed."""
    assert "cancel_confirmation" in COMPANION_COMMANDS
    assert "confirm" in COMPANION_DENIED

    b = bridge()
    reply = b.command({"command": "cancel_confirmation"})
    assert reply["type"] != "error"


def test_commands_outside_the_allowlist_are_refused_with_a_reason():
    reply = bridge().command({"command": "definitely_not_a_command"})
    assert reply["type"] == "error"
    assert "companion" in reply["message"]


def test_a_companion_cannot_choose_the_file_browser_root():
    """A phone must not be able to point the sandbox at /."""
    b = bridge()
    b.command({"command": "open_app", "app": "files",
               "appKwargs": {"root": "/"}})
    window = b.session.spatial.workspace.focused
    app = b.session.spatial.workspace.app_for(window.id)
    assert str(app.root) != "/"


def test_allowed_commands_reach_the_session():
    b = bridge()
    assert b.command({"command": "set_mode", "mode": "media"})["mode"] == "media"
    assert b.session.pipeline.mode.value == "media"


def test_replies_are_marked_as_coming_from_a_companion():
    assert bridge().command({"command": "list_apps"})["viaCompanion"] is True


# --- The snapshot ----------------------------------------------------------

def test_the_snapshot_is_compact_and_has_what_a_phone_needs():
    b = bridge()
    b.session.spatial.workspace.open("music")
    b.session.spatial.workspace.notify("mail", "Hello")
    snap = b.snapshot()

    assert snap["mode"] == "navigation"
    assert snap["emergencyStopped"] is False
    assert snap["windows"][0]["app"] == "music"
    assert snap["unread"] == 1
    # Deliberately absent: per-frame data that would dominate a phone link.
    assert "scores" not in snap
    assert "events" not in snap


def test_the_snapshot_reports_a_pending_confirmation():
    b = bridge()
    b.session.pipeline.confirmation.request(
        "file.delete", "delete 1 item", now=0.0, payload={}
    )
    pending = b.snapshot()["pendingConfirmation"]
    assert pending["operation"] == "file.delete"
    assert pending["sensitivity"] == "critical"


def test_the_notification_list_is_capped():
    b = bridge()
    for i in range(40):
        b.session.spatial.workspace.notify("mail", f"n{i}")
    assert len(b.snapshot()["notifications"]) == 10


# --- Over the wire ---------------------------------------------------------

fastapi = pytest.importorskip("fastapi", reason="server extra not installed")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from handgesture.osadapter.null import NullAdapter
    from handgesture.server.app import create_app

    return TestClient(create_app(NullAdapter()))


def desktop(client):
    """Open a desktop session and return (socket, session_id)."""
    ws = client.websocket_connect("/ws").__enter__()
    hello = ws.receive_json()
    assert hello["type"] == "hello"
    return ws, hello["sessionId"]


def get_code(ws):
    ws.send_json({"type": "command", "command": "pair_start"})
    reply = ws.receive_json()
    assert reply["type"] == "pairing"
    return reply["code"]


def test_the_desktop_can_request_a_code(client):
    ws, session_id = desktop(client)
    try:
        code = get_code(ws)
        assert len(code) == 6
        ws.send_json({"type": "command", "command": "pair_status"})
        assert ws.receive_json()["code"] == code
    finally:
        ws.close()


def test_the_full_pairing_handshake(client):
    ws, _ = desktop(client)
    try:
        code = get_code(ws)
        res = client.post("/api/pair/redeem", json={"code": code, "label": "iPhone"})
        assert res.status_code == 200
        token = res.json()["token"]

        with client.websocket_connect(f"/ws/companion?token={token}") as phone:
            hello = phone.receive_json()
            assert hello["type"] == "hello"
            assert hello["label"] == "iPhone"

            phone.send_json({"type": "poll"})
            state = phone.receive_json()
            assert state["type"] == "companion_state"
    finally:
        ws.close()


def test_a_wrong_code_is_refused_with_403(client):
    ws, _ = desktop(client)
    try:
        get_code(ws)
        res = client.post("/api/pair/redeem", json={"code": "000000"})
        assert res.status_code == 403
    finally:
        ws.close()


def test_an_unauthorised_companion_socket_is_closed_not_accepted(client):
    """It must never get a live socket it could send commands down."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/companion?token=forged") as phone:
            phone.receive_json()


def test_a_companion_socket_with_no_token_is_closed(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/companion") as phone:
            phone.receive_json()


def test_a_companion_controls_the_session_it_paired_with(client):
    ws, _ = desktop(client)
    try:
        token = client.post(
            "/api/pair/redeem", json={"code": get_code(ws)}
        ).json()["token"]

        with client.websocket_connect(f"/ws/companion?token={token}") as phone:
            phone.receive_json()
            phone.send_json({"type": "command", "command": "emergency_stop"})
            reply = phone.receive_json()
            assert reply["state"]["emergencyStopped"] is True

        # And the desktop session sees it.
        ws.send_json({"type": "command", "command": "pair_status"})
        ws.receive_json()
    finally:
        ws.close()


def test_a_companion_cannot_confirm_over_the_wire(client):
    ws, _ = desktop(client)
    try:
        token = client.post(
            "/api/pair/redeem", json={"code": get_code(ws)}
        ).json()["token"]
        with client.websocket_connect(f"/ws/companion?token={token}") as phone:
            phone.receive_json()
            phone.send_json({"type": "command", "command": "confirm",
                             "heldSeconds": 99})
            assert phone.receive_json()["type"] == "error"
    finally:
        ws.close()


def test_revoking_from_the_desktop_kicks_the_companion(client):
    ws, _ = desktop(client)
    try:
        token = client.post(
            "/api/pair/redeem", json={"code": get_code(ws)}
        ).json()["token"]

        with client.websocket_connect(f"/ws/companion?token={token}") as phone:
            phone.receive_json()

            ws.send_json({"type": "command", "command": "pair_revoke_all"})
            assert ws.receive_json()["revoked"] == 1

            # The token is re-checked on every message, not only at connect.
            phone.send_json({"type": "poll"})
            assert phone.receive_json()["message"] == "unpaired"
    finally:
        ws.close()


def test_a_code_for_a_session_that_ended_cannot_be_redeemed(client):
    ws, _ = desktop(client)
    code = get_code(ws)
    ws.close()

    res = client.post("/api/pair/redeem", json={"code": code})
    assert res.status_code in (403, 410)


def test_the_companion_page_is_served(client):
    res = client.get("/companion")
    assert res.status_code == 200
    assert "companion" in res.text
