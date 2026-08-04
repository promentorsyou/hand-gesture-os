"""Emergency stop and the destructive-action confirmation gate."""

from __future__ import annotations

import pytest

from handgesture.control.safety import (
    ConfirmationGate,
    EmergencyStop,
    Sensitivity,
    StopReason,
)

# --- Emergency stop --------------------------------------------------------

def test_stop_starts_disengaged():
    stop = EmergencyStop()
    assert not stop.engaged
    assert stop.allows()


def test_engage_blocks_everything():
    stop = EmergencyStop()
    assert stop.engage(StopReason.GESTURE, now=1.0)
    assert stop.engaged
    assert not stop.allows()
    assert stop.reason is StopReason.GESTURE


def test_engage_is_idempotent():
    """A second crossed-hands must not overwrite the original reason."""
    stop = EmergencyStop()
    stop.engage(StopReason.GESTURE, now=1.0)
    assert stop.engage(StopReason.API, now=2.0) is False
    assert stop.reason is StopReason.GESTURE
    assert stop.engaged_at == 1.0


def test_release_restores_input():
    stop = EmergencyStop()
    stop.engage(StopReason.KEYBOARD, now=1.0)
    assert stop.release()
    assert stop.allows()
    assert stop.reason is None


def test_release_when_not_engaged_is_a_noop():
    assert EmergencyStop().release() is False


def test_listeners_are_notified():
    stop = EmergencyStop()
    seen: list[tuple[bool, StopReason | None]] = []
    stop.on_change(lambda engaged, reason: seen.append((engaged, reason)))

    stop.engage(StopReason.GESTURE, now=1.0)
    stop.release()

    assert seen == [(True, StopReason.GESTURE), (False, None)]


# --- Confirmation gate -----------------------------------------------------

def test_normal_operations_need_no_confirmation():
    gate = ConfirmationGate()
    assert not gate.requires_confirmation("cursor.move")
    assert gate.request("cursor.move", "move", now=0.0) is None


@pytest.mark.parametrize(
    "operation",
    [
        "file.delete",
        "system.shutdown",
        "purchase.make",
        "terminal.run",
        "password.enter",
    ],
)
def test_destructive_operations_require_confirmation(operation):
    gate = ConfirmationGate()
    assert gate.requires_confirmation(operation)
    pending = gate.request(operation, "do the thing", now=0.0)
    assert pending is not None
    assert pending.sensitivity is Sensitivity.CRITICAL


def test_critical_operations_need_a_longer_hold():
    gate = ConfirmationGate()
    critical = gate.request("file.delete", "delete", now=0.0)
    gate.cancel()
    normal = gate.request("form.submit", "submit", now=0.0)
    assert critical.hold_seconds > normal.hold_seconds


def test_confirm_requires_the_full_hold():
    gate = ConfirmationGate()
    pending = gate.request("file.delete", "delete a file", now=0.0)

    # A quick tap must not confirm a destructive action.
    assert gate.confirm(now=0.2, held_seconds=0.1) is None
    assert gate.pending is not None

    confirmed = gate.confirm(now=1.5, held_seconds=pending.hold_seconds)
    assert confirmed is not None
    assert confirmed.operation == "file.delete"
    assert gate.pending is None


def test_cancel_drops_the_operation():
    gate = ConfirmationGate()
    gate.request("file.delete", "delete", now=0.0)
    assert gate.cancel().operation == "file.delete"
    assert gate.pending is None
    assert gate.confirm(now=1.0, held_seconds=99.0) is None


def test_stale_confirmation_expires():
    """Walking away must not leave a live destructive confirmation."""
    gate = ConfirmationGate(timeout_seconds=5.0)
    gate.request("file.delete", "delete", now=0.0)
    assert gate.expire_if_stale(now=10.0)
    assert gate.pending is None


def test_expired_confirmation_cannot_be_confirmed():
    gate = ConfirmationGate(timeout_seconds=5.0)
    gate.request("system.shutdown", "shut down", now=0.0)
    assert gate.confirm(now=99.0, held_seconds=99.0) is None


def test_only_one_confirmation_outstanding():
    """Stacked dialogs are how people confirm the wrong thing."""
    gate = ConfirmationGate()
    gate.request("file.delete", "delete A", now=0.0)
    gate.request("system.restart", "restart", now=0.1)
    assert gate.pending.operation == "system.restart"
