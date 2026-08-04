"""Windows adapter.

STATUS: UNVERIFIED. Written against the documented pywin32 / pyautogui APIs
but never executed on Windows from this project's CI (the build container is
headless Linux). Treat every method here as untested until run on a real
Windows desktop.

Install extras with: ``pip install -e ".[windows]"``
"""

from __future__ import annotations

from .base import Capability, MouseButton, OSAdapter, ScreenInfo, WindowInfo

_BUTTON = {
    MouseButton.LEFT: "left",
    MouseButton.RIGHT: "right",
    MouseButton.MIDDLE: "middle",
}


class WindowsAdapter(OSAdapter):
    name = "windows"

    def __init__(self) -> None:
        # Imported here, not at module scope, so importing this module on a
        # non-Windows box (e.g. to read it, or collect tests) never explodes.
        import pyautogui

        pyautogui.FAILSAFE = False
        self._gui = pyautogui
        try:
            import pygetwindow

            self._win = pygetwindow
        except ImportError:
            self._win = None

    @property
    def capabilities(self) -> frozenset[Capability]:
        caps = {
            Capability.CURSOR,
            Capability.CLICK,
            Capability.SCROLL,
            Capability.KEYBOARD,
            Capability.CLIPBOARD,
            Capability.APP_LAUNCH,
            Capability.SCREENSHOT,
            Capability.MEDIA_KEYS,
            Capability.VOLUME,
            Capability.LOCK_SCREEN,
            Capability.FILE_OPERATIONS,
        }
        if self._win is not None:
            caps.add(Capability.WINDOW_MANAGEMENT)
        return frozenset(caps)

    def screen_info(self) -> ScreenInfo:
        w, h = self._gui.size()
        return ScreenInfo(int(w), int(h))

    def move_cursor(self, x: int, y: int) -> None:
        self._gui.moveTo(int(x), int(y), _pause=False)

    def cursor_position(self) -> tuple[int, int]:
        p = self._gui.position()
        return (int(p.x), int(p.y))

    def click(self, button: MouseButton = MouseButton.LEFT, count: int = 1) -> None:
        self._gui.click(button=_BUTTON[button], clicks=count, _pause=False)

    def mouse_down(self, button: MouseButton = MouseButton.LEFT) -> None:
        self._gui.mouseDown(button=_BUTTON[button], _pause=False)

    def mouse_up(self, button: MouseButton = MouseButton.LEFT) -> None:
        self._gui.mouseUp(button=_BUTTON[button], _pause=False)

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        if dy:
            self._gui.scroll(int(dy), _pause=False)
        if dx:
            self._gui.hscroll(int(dx), _pause=False)

    def type_text(self, text: str) -> None:
        self._gui.typewrite(text, _pause=False)

    def press_keys(self, *keys: str) -> None:
        if len(keys) == 1:
            self._gui.press(keys[0], _pause=False)
        else:
            self._gui.hotkey(*keys, _pause=False)

    def list_windows(self) -> list[WindowInfo]:
        if self._win is None:
            raise NotImplementedError("pygetwindow not installed")
        out: list[WindowInfo] = []
        for w in self._win.getAllWindows():
            if not w.title:
                continue
            out.append(
                WindowInfo(
                    handle=str(w._hWnd),
                    title=w.title,
                    x=w.left,
                    y=w.top,
                    width=w.width,
                    height=w.height,
                    is_minimized=w.isMinimized,
                    is_maximized=w.isMaximized,
                    is_focused=w.isActive,
                )
            )
        return out

    def _window(self, handle: str):
        if self._win is None:
            raise NotImplementedError("pygetwindow not installed")
        for w in self._win.getAllWindows():
            if str(w._hWnd) == handle:
                return w
        raise LookupError(f"no window with handle {handle}")

    def focus_window(self, handle: str) -> None:
        self._window(handle).activate()

    def move_window(self, handle: str, x: int, y: int) -> None:
        self._window(handle).moveTo(int(x), int(y))

    def resize_window(self, handle: str, width: int, height: int) -> None:
        self._window(handle).resizeTo(int(width), int(height))

    def minimize_window(self, handle: str) -> None:
        self._window(handle).minimize()

    def maximize_window(self, handle: str) -> None:
        self._window(handle).maximize()

    def close_window(self, handle: str) -> None:
        self._window(handle).close()

    def screenshot(self, path: str | None = None) -> str:
        target = path or "screenshot.png"
        self._gui.screenshot(target)
        return target

    def media_key(self, key: str) -> None:
        mapping = {
            "play_pause": "playpause",
            "next": "nexttrack",
            "previous": "prevtrack",
            "stop": "stop",
        }
        self._gui.press(mapping.get(key, key), _pause=False)

    def set_volume(self, level: float) -> None:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(min(1.0, max(0.0, level)), None)

    def get_volume(self) -> float:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        return float(volume.GetMasterVolumeLevelScalar())

    def mute(self, muted: bool = True) -> None:
        self._gui.press("volumemute", _pause=False)

    def launch_app(self, name: str) -> None:
        import subprocess

        subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)

    def lock_screen(self) -> None:
        import ctypes

        ctypes.windll.user32.LockWorkStation()
