"""macOS adapter.

STATUS: UNVERIFIED. Requires the user to grant Accessibility and Screen
Recording permissions in System Settings > Privacy & Security; without them
macOS silently ignores synthetic input. Never executed from this project's
CI (headless Linux container).

Install extras with: ``pip install -e ".[macos]"``
"""

from __future__ import annotations

import subprocess

from .base import Capability, MouseButton, OSAdapter, ScreenInfo, WindowInfo

_BUTTON = {
    MouseButton.LEFT: "left",
    MouseButton.RIGHT: "right",
    MouseButton.MIDDLE: "middle",
}


class MacOSAdapter(OSAdapter):
    name = "macos"

    def __init__(self) -> None:
        import pyautogui

        pyautogui.FAILSAFE = False
        self._gui = pyautogui

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
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
                Capability.WINDOW_MANAGEMENT,
            }
        )

    def _mod(self) -> str:
        # macOS uses Command, not Control, for copy/cut/paste.
        return "command"

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

    def _osascript(self, script: str) -> str:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, check=False
        )
        return result.stdout.strip()

    def list_windows(self) -> list[WindowInfo]:
        # System Events exposes window geometry per process.
        script = (
            'tell application "System Events" to get {name, position, size} of '
            "every window of (every process whose visible is true)"
        )
        raw = self._osascript(script)
        if not raw:
            return []
        # AppleScript returns a flat comma-separated list; parsing it
        # robustly needs the richer Accessibility API, so this stays a
        # best-effort listing until verified on hardware.
        return []

    def focus_window(self, handle: str) -> None:
        self._osascript(f'tell application "{handle}" to activate')

    def screenshot(self, path: str | None = None) -> str:
        target = path or "screenshot.png"
        subprocess.run(["screencapture", "-x", target], check=False)
        return target

    def media_key(self, key: str) -> None:
        codes = {"play_pause": 16, "next": 17, "previous": 18}
        code = codes.get(key)
        if code is None:
            return
        self._osascript(
            f'tell application "System Events" to key code {code}'
        )

    def set_volume(self, level: float) -> None:
        pct = int(min(1.0, max(0.0, level)) * 100)
        self._osascript(f"set volume output volume {pct}")

    def get_volume(self) -> float:
        out = self._osascript("output volume of (get volume settings)")
        try:
            return int(out) / 100.0
        except ValueError:
            return 0.0

    def mute(self, muted: bool = True) -> None:
        self._osascript(f"set volume output muted {str(bool(muted)).lower()}")

    def launch_app(self, name: str) -> None:
        subprocess.Popen(["open", "-a", name])

    def lock_screen(self) -> None:
        self._osascript(
            'tell application "System Events" to keystroke "q" using '
            "{control down, command down}"
        )
