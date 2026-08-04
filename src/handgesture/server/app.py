"""FastAPI + WebSocket server.

Architecture note: hand tracking runs in the *browser* (MediaPipe Hands via
WebAssembly), not in Python. The browser owns the webcam, streams landmarks
over a WebSocket, and this server runs the gesture pipeline and drives the
OS adapter.

That split is deliberate:

* the browser already has a permission-managed, cross-platform camera API,
  so we avoid per-OS webcam driver problems entirely
* the Python side keeps the OS automation, which a browser cannot do
* the whole pipeline stays testable headlessly, because landmarks can come
  from a test fixture just as easily as from a camera
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..calibration.calibrator import CalibrationProfile, Calibrator
from ..control.safety import StopReason
from ..gestures.vocabulary import Mode
from ..osadapter.base import OSAdapter
from ..osadapter.null import NullAdapter
from ..pipeline import GesturePipeline, PipelineConfig, PipelineState
from ..types import Frame, Hand, Handedness, Point

STATIC_DIR = Path(__file__).parent / "static"


def _hand_from_payload(raw: dict[str, Any]) -> Hand | None:
    """Parse one hand from the browser's landmark message."""
    pts = raw.get("landmarks") or []
    if len(pts) != 21:
        return None
    landmarks = tuple(
        Point(float(p["x"]), float(p["y"]), float(p.get("z", 0.0))) for p in pts
    )
    label = str(raw.get("handedness", "unknown")).lower()
    handedness = {
        "left": Handedness.LEFT,
        "right": Handedness.RIGHT,
    }.get(label, Handedness.UNKNOWN)
    return Hand(landmarks, handedness, float(raw.get("confidence", 1.0)))


def frame_from_payload(payload: dict[str, Any]) -> Frame:
    """Build a :class:`Frame` from a browser WebSocket message."""
    hands = []
    for raw in payload.get("hands", []):
        hand = _hand_from_payload(raw)
        if hand is not None:
            hands.append(hand)
    return Frame(
        hands=tuple(hands),
        timestamp=float(payload.get("timestamp", 0.0)),
        width=int(payload.get("width", 640)),
        height=int(payload.get("height", 480)),
        brightness=payload.get("brightness"),
    )


def state_to_payload(state: PipelineState) -> dict[str, Any]:
    """Serialise pipeline state for the UI."""
    return {
        "mode": state.mode.value,
        "gesture": state.gesture.value,
        "confidence": round(state.confidence, 3),
        "health": state.health.value,
        "handCount": state.hand_count,
        "emergencyStopped": state.emergency_stopped,
        "stopReason": state.stop_reason.value if state.stop_reason else None,
        "pendingConfirmation": state.pending_confirmation,
        "cursor": (
            {
                "x": round(state.cursor.x, 1),
                "y": round(state.cursor.y, 1),
                "speed": round(state.cursor.speed, 4),
                "precision": state.cursor.precision,
                "clamped": state.cursor.clamped,
            }
            if state.cursor
            else None
        ),
        "events": [
            {
                "type": e.type.value,
                "gesture": e.gesture.value,
                "confidence": round(e.confidence, 3),
                "duration": round(e.duration, 3),
            }
            for e in state.events
        ],
        "scores": {g.value: round(s, 3) for g, s in state.scores.items() if s > 0.01},
    }


class Session:
    """One connected client: its pipeline, calibrator, and adapter."""

    def __init__(self, adapter: OSAdapter | None = None) -> None:
        self.adapter = adapter or NullAdapter()
        screen = self.adapter.screen_info()
        config = PipelineConfig()
        config.cursor.screen_width = screen.width
        config.cursor.screen_height = screen.height
        self.pipeline = GesturePipeline(config)
        self.calibrator = Calibrator()
        self.profile = CalibrationProfile()

    def handle_frame(self, payload: dict[str, Any]) -> dict[str, Any]:
        frame = frame_from_payload(payload)

        # Calibration takes over the stream while it is running.
        if self.calibrator.active:
            if frame.hand_count:
                self.calibrator.add_sample(frame.hands[0])
            if not self.calibrator.active:
                self.profile = self.calibrator.build_profile()
            return {
                "type": "calibration",
                "step": self.calibrator.step.value,
                "prompt": self.calibrator.prompt,
                "progress": round(self.calibrator.progress, 3),
                "complete": not self.calibrator.active,
            }

        state = self.pipeline.process(frame)
        payload_out = state_to_payload(state)
        payload_out["type"] = "state"
        return payload_out

    def handle_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle a control message (mode change, stop, calibration...)."""
        command = payload.get("command")

        if command == "set_mode":
            try:
                self.pipeline.set_mode(Mode(payload["mode"]))
            except (KeyError, ValueError):
                return {"type": "error", "message": "unknown mode"}
            return {"type": "ack", "command": command, "mode": self.pipeline.mode.value}

        if command == "emergency_stop":
            self.pipeline.engage_stop(StopReason.API, float(payload.get("timestamp", 0)))
            return {"type": "ack", "command": command, "engaged": True}

        if command == "release_stop":
            self.pipeline.release_stop()
            return {"type": "ack", "command": command, "engaged": False}

        if command == "start_calibration":
            step = self.calibrator.start()
            return {
                "type": "calibration",
                "step": step.value,
                "prompt": self.calibrator.prompt,
                "progress": 0.0,
                "complete": False,
            }

        if command == "cancel_calibration":
            self.calibrator.cancel()
            return {"type": "ack", "command": command}

        if command == "get_profile":
            return {"type": "profile", "profile": self.profile.to_dict()}

        return {"type": "error", "message": f"unknown command: {command}"}


def create_app(adapter: OSAdapter | None = None):
    """Build the FastAPI app.

    The actual wiring lives in :mod:`handgesture.server.asgi`, imported
    lazily here so the core package stays importable (and testable) without
    FastAPI installed.
    """
    from .asgi import build_app

    return build_app(adapter)
