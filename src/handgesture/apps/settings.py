"""The settings app.

Everything a user needs to tune without touching a config file: gesture
sensitivity, dwell timing, the dominant hand, one-hand mode, and the
accessibility options.

Settings are validated on the way in. A gesture system where a bad value
silently disables recognition is a gesture system the user cannot recover
from — so an out-of-range value is rejected with a message rather than
clamped silently or stored as-is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import ActionResult, App, register


@dataclass(frozen=True, slots=True)
class Setting:
    key: str
    label: str
    kind: str            # "bool" | "float" | "choice"
    default: Any
    minimum: float = 0.0
    maximum: float = 1.0
    choices: tuple[str, ...] = ()
    help: str = ""

    def validate(self, value: Any) -> tuple[bool, Any, str]:
        if self.kind == "bool":
            if not isinstance(value, bool):
                return False, None, f"{self.key} must be true or false"
            return True, value, ""
        if self.kind == "float":
            try:
                number = float(value)
            except (TypeError, ValueError):
                return False, None, f"{self.key} must be a number"
            if not self.minimum <= number <= self.maximum:
                return (
                    False, None,
                    f"{self.key} must be between {self.minimum} and {self.maximum}",
                )
            return True, number, ""
        if value not in self.choices:
            return False, None, f"{self.key} must be one of {', '.join(self.choices)}"
        return True, value, ""


SETTINGS: tuple[Setting, ...] = (
    Setting("min_gesture_confidence", "Gesture confidence", "float", 0.55, 0.2, 0.95,
            help="How certain recognition must be before a gesture counts."),
    Setting("hold_seconds", "Hold time", "float", 0.08, 0.02, 1.0,
            help="How long a pose must be held before it fires."),
    Setting("cooldown_seconds", "Cooldown", "float", 0.35, 0.0, 2.0,
            help="Lockout after a gesture fires, to stop accidental repeats."),
    Setting("cursor_gain", "Cursor speed", "float", 1.0, 0.2, 3.0),
    Setting("dwell_seconds", "Keyboard dwell", "float", 0.7, 0.2, 3.0,
            help="How long to rest on a key before it types."),
    Setting("dominant_hand", "Dominant hand", "choice", "right",
            choices=("left", "right")),
    Setting("one_hand_mode", "One-hand mode", "bool", False,
            help="Use a single hand for everything, including two-hand gestures."),
    Setting("stop_on_hand_loss", "Stop when tracking is lost", "bool", True,
            help="Safer: an interrupted drag releases instead of dangling."),
    Setting("mirror_view", "Mirror the camera", "bool", True),
    Setting("high_contrast", "High contrast UI", "bool", False),
    Setting("large_targets", "Larger targets", "bool", False,
            help="Bigger keys and buttons, for less precise tracking."),
    Setting("reduce_motion", "Reduce motion", "bool", False),
)

_BY_KEY = {s.key: s for s in SETTINGS}


@register
class SettingsApp(App):
    """Reads and writes the user-tunable settings."""

    name = "settings"
    title = "Settings"

    def __init__(self) -> None:
        self.values: dict[str, Any] = {s.key: s.default for s in SETTINGS}

    @property
    def actions(self) -> tuple[str, ...]:
        return ("set", "reset", "reset_all")

    def state(self) -> dict[str, Any]:
        return {
            "values": dict(self.values),
            "schema": [
                {
                    "key": s.key,
                    "label": s.label,
                    "kind": s.kind,
                    "default": s.default,
                    "min": s.minimum,
                    "max": s.maximum,
                    "choices": list(s.choices),
                    "help": s.help,
                }
                for s in SETTINGS
            ],
        }

    def action(self, name: str, /, **payload: Any) -> ActionResult:
        if name == "set":
            key = str(payload.get("key", ""))
            setting = _BY_KEY.get(key)
            if setting is None:
                return ActionResult.fail(f"unknown setting {key!r}")
            ok, value, message = setting.validate(payload.get("value"))
            if not ok:
                return ActionResult.fail(message)
            self.values[key] = value
            return ActionResult(message=f"{key} = {value}")

        if name == "reset":
            key = str(payload.get("key", ""))
            setting = _BY_KEY.get(key)
            if setting is None:
                return ActionResult.fail(f"unknown setting {key!r}")
            self.values[key] = setting.default
            return ActionResult(message=f"{key} reset")

        if name == "reset_all":
            self.values = {s.key: s.default for s in SETTINGS}
            return ActionResult(message="all settings reset")

        return self.unknown(name)

    # --- Applying ---------------------------------------------------------

    def apply_to(self, pipeline) -> list[str]:
        """Push the current values onto a live pipeline.

        Returns the settings that were actually applied, so the caller can
        tell the user what took effect — several settings are consumed by
        the UI rather than the pipeline.
        """
        from ..types import Handedness

        config = pipeline.config
        applied: list[str] = []

        config.min_gesture_confidence = self.values["min_gesture_confidence"]
        pipeline.recognizer.min_confidence = self.values["min_gesture_confidence"]
        config.debounce.hold_seconds = self.values["hold_seconds"]
        config.debounce.cooldown_seconds = self.values["cooldown_seconds"]
        config.cursor.gain = self.values["cursor_gain"]
        config.cursor.mirror_x = self.values["mirror_view"]
        config.one_hand_mode = self.values["one_hand_mode"]
        config.stop_on_hand_loss = self.values["stop_on_hand_loss"]
        config.dominant_hand = (
            Handedness.LEFT if self.values["dominant_hand"] == "left"
            else Handedness.RIGHT
        )
        applied.extend(
            [
                "min_gesture_confidence", "hold_seconds", "cooldown_seconds",
                "cursor_gain", "mirror_view", "one_hand_mode",
                "stop_on_hand_loss", "dominant_hand",
            ]
        )
        return applied
