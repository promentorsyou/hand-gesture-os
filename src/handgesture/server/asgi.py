"""FastAPI wiring.

Separate from ``app.py`` on purpose. FastAPI resolves route annotations with
``get_type_hints()`` against the *module* globals, so ``WebSocket`` must be
imported at module level — importing it inside a factory function leaves the
annotation unresolvable and FastAPI silently reinterprets the parameter as a
query field, which rejects every connection with a 1008 close.

Keeping FastAPI imports here (and importing this module lazily from
``app.create_app``) means the core package still works without FastAPI
installed, while route annotations remain resolvable.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..companion.pairing import PairingError, PairingService
from ..companion.remote import COMPANION_COMMANDS, CompanionBridge
from ..gestures.vocabulary import MODE_GESTURES, Gesture
from ..osadapter.base import OSAdapter
from ..osadapter.null import NullAdapter
from ..pipeline import PipelineConfig
from .app import STATIC_DIR, Session

#: FastAPI needs the marker in the signature; ruff rightly objects to a
#: call in a default, so it lives here as a singleton.
_JSON_BODY = Body(default_factory=dict)


def build_app(adapter: OSAdapter | None = None,
              config: PipelineConfig | None = None) -> FastAPI:
    """Construct the ASGI application."""
    app = FastAPI(title="hand-gesture-os", version="0.1.0")
    shared_adapter = adapter or NullAdapter()

    # Live desktop sessions, so a paired phone can find the one it belongs
    # to. Keyed by the session's random id — a companion cannot enumerate
    # or guess its way to another session.
    sessions: dict[str, Session] = {}
    pairing = PairingService()
    app.state.sessions = sessions
    app.state.pairing = pairing

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "adapter": shared_adapter.name,
            "capabilities": sorted(c.value for c in shared_adapter.capabilities),
        }

    @app.get("/api/gestures")
    async def gestures() -> dict[str, Any]:
        return {
            "gestures": [g.value for g in Gesture],
            "modes": {
                m.value: sorted(g.value for g in gs) for m, gs in MODE_GESTURES.items()
            },
        }

    @app.post("/api/pair/redeem")
    async def pair_redeem(body: dict[str, Any] = _JSON_BODY) -> dict[str, Any]:
        """Exchange a pairing code for a companion token.

        The only unauthenticated write endpoint in the server, which is why
        the pairing service rate-limits it. A refusal is a 403 with the
        reason, never a hint about whether the code merely expired.
        """
        import time

        try:
            token = pairing.redeem(
                str(body.get("code", "")),
                time.time(),
                label=str(body.get("label", "companion")),
            )
        except PairingError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        if token.session_id not in sessions:
            pairing.revoke(token.token)
            raise HTTPException(status_code=410, detail="that session has ended")

        return {"token": token.token, "label": token.label}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        session = Session(shared_adapter, config=config)
        sessions[session.id] = session
        await websocket.send_json({"type": "hello", "sessionId": session.id})
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "bad json"})
                    continue

                if payload.get("type") == "command":
                    reply = _pairing_command(session, payload)
                    if reply is None:
                        reply = session.handle(payload)
                else:
                    reply = session.handle(payload)
                await websocket.send_json(reply)
        except WebSocketDisconnect:
            pass
        finally:
            # A companion outlives nothing: when the desktop session goes,
            # its tokens go with it.
            sessions.pop(session.id, None)
            pairing.cancel_code(session.id)
            pairing.revoke_session(session.id)

    def _pairing_command(session: Session, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Handle the pairing commands, which need the registry.

        Returns ``None`` for anything that is the session's own business.
        """
        import time

        command = payload.get("command")
        now = float(payload.get("timestamp") or time.time())

        if command == "pair_start":
            code = pairing.create_code(session.id, time.time())
            return {
                "type": "pairing",
                "code": code.code,
                "expiresIn": round(code.expires_at - time.time()),
                "companions": [c.label for c in pairing.companions(session.id)],
            }

        if command == "pair_cancel":
            return {"type": "pairing", "cancelled": pairing.cancel_code(session.id),
                    "code": None}

        if command == "pair_status":
            pending = pairing.pending_code(session.id, time.time())
            return {
                "type": "pairing",
                "code": pending.code if pending else None,
                "expiresIn": round(pending.expires_at - time.time()) if pending else 0,
                "companions": [c.label for c in pairing.companions(session.id)],
                "lockedOut": pairing.locked_out(now),
            }

        if command == "pair_revoke_all":
            return {"type": "pairing", "revoked": pairing.revoke_session(session.id),
                    "companions": []}

        return None

    @app.websocket("/ws/companion")
    async def companion_endpoint(websocket: WebSocket, token: str = "") -> None:
        """The paired-device socket.

        Authorisation happens before ``accept()``: an unauthorised client
        gets a close, never a live socket it can send commands down.
        """
        entry = pairing.verify(token)
        session = sessions.get(entry.session_id) if entry else None
        if entry is None or session is None:
            await websocket.close(code=1008)
            return

        await websocket.accept()
        bridge = CompanionBridge(session, entry.label)
        await websocket.send_json(
            {"type": "hello", "label": entry.label,
             "commands": sorted(COMPANION_COMMANDS)}
        )
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "bad json"})
                    continue

                # The token can be revoked from the desktop mid-session, so
                # it is re-checked on every message rather than only at
                # connect time.
                if pairing.verify(token) is None or entry.session_id not in sessions:
                    await websocket.send_json({"type": "error", "message": "unpaired"})
                    await websocket.close(code=1008)
                    return

                if payload.get("type") == "command":
                    reply = bridge.command(payload)
                    reply["state"] = bridge.snapshot()
                    await websocket.send_json(reply)
                else:
                    await websocket.send_json(bridge.snapshot())
        except WebSocketDisconnect:
            pass

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/companion")
        async def companion_page() -> FileResponse:
            return FileResponse(STATIC_DIR / "companion.html")

    return app
