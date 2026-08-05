"""The spatial interface: floating windows, multitasking, overlays."""

from .controller import SpatialAction, SpatialController
from .window import GrabKind, Rect, Window, WindowState
from .workspace import (
    CloseResult,
    MultitaskCard,
    Notification,
    Overlay,
    Workspace,
)

__all__ = [
    "CloseResult",
    "GrabKind",
    "MultitaskCard",
    "Notification",
    "Overlay",
    "Rect",
    "SpatialAction",
    "SpatialController",
    "Window",
    "WindowState",
    "Workspace",
]
