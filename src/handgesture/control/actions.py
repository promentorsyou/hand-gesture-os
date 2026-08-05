"""Dispatching resolved intents to the operating system.

This is the only place where gestures become real OS effects. Keeping it in
one module means there is exactly one choke point to audit for safety, and
exactly one seam to swap for the recording NullAdapter in tests.

Two rules hold everywhere in here:

1. **Nothing executes while the emergency stop is engaged.** Checked at the
   top of dispatch, not per-action, so a new action cannot forget to check.
2. **Destructive operations never execute directly.** They go through the
   confirmation gate and wait for an explicit confirm.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..gestures.compound import ClickAction, ClickEvent
from ..gestures.motion import MotionResult
from ..gestures.vocabulary import Gesture, Mode
from ..osadapter.base import Capability, MouseButton, OSAdapter
from .safety import ConfirmationGate, EmergencyStop


@dataclass(frozen=True, slots=True)
class DispatchedAction:
    """A record of what was dispatched, for the UI and for tests."""

    name: str
    detail: str = ""
    executed: bool = True
    #: Set when the action was queued for confirmation rather than run.
    awaiting_confirmation: bool = False


#: Which OS action each motion gesture maps to, per mode. Modes that omit a
#: gesture simply ignore it — the mode table in vocabulary.py already gates
#: what reaches here, this decides what it *means*.
_SWIPE_ACTIONS: dict[Mode, dict[Gesture, tuple[str, ...]]] = {
    Mode.BROWSER: {
        Gesture.SWIPE_LEFT: ("alt", "left"),
        Gesture.SWIPE_RIGHT: ("alt", "right"),
    },
    Mode.NAVIGATION: {
        Gesture.SWIPE_LEFT: ("alt", "left"),
        Gesture.SWIPE_RIGHT: ("alt", "right"),
    },
    Mode.MEDIA: {},  # handled separately as media keys
}


class ActionDispatcher:
    """Turns resolved intents into OS adapter calls."""

    def __init__(
        self,
        adapter: OSAdapter,
        emergency_stop: EmergencyStop,
        confirmation: ConfirmationGate,
    ) -> None:
        self.adapter = adapter
        self.emergency_stop = emergency_stop
        self.confirmation = confirmation
        self.history: list[DispatchedAction] = []
        #: Operations owned by something above this layer (an app, say),
        #: registered here so they still pass through the confirmation gate
        #: and the emergency stop instead of inventing a second path.
        self.extra_handlers: dict[str, Callable[[dict[str, Any]], str]] = {}

    def register_operation(
        self, operation: str, handler: Callable[[dict[str, Any]], str]
    ) -> None:
        """Route ``operation`` to ``handler``. The handler returns a detail string."""
        self.extra_handlers[operation] = handler

    def _record(self, action: DispatchedAction) -> DispatchedAction:
        self.history.append(action)
        # Bound the log: this runs for the life of the session.
        if len(self.history) > 200:
            del self.history[:100]
        return action

    def _blocked(self) -> bool:
        return self.emergency_stop.engaged

    # --- Pointer -------------------------------------------------------

    def move_cursor(self, x: float, y: float) -> DispatchedAction | None:
        if self._blocked() or not self.adapter.supports(Capability.CURSOR):
            return None
        self.adapter.move_cursor(int(x), int(y))
        # Deliberately not recorded: cursor movement happens every frame and
        # would drown the history that matters.
        return None

    def dispatch_clicks(self, events: list[ClickEvent]) -> list[DispatchedAction]:
        """Execute resolved pointer actions."""
        out: list[DispatchedAction] = []
        if self._blocked():
            return out
        if not self.adapter.supports(Capability.CLICK):
            return out

        for event in events:
            action = event.action
            if action is ClickAction.CLICK:
                self.adapter.click(MouseButton.LEFT)
                out.append(self._record(DispatchedAction("click")))
            elif action is ClickAction.DOUBLE_CLICK:
                self.adapter.click(MouseButton.LEFT, count=2)
                out.append(self._record(DispatchedAction("double_click")))
            elif action is ClickAction.RIGHT_CLICK:
                self.adapter.click(MouseButton.RIGHT)
                out.append(self._record(DispatchedAction("right_click")))
            elif action in (ClickAction.HOLD_BEGIN, ClickAction.DRAG_BEGIN):
                # DRAG_BEGIN follows HOLD_BEGIN, and the button is already
                # down by then — pressing again would be wrong.
                if action is ClickAction.HOLD_BEGIN:
                    self.adapter.mouse_down(MouseButton.LEFT)
                    out.append(self._record(DispatchedAction("mouse_down")))
                else:
                    out.append(self._record(DispatchedAction("drag_begin")))
            elif action in (ClickAction.HOLD_END, ClickAction.DRAG_END):
                self.adapter.mouse_up(MouseButton.LEFT)
                out.append(self._record(DispatchedAction("mouse_up")))
            # DRAG_MOVE needs no adapter call: the cursor is already being
            # moved every frame and the button is held.

        return out

    # --- Motion --------------------------------------------------------

    def dispatch_motion(self, motion: MotionResult, mode: Mode) -> list[DispatchedAction]:
        """Execute scroll, zoom, rotate, and swipe actions."""
        out: list[DispatchedAction] = []
        if self._blocked():
            return out

        dx, dy = motion.scroll
        if (dx or dy) and self.adapter.supports(Capability.SCROLL):
            self.adapter.scroll(int(dx), int(dy))
            out.append(self._record(DispatchedAction("scroll", f"{int(dx)},{int(dy)}")))

        if motion.zoom and self.adapter.supports(Capability.KEYBOARD):
            key = "plus" if motion.zoom > 0 else "minus"
            self.adapter.press_keys(self.adapter._mod(), key)
            out.append(self._record(DispatchedAction("zoom", key)))

        if motion.rotate and mode is Mode.MEDIA:
            out.extend(self._rotate_volume(motion.rotate))

        if motion.gesture is not Gesture.NONE:
            out.extend(self._dispatch_swipe(motion.gesture, mode))

        return out

    def _rotate_volume(self, delta: float) -> list[DispatchedAction]:
        if not self.adapter.supports(Capability.VOLUME):
            return []
        try:
            current = self.adapter.get_volume()
        except NotImplementedError:
            return []
        # Roughly a full sweep of the wrist for the full volume range.
        target = min(1.0, max(0.0, current + delta * 0.35))
        self.adapter.set_volume(target)
        return [self._record(DispatchedAction("set_volume", f"{target:.2f}"))]

    def _dispatch_swipe(self, gesture: Gesture, mode: Mode) -> list[DispatchedAction]:
        if mode is Mode.MEDIA and self.adapter.supports(Capability.MEDIA_KEYS):
            key = {
                Gesture.SWIPE_LEFT: "previous",
                Gesture.SWIPE_RIGHT: "next",
            }.get(gesture)
            if key:
                self.adapter.media_key(key)
                return [self._record(DispatchedAction("media_key", key))]
            return []

        if gesture is Gesture.SWIPE_UP:
            return self._multitask()
        if gesture is Gesture.SWIPE_DOWN:
            return self._minimize()

        chord = _SWIPE_ACTIONS.get(mode, {}).get(gesture)
        if chord and self.adapter.supports(Capability.KEYBOARD):
            self.adapter.press_keys(*chord)
            return [self._record(DispatchedAction("press_keys", "+".join(chord)))]
        return []

    def _multitask(self) -> list[DispatchedAction]:
        if not self.adapter.supports(Capability.KEYBOARD):
            return []
        # Platform-appropriate "show all windows".
        chord = ("command", "up") if self.adapter.name == "macos" else ("super",)
        self.adapter.press_keys(*chord)
        return [self._record(DispatchedAction("multitask"))]

    def _minimize(self) -> list[DispatchedAction]:
        if not self.adapter.supports(Capability.KEYBOARD):
            return []
        chord = ("command", "m") if self.adapter.name == "macos" else ("super", "down")
        self.adapter.press_keys(*chord)
        return [self._record(DispatchedAction("minimize"))]

    # --- Clipboard and confirmed operations ----------------------------

    def clipboard(self, operation: str) -> DispatchedAction | None:
        """``copy`` / ``cut`` / ``paste``."""
        if self._blocked() or not self.adapter.supports(Capability.CLIPBOARD):
            return None
        fn = {"copy": self.adapter.copy, "cut": self.adapter.cut, "paste": self.adapter.paste}
        if operation not in fn:
            return None
        fn[operation]()
        return self._record(DispatchedAction(f"clipboard.{operation}"))

    def request(
        self,
        operation: str,
        description: str,
        now: float,
        payload: dict[str, Any] | None = None,
    ) -> DispatchedAction:
        """Run an operation, or queue it for confirmation if it is sensitive.

        Every destructive path in the system should come through here rather
        than calling the adapter directly.
        """
        if self._blocked():
            return self._record(
                DispatchedAction(operation, "blocked by emergency stop", executed=False)
            )

        pending = self.confirmation.request(operation, description, now, payload)
        if pending is not None:
            return self._record(
                DispatchedAction(
                    operation,
                    f"awaiting {pending.sensitivity.value} confirmation",
                    executed=False,
                    awaiting_confirmation=True,
                )
            )

        return self._execute(operation, payload or {})

    def confirm_pending(self, now: float, held_seconds: float) -> DispatchedAction | None:
        """Execute the pending operation if the confirm hold is long enough."""
        confirmed = self.confirmation.confirm(now, held_seconds)
        if confirmed is None:
            return None
        return self._execute(confirmed.operation, confirmed.payload)

    def cancel_pending(self) -> DispatchedAction | None:
        cancelled = self.confirmation.cancel()
        if cancelled is None:
            return None
        return self._record(
            DispatchedAction(cancelled.operation, "cancelled", executed=False)
        )

    def _execute(self, operation: str, payload: dict[str, Any]) -> DispatchedAction:
        """Perform a (already-authorised) named operation."""
        extra = self.extra_handlers.get(operation)
        if extra is not None:
            try:
                detail = extra(payload)
            except NotImplementedError as exc:
                return self._record(DispatchedAction(operation, str(exc), executed=False))
            return self._record(DispatchedAction(operation, detail))

        handlers = {
            "keyboard.type": lambda: self.adapter.type_text(str(payload.get("text", ""))),
            "keyboard.chord": lambda: self.adapter.press_keys(*payload.get("keys", [])),
            "audio.set_volume": lambda: self.adapter.set_volume(
                float(payload.get("level", 0.0))
            ),
            "system.screenshot": lambda: self.adapter.screenshot(payload.get("path")),
            "system.lock": self.adapter.lock_screen,
            "media.play_pause": lambda: self.adapter.media_key("play_pause"),
            "media.next": lambda: self.adapter.media_key("next"),
            "media.previous": lambda: self.adapter.media_key("previous"),
            "audio.mute": lambda: self.adapter.mute(True),
            "audio.unmute": lambda: self.adapter.mute(False),
            "app.launch": lambda: self.adapter.launch_app(payload.get("name", "")),
        }
        handler = handlers.get(operation)
        if handler is None:
            return self._record(
                DispatchedAction(operation, "no handler", executed=False)
            )
        try:
            handler()
        except NotImplementedError as exc:
            return self._record(DispatchedAction(operation, str(exc), executed=False))
        return self._record(DispatchedAction(operation))
