"""What a paired phone is allowed to do.

The companion speaks the same command protocol as the desktop UI, but
through an **allowlist** rather than the full surface. Two rules shape it:

* **A companion can stop, but cannot confirm.** The emergency stop is
  reachable from the phone — that is arguably the single most useful thing a
  second screen offers. Confirming a destructive operation is not: the
  design requires a *visible gesture* in front of the camera before anything
  irreversible happens, and a phone tap is not that. A companion may
  **cancel** a pending confirmation, because cancelling is always the safe
  direction.
* **A companion cannot choose a sandbox root.** ``open_app`` is allowed but
  its ``appKwargs`` are stripped, so a phone cannot point the file browser
  at ``/``.

Anything not on the list is refused with a message rather than silently
ignored, so a mismatched client version is obvious instead of mysterious.
"""

from __future__ import annotations

from typing import Any

#: Commands a paired companion may send.
COMPANION_COMMANDS = frozenset(
    {
        # Safety and mode
        "emergency_stop",
        "release_stop",
        "set_mode",
        "cancel_confirmation",
        # Calibration, driven from the phone while you stand at the camera
        "start_calibration",
        "cancel_calibration",
        "get_profile",
        # Workspace
        "open_app",
        "focus_window",
        "close_window",
        "window_state",
        "set_overlay",
        "app_action",
        "list_apps",
        # Settings and notifications
        "toggle_setting",
        "clear_notifications",
    }
)

#: Commands deliberately refused, with the reason shown to the user.
COMPANION_DENIED = {
    "confirm": (
        "confirmation needs a gesture in front of the camera, not a phone tap"
    ),
    "confirm_close": (
        "confirmation needs a gesture in front of the camera, not a phone tap"
    ),
}


class CompanionBridge:
    """A restricted view of one desktop session, for a paired device."""

    def __init__(self, session: Any, label: str = "companion") -> None:
        self.session = session
        self.label = label

    # --- Reading ----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """A compact status view, sized for a phone screen.

        Deliberately *not* the desktop's full state payload: per-frame
        gesture scores and landmark data are useless on a phone and would
        dominate the link.
        """
        pipeline = self.session.pipeline
        workspace = self.session.spatial.workspace
        pending = pipeline.confirmation.pending

        return {
            "type": "companion_state",
            "mode": pipeline.mode.value,
            "emergencyStopped": pipeline.emergency_stop.engaged,
            "stopReason": (
                pipeline.emergency_stop.reason.value
                if pipeline.emergency_stop.reason
                else None
            ),
            "calibrating": self.session.calibrator.active,
            "calibrationStep": self.session.calibrator.step.value,
            "calibrationProgress": round(self.session.calibrator.progress, 3),
            "adapter": self.session.adapter.name,
            "pendingConfirmation": (
                {
                    "operation": pending.operation,
                    "sensitivity": pending.sensitivity.value,
                    "description": pending.description,
                }
                if pending
                else None
            ),
            "windows": [
                {
                    "id": w.id,
                    "app": w.app,
                    "title": w.title,
                    "state": w.state.value,
                    "focused": workspace.focused is not None
                    and workspace.focused.id == w.id,
                }
                for w in sorted(workspace.windows, key=lambda w: w.z, reverse=True)
            ],
            "overlay": workspace.overlay.value,
            "quickSettings": dict(workspace.quick_settings),
            "unread": workspace.unread_count,
            "notifications": [
                {"app": n.app, "title": n.title, "body": n.body}
                for n in list(workspace.notifications)[:10]
            ],
        }

    # --- Writing ----------------------------------------------------------

    def command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run a command if the companion is allowed to."""
        name = str(payload.get("command", ""))

        if name in COMPANION_DENIED:
            return {
                "type": "error",
                "command": name,
                "message": COMPANION_DENIED[name],
            }
        if name not in COMPANION_COMMANDS:
            return {
                "type": "error",
                "command": name,
                "message": f"{name!r} is not available from a companion device",
            }

        if name == "open_app":
            # A phone must not get to choose the file browser's root.
            payload = {k: v for k, v in payload.items() if k != "appKwargs"}

        reply = self.session.handle_command(payload)
        reply["viaCompanion"] = True
        return reply
