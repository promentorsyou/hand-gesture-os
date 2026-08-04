"""Linux adapter (X11 primarily; Wayland support is limited by design).

STATUS: UNVERIFIED. Wayland deliberately blocks synthetic input from
ordinary applications, so on a Wayland session most of this will not work
and the adapter reports reduced capabilities. X11 sessions with ``xdotool``
and ``wmctrl`` installed are the supported path.

Install extras with: ``pip install -e ".[linux]"``
"""

from __future__ import annotations

import os
import shutil
import subprocess

from .base import Capability, MouseButton, OSAdapter, ScreenInfo, WindowInfo

_BUTTON_NUM = {MouseButton.LEFT: 1, MouseButton.MIDDLE: 2, MouseButton.RIGHT: 3}


def _is_wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(
        os.environ.get("WAYLAND_DISPLAY")
    )


class LinuxAdapter(OSAdapter):
    name = "linux"

    def __init__(self) -> None:
        self._xdotool = shutil.which("xdotool")
        self._wmctrl = shutil.which("wmctrl")
        self._wayland = _is_wayland()
        if self._xdotool is None:
            raise RuntimeError(
                "xdotool not found; install it or run with the null adapter"
            )

    @property
    def capabilities(self) -> frozenset[Capability]:
        caps = {
            Capability.CURSOR,
            Capability.CLICK,
            Capability.SCROLL,
            Capability.KEYBOARD,
            Capability.CLIPBOARD,
            Capability.APP_LAUNCH,
            Capability.FILE_OPERATIONS,
        }
        if not self._wayland:
            caps.add(Capability.SCREENSHOT)
            if self._wmctrl:
                caps.add(Capability.WINDOW_MANAGEMENT)
        if shutil.which("amixer") or shutil.which("pactl"):
            caps.add(Capability.VOLUME)
        return frozenset(caps)

    def _run(self, *args: str) -> str:
        result = subprocess.run(list(args), capture_output=True, text=True, check=False)
        return result.stdout.strip()

    def screen_info(self) -> ScreenInfo:
        out = self._run(self._xdotool, "getdisplaygeometry")
        try:
            w, h = out.split()
            return ScreenInfo(int(w), int(h))
        except ValueError:
            return ScreenInfo(1920, 1080)

    def move_cursor(self, x: int, y: int) -> None:
        self._run(self._xdotool, "mousemove", str(int(x)), str(int(y)))

    def cursor_position(self) -> tuple[int, int]:
        out = self._run(self._xdotool, "getmouselocation", "--shell")
        vals = dict(
            line.split("=", 1) for line in out.splitlines() if "=" in line
        )
        return (int(vals.get("X", 0)), int(vals.get("Y", 0)))

    def click(self, button: MouseButton = MouseButton.LEFT, count: int = 1) -> None:
        self._run(
            self._xdotool, "click", "--repeat", str(count), str(_BUTTON_NUM[button])
        )

    def mouse_down(self, button: MouseButton = MouseButton.LEFT) -> None:
        self._run(self._xdotool, "mousedown", str(_BUTTON_NUM[button]))

    def mouse_up(self, button: MouseButton = MouseButton.LEFT) -> None:
        self._run(self._xdotool, "mouseup", str(_BUTTON_NUM[button]))

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        # X11 encodes scroll as buttons 4/5 (vertical) and 6/7 (horizontal).
        if dy:
            button = "4" if dy > 0 else "5"
            self._run(self._xdotool, "click", "--repeat", str(abs(int(dy))), button)
        if dx:
            button = "7" if dx > 0 else "6"
            self._run(self._xdotool, "click", "--repeat", str(abs(int(dx))), button)

    def type_text(self, text: str) -> None:
        self._run(self._xdotool, "type", "--clearmodifiers", text)

    def press_keys(self, *keys: str) -> None:
        self._run(self._xdotool, "key", "+".join(keys))

    def list_windows(self) -> list[WindowInfo]:
        if not self._wmctrl:
            raise NotImplementedError("wmctrl not installed")
        out: list[WindowInfo] = []
        for line in self._run(self._wmctrl, "-lG").splitlines():
            parts = line.split(None, 7)
            if len(parts) < 8:
                continue
            handle, _desk, x, y, w, h, _host, title = parts
            out.append(
                WindowInfo(
                    handle=handle,
                    title=title,
                    x=int(x),
                    y=int(y),
                    width=int(w),
                    height=int(h),
                )
            )
        return out

    def focus_window(self, handle: str) -> None:
        self._run(self._wmctrl, "-i", "-a", handle)

    def move_window(self, handle: str, x: int, y: int) -> None:
        self._run(self._wmctrl, "-i", "-r", handle, "-e", f"0,{int(x)},{int(y)},-1,-1")

    def resize_window(self, handle: str, width: int, height: int) -> None:
        self._run(
            self._wmctrl, "-i", "-r", handle, "-e", f"0,-1,-1,{int(width)},{int(height)}"
        )

    def minimize_window(self, handle: str) -> None:
        self._run(self._xdotool, "windowminimize", handle)

    def maximize_window(self, handle: str) -> None:
        self._run(
            self._wmctrl, "-i", "-r", handle,
            "-b", "add,maximized_vert,maximized_horz",
        )

    def close_window(self, handle: str) -> None:
        self._run(self._wmctrl, "-i", "-c", handle)

    def screenshot(self, path: str | None = None) -> str:
        target = path or "screenshot.png"
        for tool, args in (
            ("import", ["-window", "root", target]),
            ("scrot", [target]),
            ("gnome-screenshot", ["-f", target]),
        ):
            if shutil.which(tool):
                subprocess.run([tool, *args], check=False)
                return target
        raise NotImplementedError("no screenshot tool found")

    def set_volume(self, level: float) -> None:
        pct = int(min(1.0, max(0.0, level)) * 100)
        if shutil.which("pactl"):
            self._run("pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%")
        else:
            self._run("amixer", "set", "Master", f"{pct}%")

    def get_volume(self) -> float:
        if shutil.which("pactl"):
            out = self._run("pactl", "get-sink-volume", "@DEFAULT_SINK@")
            for token in out.split():
                if token.endswith("%"):
                    try:
                        return int(token.rstrip("%")) / 100.0
                    except ValueError:
                        continue
        return 0.0

    def mute(self, muted: bool = True) -> None:
        if shutil.which("pactl"):
            self._run("pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if muted else "0")
        else:
            self._run("amixer", "set", "Master", "mute" if muted else "unmute")

    def media_key(self, key: str) -> None:
        mapping = {
            "play_pause": "XF86AudioPlay",
            "next": "XF86AudioNext",
            "previous": "XF86AudioPrev",
            "stop": "XF86AudioStop",
        }
        self._run(self._xdotool, "key", mapping.get(key, key))

    def launch_app(self, name: str) -> None:
        subprocess.Popen([name])

    def lock_screen(self) -> None:
        for tool in ("loginctl", "xdg-screensaver", "gnome-screensaver-command"):
            if shutil.which(tool):
                args = {
                    "loginctl": ["lock-session"],
                    "xdg-screensaver": ["lock"],
                    "gnome-screensaver-command": ["--lock"],
                }[tool]
                subprocess.run([tool, *args], check=False)
                return
        raise NotImplementedError("no screen locker found")
