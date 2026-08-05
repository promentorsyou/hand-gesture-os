"""The spatial workspace: windows, home screen, multitasking, shade.

This is the state machine behind the mobile-style interface that renders
over the camera view. It owns every window, which one has focus, and the
three overlays that can be pulled over the top of them: the home screen,
the multitasking card deck, and the quick-settings shade.

It deliberately performs **no** OS calls. The workspace is what the user
manipulates with their hands; whether a window here also drives a real OS
window is the adapter's problem, and that path is UNVERIFIED in this
container. Keeping the split here means the whole spatial interface is
testable headlessly.

Safety rule carried down from Phase 1: closing a window with unsaved work
is a sensitive operation. :meth:`Workspace.request_close` never closes such
a window directly — it reports that confirmation is required, and the
caller routes it through the confirmation gate.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

from ..apps.base import App, create_app
from ..types import Point
from .window import (
    MIN_HEIGHT,
    MIN_WIDTH,
    GrabKind,
    Rect,
    Window,
    WindowState,
)

#: Cascade offset for each new window, so they do not stack exactly.
_CASCADE = 0.045
_CASCADE_WRAP = 6


class Overlay(str, Enum):
    """Which full-screen layer is in front of the windows."""

    NONE = "none"
    HOME = "home"
    MULTITASK = "multitask"
    QUICK_SETTINGS = "quick_settings"


@dataclass(frozen=True, slots=True)
class Notification:
    app: str
    title: str
    body: str = ""
    timestamp: float = 0.0
    read: bool = False


@dataclass(frozen=True, slots=True)
class MultitaskCard:
    """One card in the app-switcher deck."""

    window_id: int
    app: str
    title: str
    focused: bool


@dataclass(slots=True)
class GrabSession:
    """An in-flight window move or resize."""

    window_id: int
    kind: GrabKind
    #: Cursor position when the grab began.
    origin: Point
    #: Window rectangle when the grab began. Deltas are applied to this,
    #: not accumulated frame to frame, so rounding cannot drift.
    start_rect: Rect


@dataclass(slots=True)
class CloseResult:
    """Outcome of asking to close a window."""

    closed: bool
    needs_confirmation: bool = False
    window_id: int | None = None


class Workspace:
    """Windows, focus, overlays, and hand-driven window manipulation."""

    #: How many notifications are retained.
    max_notifications = 50

    def __init__(self) -> None:
        self.windows: list[Window] = []
        self.overlay: Overlay = Overlay.NONE
        self.notifications: deque[Notification] = deque(maxlen=self.max_notifications)
        self.quick_settings: dict[str, bool] = {
            "gesture_control": True,
            "one_hand_mode": False,
            "precision_mode": False,
            "notifications": True,
            "mirror_view": True,
        }
        #: Running app instances, keyed by window id. A window whose app
        #: name is not registered simply has no instance — the spatial
        #: layer still works, it just has nothing to put inside.
        self.apps: dict[int, App] = {}
        self._focus_id: int | None = None
        self._z = 0
        self._opened = 0
        self._grab: GrabSession | None = None

    # --- Window lifecycle ----------------------------------------------

    def open(self, app: str, *, title: str = "", rect: Rect | None = None,
             unsaved: bool = False, app_kwargs: dict | None = None) -> Window:
        """Open a window and give it focus."""
        if rect is None:
            step = (self._opened % _CASCADE_WRAP) * _CASCADE
            rect = Rect(0.12 + step, 0.10 + step, 0.42, 0.40)
        self._opened += 1
        self._z += 1
        instance = create_app(app, **(app_kwargs or {}))
        window = Window(
            app=app,
            title=title or (instance.title if instance else ""),
            rect=rect.clamped(),
            z=self._z,
            unsaved=unsaved,
        )
        if instance is not None:
            self.apps[window.id] = instance
        self.windows.append(window)
        self._focus_id = window.id
        self.overlay = Overlay.NONE
        return window

    def get(self, window_id: int) -> Window | None:
        for w in self.windows:
            if w.id == window_id:
                return w
        return None

    def request_close(self, window_id: int) -> CloseResult:
        """Close a window, unless it has unsaved work.

        Unsaved work is on the sensitive-operations list, so this refuses to
        act and hands the decision back to the confirmation gate.
        """
        window = self.get(window_id)
        if window is None:
            return CloseResult(closed=False)
        if window.unsaved:
            return CloseResult(
                closed=False, needs_confirmation=True, window_id=window_id
            )
        self.force_close(window_id)
        return CloseResult(closed=True, window_id=window_id)

    def force_close(self, window_id: int) -> bool:
        """Close regardless of unsaved state. Only for confirmed closes."""
        window = self.get(window_id)
        if window is None:
            return False
        if self._grab is not None and self._grab.window_id == window_id:
            self._grab = None
        self.windows.remove(window)
        self.apps.pop(window_id, None)
        if self._focus_id == window_id:
            self._focus_id = None
            top = self.top_window()
            if top is not None:
                self._focus_id = top.id
        return True

    def app_for(self, window_id: int) -> App | None:
        """The running app instance in a window, if it has one."""
        return self.apps.get(window_id)

    # --- Focus and ordering --------------------------------------------

    @property
    def focused(self) -> Window | None:
        return self.get(self._focus_id) if self._focus_id is not None else None

    def focus(self, window_id: int) -> Window | None:
        """Focus a window and raise it to the front."""
        window = self.get(window_id)
        if window is None:
            return None
        if window.state is WindowState.MINIMIZED:
            window.restore()
        self._z += 1
        window.z = self._z
        self._focus_id = window.id
        self.overlay = Overlay.NONE
        return window

    def visible_windows(self) -> list[Window]:
        """Visible windows, back to front — i.e. paint order."""
        return sorted((w for w in self.windows if w.visible), key=lambda w: w.z)

    def top_window(self) -> Window | None:
        visible = self.visible_windows()
        return visible[-1] if visible else None

    def window_at(self, point: Point) -> Window | None:
        """Topmost visible window under ``point``."""
        for window in reversed(self.visible_windows()):
            if window.rect.contains(point):
                return window
        return None

    def focus_at(self, point: Point) -> Window | None:
        window = self.window_at(point)
        return self.focus(window.id) if window is not None else None

    def cycle(self, step: int = 1) -> Window | None:
        """Switch to the next/previous app in open order."""
        if not self.windows:
            return None
        order = sorted(self.windows, key=lambda w: w.id)
        current = next(
            (i for i, w in enumerate(order) if w.id == self._focus_id), -1
        )
        index = (current + step) % len(order)
        return self.focus(order[index].id)

    # --- Window state ---------------------------------------------------

    def minimize(self, window_id: int | None = None) -> Window | None:
        window = self.get(window_id) if window_id is not None else self.focused
        if window is None:
            return None
        self._end_grab_for(window.id)
        window.minimize()
        if self._focus_id == window.id:
            top = self.top_window()
            self._focus_id = top.id if top is not None else None
        return window

    def maximize(self, window_id: int | None = None) -> Window | None:
        window = self.get(window_id) if window_id is not None else self.focused
        if window is None:
            return None
        self._end_grab_for(window.id)
        window.maximize()
        return window

    def restore(self, window_id: int | None = None) -> Window | None:
        window = self.get(window_id) if window_id is not None else self.focused
        if window is None:
            return None
        window.restore()
        return window

    def toggle_maximize(self, window_id: int | None = None) -> Window | None:
        window = self.get(window_id) if window_id is not None else self.focused
        if window is None:
            return None
        self._end_grab_for(window.id)
        window.toggle_maximize()
        return window

    # --- Hand-driven move and resize ------------------------------------

    @property
    def grabbing(self) -> bool:
        return self._grab is not None

    @property
    def grab(self) -> GrabSession | None:
        return self._grab

    def begin_grab(self, point: Point) -> GrabKind:
        """Start moving or resizing whatever window is under ``point``.

        Returns what the grab turned out to mean, so the caller can give
        the user immediate visual feedback about which one they got.
        """
        for window in reversed(self.visible_windows()):
            kind = window.grab_kind(point)
            if kind is GrabKind.NONE:
                if window.rect.contains(point):
                    # A grab inside the body is a content interaction; it
                    # still focuses, but it must not drag the window.
                    self.focus(window.id)
                    return GrabKind.NONE
                continue
            self.focus(window.id)
            self._grab = GrabSession(
                window_id=window.id, kind=kind, origin=point, start_rect=window.rect
            )
            return kind
        return GrabKind.NONE

    def update_grab(self, point: Point) -> Window | None:
        """Apply hand movement to the grabbed window."""
        session = self._grab
        if session is None:
            return None
        window = self.get(session.window_id)
        if window is None:
            self._grab = None
            return None

        dx = point.x - session.origin.x
        dy = point.y - session.origin.y

        if session.kind is GrabKind.MOVE:
            window.rect = session.start_rect.translated(dx, dy).clamped()
        else:
            window.rect = session.start_rect.resized(
                session.start_rect.width + dx, session.start_rect.height + dy
            ).clamped()
        return window

    def end_grab(self) -> Window | None:
        session, self._grab = self._grab, None
        return self.get(session.window_id) if session is not None else None

    def _end_grab_for(self, window_id: int) -> None:
        if self._grab is not None and self._grab.window_id == window_id:
            self._grab = None

    # --- Overlays --------------------------------------------------------

    def show_home(self) -> Overlay:
        self._grab = None
        self.overlay = Overlay.HOME
        return self.overlay

    def show_multitask(self) -> Overlay:
        self._grab = None
        self.overlay = Overlay.MULTITASK
        return self.overlay

    def show_quick_settings(self) -> Overlay:
        self._grab = None
        self.overlay = Overlay.QUICK_SETTINGS
        return self.overlay

    def dismiss_overlay(self) -> Overlay:
        self.overlay = Overlay.NONE
        return self.overlay

    def toggle_overlay(self, overlay: Overlay) -> Overlay:
        """Show ``overlay``, or hide it if it is already showing."""
        if self.overlay is overlay:
            return self.dismiss_overlay()
        self._grab = None
        self.overlay = overlay
        return self.overlay

    def multitask_cards(self) -> list[MultitaskCard]:
        """Cards front to back — most recently used first, as expected."""
        return [
            MultitaskCard(
                window_id=w.id,
                app=w.app,
                title=w.title,
                focused=w.id == self._focus_id,
            )
            for w in sorted(self.windows, key=lambda w: w.z, reverse=True)
        ]

    def select_card(self, window_id: int) -> Window | None:
        """Pick a card from the multitasking deck."""
        window = self.focus(window_id)
        if window is not None:
            self.overlay = Overlay.NONE
        return window

    # --- Notifications and quick settings --------------------------------

    def notify(self, app: str, title: str, body: str = "", timestamp: float = 0.0) -> Notification | None:
        """Post a notification, unless the user has muted them."""
        if not self.quick_settings.get("notifications", True):
            return None
        note = Notification(app=app, title=title, body=body, timestamp=timestamp)
        self.notifications.appendleft(note)
        return note

    @property
    def unread_count(self) -> int:
        return sum(1 for n in self.notifications if not n.read)

    def mark_all_read(self) -> int:
        count = self.unread_count
        self.notifications = deque(
            (
                n if n.read else Notification(n.app, n.title, n.body, n.timestamp, True)
                for n in self.notifications
            ),
            maxlen=self.max_notifications,
        )
        return count

    def clear_notifications(self) -> int:
        count = len(self.notifications)
        self.notifications.clear()
        return count

    def toggle_setting(self, name: str) -> bool | None:
        if name not in self.quick_settings:
            return None
        self.quick_settings[name] = not self.quick_settings[name]
        return self.quick_settings[name]

    # --- Snapshot ---------------------------------------------------------

    def snapshot(self) -> dict:
        """Serialisable state for the UI and the mobile companion."""
        return {
            "overlay": self.overlay.value,
            "focused": self._focus_id,
            "grabbing": self._grab.kind.value if self._grab else None,
            "windows": [
                {
                    "id": w.id,
                    "app": w.app,
                    "title": w.title,
                    "state": w.state.value,
                    "z": w.z,
                    "unsaved": w.unsaved,
                    "rect": [w.rect.x, w.rect.y, w.rect.width, w.rect.height],
                }
                for w in sorted(self.windows, key=lambda w: w.z)
            ],
            "notifications": [
                {"app": n.app, "title": n.title, "body": n.body, "read": n.read}
                for n in self.notifications
            ],
            "app": (
                {
                    "name": self.apps[self._focus_id].name,
                    "state": self.apps[self._focus_id].state(),
                }
                if self._focus_id in self.apps
                else None
            ),
            "unread": self.unread_count,
            "quick_settings": dict(self.quick_settings),
        }


__all__ = [
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "CloseResult",
    "GrabSession",
    "MultitaskCard",
    "Notification",
    "Overlay",
    "Workspace",
]
