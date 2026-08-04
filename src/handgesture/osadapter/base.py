"""The operating-system adapter interface.

Every platform-specific action in the whole system goes through this one
interface. Nothing above this layer imports ``pyautogui``, ``win32api``,
``Quartz``, or ``Xlib`` — which is what lets the entire gesture pipeline be
tested on a headless machine against :class:`~.null.NullAdapter`.

IMPORTANT: the concrete platform adapters cannot be verified in CI or in a
container — they need a real desktop session, a real cursor, and real
windows. They are marked UNVERIFIED until run on target hardware.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class MouseButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class Capability(str, Enum):
    """What a given adapter can actually do.

    Callers check capabilities instead of catching ``NotImplementedError``,
    so the UI can grey out actions the current platform cannot perform.
    """

    CURSOR = "cursor"
    CLICK = "click"
    SCROLL = "scroll"
    KEYBOARD = "keyboard"
    CLIPBOARD = "clipboard"
    WINDOW_MANAGEMENT = "window_management"
    APP_LAUNCH = "app_launch"
    FILE_OPERATIONS = "file_operations"
    SCREENSHOT = "screenshot"
    VOLUME = "volume"
    MEDIA_KEYS = "media_keys"
    LOCK_SCREEN = "lock_screen"


@dataclass(frozen=True, slots=True)
class WindowInfo:
    handle: str
    title: str
    x: int
    y: int
    width: int
    height: int
    is_minimized: bool = False
    is_maximized: bool = False
    is_focused: bool = False
    app_name: str = ""


@dataclass(slots=True)
class ScreenInfo:
    width: int
    height: int
    scale: float = 1.0


class OSAdapter(ABC):
    """Platform-agnostic desktop automation surface.

    Subclasses implement whatever their platform supports and declare it via
    :attr:`capabilities`.
    """

    name: str = "abstract"

    @property
    @abstractmethod
    def capabilities(self) -> frozenset[Capability]:
        """What this adapter can do on the current system."""

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    # --- Screen -------------------------------------------------------
    @abstractmethod
    def screen_info(self) -> ScreenInfo: ...

    # --- Cursor and clicks --------------------------------------------
    @abstractmethod
    def move_cursor(self, x: int, y: int) -> None: ...

    @abstractmethod
    def cursor_position(self) -> tuple[int, int]: ...

    @abstractmethod
    def click(self, button: MouseButton = MouseButton.LEFT, count: int = 1) -> None: ...

    @abstractmethod
    def mouse_down(self, button: MouseButton = MouseButton.LEFT) -> None: ...

    @abstractmethod
    def mouse_up(self, button: MouseButton = MouseButton.LEFT) -> None: ...

    @abstractmethod
    def scroll(self, dx: int = 0, dy: int = 0) -> None: ...

    # --- Keyboard ------------------------------------------------------
    @abstractmethod
    def type_text(self, text: str) -> None: ...

    @abstractmethod
    def press_keys(self, *keys: str) -> None:
        """Press a chord, e.g. ``press_keys("ctrl", "c")``."""

    # --- Clipboard -----------------------------------------------------
    def copy(self) -> None:
        self.press_keys(self._mod(), "c")

    def cut(self) -> None:
        self.press_keys(self._mod(), "x")

    def paste(self) -> None:
        self.press_keys(self._mod(), "v")

    def _mod(self) -> str:
        """The platform's primary modifier key."""
        return "ctrl"

    # --- Windows -------------------------------------------------------
    def list_windows(self) -> list[WindowInfo]:
        raise NotImplementedError(f"{self.name} does not support window management")

    def focus_window(self, handle: str) -> None:
        raise NotImplementedError(f"{self.name} does not support window management")

    def move_window(self, handle: str, x: int, y: int) -> None:
        raise NotImplementedError(f"{self.name} does not support window management")

    def resize_window(self, handle: str, width: int, height: int) -> None:
        raise NotImplementedError(f"{self.name} does not support window management")

    def minimize_window(self, handle: str) -> None:
        raise NotImplementedError(f"{self.name} does not support window management")

    def maximize_window(self, handle: str) -> None:
        raise NotImplementedError(f"{self.name} does not support window management")

    def close_window(self, handle: str) -> None:
        raise NotImplementedError(f"{self.name} does not support window management")

    # --- System --------------------------------------------------------
    def screenshot(self, path: str | None = None) -> str:
        raise NotImplementedError(f"{self.name} does not support screenshots")

    def set_volume(self, level: float) -> None:
        raise NotImplementedError(f"{self.name} does not support volume control")

    def get_volume(self) -> float:
        raise NotImplementedError(f"{self.name} does not support volume control")

    def mute(self, muted: bool = True) -> None:
        raise NotImplementedError(f"{self.name} does not support volume control")

    def media_key(self, key: str) -> None:
        """``play_pause`` / ``next`` / ``previous`` / ``stop``."""
        raise NotImplementedError(f"{self.name} does not support media keys")

    def launch_app(self, name: str) -> None:
        raise NotImplementedError(f"{self.name} does not support app launching")

    def lock_screen(self) -> None:
        raise NotImplementedError(f"{self.name} does not support locking")


def get_adapter(platform_name: str | None = None) -> OSAdapter:
    """Return the adapter for this platform.

    Falls back to the null adapter whenever the real one cannot be
    constructed (missing dependency, headless session, unsupported OS), so
    the app always starts — in simulation rather than failing outright.
    """
    import platform as _platform

    from .null import NullAdapter

    system = (platform_name or _platform.system()).lower()

    try:
        if system in ("windows", "win32"):
            from .windows import WindowsAdapter

            return WindowsAdapter()
        if system == "darwin":
            from .macos import MacOSAdapter

            return MacOSAdapter()
        if system == "linux":
            from .linux import LinuxAdapter

            return LinuxAdapter()
    except Exception:  # pragma: no cover - depends on host environment
        return NullAdapter()

    return NullAdapter()
