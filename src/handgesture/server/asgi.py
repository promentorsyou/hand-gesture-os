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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..gestures.vocabulary import MODE_GESTURES, Gesture
from ..osadapter.base import OSAdapter
from ..osadapter.null import NullAdapter
from .app import STATIC_DIR, Session


def build_app(adapter: OSAdapter | None = None) -> FastAPI:
    """Construct the ASGI application."""
    app = FastAPI(title="hand-gesture-os", version="0.1.0")
    shared_adapter = adapter or NullAdapter()

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

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        session = Session(shared_adapter)
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "bad json"})
                    continue

                if payload.get("type") == "command":
                    await websocket.send_json(session.handle_command(payload))
                else:
                    await websocket.send_json(session.handle_frame(payload))
        except WebSocketDisconnect:
            pass

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app
