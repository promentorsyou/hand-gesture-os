"""A no-op adapter that records everything instead of doing it.

This is what makes the whole system testable without a desktop. It supports
every capability, performs no real action, and keeps an ordered log of the
calls it received — so tests can assert on exactly what the gesture
pipeline *would* have done.

It is also the adapter used by simulation and demo modes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import Capability, MouseButton, OSAdapter, ScreenInfo, WindowInfo


@dataclass(frozen=True, slots=True)
class RecordedCall:
    action: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        parts = [repr(a) for a in self.args]
        parts += [f"{k}={v!r}" for k, v in self.kwargs.items()]
        return f"{self.action}({', '.join(parts)})"


class NullAdapter(OSAdapter):
    """Records actions; performs none of them."""

    name = "null"

    def __init__(self, screen_width: int = 1920, screen_height: int = 1080) -> None:
        self._screen = ScreenInfo(screen_width, screen_height)
        self._cursor = (screen_width // 2, screen_height // 2)
        self._volume = 0.5
        self._muted = False
        self._windows: list[WindowInfo] = []
        self.calls: list[RecordedCall] = []

    # --- Test helpers --------------------------------------------------
    def _record(self, action: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(RecordedCall(action, args, kwargs))

    def clear(self) -> None:
        self.calls.clear()

    def actions(self) -> list[str]:
        return [c.action for c in self.calls]

    def count(self, action: str) -> int:
        return sum(1 for c in self.calls if c.action == action)

    def last(self, action: str | None = None) -> RecordedCall | None:
        for call in reversed(self.calls):
            if action is None or call.action == action:
                return call
        return None

    def add_window(self, window: WindowInfo) -> None:
        """Seed a fake window so window-management tests have something."""
        self._windows.append(window)

    # --- Capabilities --------------------------------------------------
    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(Capability)

    # --- Screen --------------------------------------------------------
    def screen_info(self) -> ScreenInfo:
        return self._screen

    # --- Cursor --------------------------------------------------------
    def move_cursor(self, x: int, y: int) -> None:
        self._cursor = (int(x), int(y))
        self._record("move_cursor", int(x), int(y))

    def cursor_position(self) -> tuple[int, int]:
        return self._cursor

    def click(self, button: MouseButton = MouseButton.LEFT, count: int = 1) -> None:
        self._record("click", button.value, count=count)

    def mouse_down(self, button: MouseButton = MouseButton.LEFT) -> None:
        self._record("mouse_down", button.value)

    def mouse_up(self, button: MouseButton = MouseButton.LEFT) -> None:
        self._record("mouse_up", button.value)

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        self._record("scroll", dx, dy)

    # --- Keyboard ------------------------------------------------------
    def type_text(self, text: str) -> None:
        self._record("type_text", text)

    def press_keys(self, *keys: str) -> None:
        self._record("press_keys", *keys)

    # --- Windows -------------------------------------------------------
    def list_windows(self) -> list[WindowInfo]:
        return list(self._windows)

    def focus_window(self, handle: str) -> None:
        self._record("focus_window", handle)

    def move_window(self, handle: str, x: int, y: int) -> None:
        self._record("move_window", handle, x, y)

    def resize_window(self, handle: str, width: int, height: int) -> None:
        self._record("resize_window", handle, width, height)

    def minimize_window(self, handle: str) -> None:
        self._record("minimize_window", handle)

    def maximize_window(self, handle: str) -> None:
        self._record("maximize_window", handle)

    def close_window(self, handle: str) -> None:
        self._record("close_window", handle)

    # --- System --------------------------------------------------------
    def screenshot(self, path: str | None = None) -> str:
        target = path or "/tmp/simulated-screenshot.png"
        self._record("screenshot", target)
        return target

    def set_volume(self, level: float) -> None:
        self._volume = min(1.0, max(0.0, level))
        self._record("set_volume", self._volume)

    def get_volume(self) -> float:
        return self._volume

    def mute(self, muted: bool = True) -> None:
        self._muted = muted
        self._record("mute", muted)

    def media_key(self, key: str) -> None:
        self._record("media_key", key)

    def launch_app(self, name: str) -> None:
        self._record("launch_app", name)

    def lock_screen(self) -> None:
        self._record("lock_screen")
