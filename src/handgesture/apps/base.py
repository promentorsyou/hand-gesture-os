"""The application interface.

Phase 3 gave the spatial interface windows to put things in. Phase 4 fills
them. Every app here is a pure state machine driven by named actions:

    app.action("next")  ->  ActionResult(ok=True, state={...})

That shape is deliberate. It means one WebSocket command (``app_action``)
reaches every app, the mobile companion in Phase 5 gets the same surface for
free, and — most importantly — every app is fully testable without a
display, a webcam, or a desktop session.

Apps never call the OS adapter directly. When an app needs a real OS effect
(a media key, a keystroke, a file deletion) it returns an
:class:`OSRequest`, and the caller routes it through the existing dispatcher
and confirmation gate. That keeps the Phase 2 rule intact: there is exactly
one choke point where gestures become real OS effects.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class OSRequest:
    """An OS effect an app wants, to be routed through the dispatcher.

    ``operation`` matches the names the dispatcher and the confirmation gate
    already know, so sensitive operations stay sensitive by construction.
    """

    operation: str
    description: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionResult:
    """What an action did."""

    ok: bool = True
    #: Human-readable outcome, shown in the app's own UI.
    message: str = ""
    #: OS effects the caller should dispatch, in order.
    requests: tuple[OSRequest, ...] = ()
    #: Payload for actions that answer a question rather than change
    #: something — a keyboard layout, say. Kept out of ``state()`` so that
    #: large, rarely-changing data is not re-sent on every frame.
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def fail(cls, message: str) -> ActionResult:
        return cls(ok=False, message=message)


class App(ABC):
    """Base class for every application in the spatial interface."""

    #: Registry key, and the ``app`` name a window carries.
    name: str = "app"
    #: Shown in the window title bar.
    title: str = "App"

    @abstractmethod
    def state(self) -> dict[str, Any]:
        """Serialisable state for the UI and the mobile companion."""

    @abstractmethod
    def action(self, name: str, /, **payload: Any) -> ActionResult:
        """Perform a named action. Unknown names must fail, not raise.

        ``name`` is positional-only on purpose: apps take a payload key
        called ``name`` (a filename, say), and without the ``/`` that would
        collide with the action name itself.
        """

    #: Actions this app understands, for the UI and for discovery.
    @property
    def actions(self) -> tuple[str, ...]:
        return ()

    def unknown(self, name: str) -> ActionResult:
        return ActionResult.fail(f"{self.name}: unknown action {name!r}")


_REGISTRY: dict[str, type[App]] = {}


def register(cls: type[App]) -> type[App]:
    """Class decorator adding an app to the registry."""
    _REGISTRY[cls.name] = cls
    return cls


def available_apps() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def create_app(name: str, /, **kwargs: Any) -> App | None:
    """Instantiate a registered app, or ``None`` if there is no such app."""
    cls = _REGISTRY.get(name)
    return cls(**kwargs) if cls is not None else None
