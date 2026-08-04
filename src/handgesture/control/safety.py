"""Emergency stop and the confirmation gate for destructive actions.

Two independent safety layers:

* :class:`EmergencyStop` — a hard kill switch. Once engaged, every gesture
  is dropped until it is explicitly released. It is deliberately *not*
  releasable by a gesture that could fire accidentally.
* :class:`ConfirmationGate` — sensitive operations never execute straight
  from a gesture; they queue here and wait for an explicit confirm.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StopReason(str, Enum):
    GESTURE = "gesture"           # crossed-hands gesture
    KEYBOARD = "keyboard"         # fallback hotkey
    API = "api"                   # programmatic / mobile companion
    TRACKING_LOST = "tracking_lost"
    LOW_LIGHT = "low_light"


@dataclass(slots=True)
class EmergencyStop:
    """Global kill switch for gesture input.

    While engaged, :meth:`allows` returns False for everything, so callers
    can gate at a single choke point rather than checking a flag in twenty
    places.
    """

    engaged: bool = False
    reason: StopReason | None = None
    engaged_at: float | None = None
    _listeners: list[Callable[[bool, StopReason | None], None]] = field(
        default_factory=list, repr=False
    )

    def engage(self, reason: StopReason, now: float | None = None) -> bool:
        """Engage the stop. Returns True if this call changed the state."""
        if self.engaged:
            return False
        self.engaged = True
        self.reason = reason
        self.engaged_at = now if now is not None else time.monotonic()
        self._notify()
        return True

    def release(self) -> bool:
        """Release the stop. Returns True if this call changed the state.

        Intentionally has no gesture binding by default — re-enabling gesture
        control from a gesture would defeat the purpose of the kill switch.
        The UI, the hotkey, and the mobile companion can all call this.
        """
        if not self.engaged:
            return False
        self.engaged = False
        self.reason = None
        self.engaged_at = None
        self._notify()
        return True

    def allows(self, _gesture: Any = None) -> bool:
        return not self.engaged

    def on_change(self, fn: Callable[[bool, StopReason | None], None]) -> None:
        self._listeners.append(fn)

    def _notify(self) -> None:
        for fn in self._listeners:
            fn(self.engaged, self.reason)


class Sensitivity(str, Enum):
    """How dangerous an operation is, which sets how it must be confirmed."""

    NORMAL = "normal"        # executes immediately
    CONFIRM = "confirm"      # needs an explicit confirm gesture
    CRITICAL = "critical"    # needs confirm + a longer deliberate hold


#: Operations that must never fire straight from a gesture. Everything the
#: brief listed as sensitive is here, mapped to how hard it is to confirm.
SENSITIVE_OPERATIONS: dict[str, Sensitivity] = {
    "file.delete": Sensitivity.CRITICAL,
    "file.move_permanent": Sensitivity.CRITICAL,
    "message.send": Sensitivity.CONFIRM,
    "form.submit": Sensitivity.CONFIRM,
    "purchase.make": Sensitivity.CRITICAL,
    "software.install": Sensitivity.CRITICAL,
    "terminal.run": Sensitivity.CRITICAL,
    "password.enter": Sensitivity.CRITICAL,
    "window.close_unsaved": Sensitivity.CONFIRM,
    "system.shutdown": Sensitivity.CRITICAL,
    "system.restart": Sensitivity.CRITICAL,
}


@dataclass(frozen=True, slots=True)
class PendingConfirmation:
    operation: str
    sensitivity: Sensitivity
    description: str
    requested_at: float
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def hold_seconds(self) -> float:
        """How long Confirm must be held before it counts."""
        return 1.2 if self.sensitivity is Sensitivity.CRITICAL else 0.4


class ConfirmationGate:
    """Queues sensitive operations until the user explicitly confirms.

    Only one confirmation can be outstanding at a time — stacking dialogs is
    exactly how people confirm the wrong thing.
    """

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: PendingConfirmation | None = None

    @property
    def pending(self) -> PendingConfirmation | None:
        return self._pending

    @staticmethod
    def sensitivity_of(operation: str) -> Sensitivity:
        return SENSITIVE_OPERATIONS.get(operation, Sensitivity.NORMAL)

    def requires_confirmation(self, operation: str) -> bool:
        return self.sensitivity_of(operation) is not Sensitivity.NORMAL

    def request(
        self,
        operation: str,
        description: str,
        now: float,
        payload: dict[str, Any] | None = None,
    ) -> PendingConfirmation | None:
        """Queue an operation for confirmation.

        Returns the pending confirmation, or ``None`` when the operation is
        not sensitive and the caller should just execute it.
        """
        sensitivity = self.sensitivity_of(operation)
        if sensitivity is Sensitivity.NORMAL:
            return None

        self._pending = PendingConfirmation(
            operation=operation,
            sensitivity=sensitivity,
            description=description,
            requested_at=now,
            payload=payload or {},
        )
        return self._pending

    def expire_if_stale(self, now: float) -> bool:
        """Drop a confirmation the user walked away from. True if dropped."""
        p = self._pending
        if p is not None and (now - p.requested_at) > self.timeout_seconds:
            self._pending = None
            return True
        return False

    def confirm(self, now: float, held_seconds: float) -> PendingConfirmation | None:
        """Confirm the pending operation if it has been held long enough."""
        p = self._pending
        if p is None:
            return None
        if self.expire_if_stale(now):
            return None
        if held_seconds < p.hold_seconds:
            return None
        self._pending = None
        return p

    def cancel(self) -> PendingConfirmation | None:
        p = self._pending
        self._pending = None
        return p
