"""Floating windows in the spatial interface.

Phase 3 draws a mobile-style desktop *over the camera view*, so windows live
in the same normalised 0..1 coordinate space as everything else in this
project. A window here is a pure data model: it knows its rectangle, its
z-order and its state, and nothing about rendering or about the OS.

Keeping it pure is what makes it testable in a container with no display.
The bridge to real OS windows stays in ``osadapter`` and is **UNVERIFIED**.

Two decisions worth stating:

* **Rectangles clamp, they do not wrap.** A window dragged off the edge is
  a window you cannot grab again, so every move ends inside the workspace
  with at least a title bar's worth of the window still reachable.
* **Minimum size is enforced on resize, not on construction.** Callers may
  build whatever they like; the interactive path is the one that has to
  stay usable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from itertools import count

from ..types import Point

#: Smallest interactive window, as a fraction of the workspace.
MIN_WIDTH = 0.14
MIN_HEIGHT = 0.10

#: Height of the grab bar at the top of a window. Grabbing anywhere else
#: inside the window is a content interaction, not a move.
TITLE_BAR_HEIGHT = 0.045

#: Size of the corner square that resizes instead of moves.
RESIZE_HANDLE = 0.05


class WindowState(str, Enum):
    NORMAL = "normal"
    MINIMIZED = "minimized"
    MAXIMIZED = "maximized"


class GrabKind(str, Enum):
    """What a grab on a window means."""

    NONE = "none"
    MOVE = "move"
    RESIZE = "resize"


@dataclass(frozen=True, slots=True)
class Rect:
    """An axis-aligned rectangle in normalised workspace coordinates."""

    x: float = 0.0
    y: float = 0.0
    width: float = 0.3
    height: float = 0.3

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    def contains(self, point: Point) -> bool:
        return self.x <= point.x <= self.right and self.y <= point.y <= self.bottom

    def moved_to(self, x: float, y: float) -> Rect:
        return replace(self, x=x, y=y)

    def translated(self, dx: float, dy: float) -> Rect:
        return replace(self, x=self.x + dx, y=self.y + dy)

    def resized(self, width: float, height: float) -> Rect:
        """Resize from the top-left corner, never below the minimum."""
        return replace(
            self,
            width=max(MIN_WIDTH, width),
            height=max(MIN_HEIGHT, height),
        )

    def clamped(self) -> Rect:
        """Keep the window inside the workspace and still grabbable.

        The rule is not "fully on screen" — a large window pushed to an edge
        should be allowed to overhang. The rule is that the title bar must
        stay reachable, because that is the only way to move it back.
        """
        width = min(self.width, 1.0)
        height = min(self.height, 1.0)
        # Leave at least a title bar visible vertically and a handle's worth
        # horizontally.
        min_x = RESIZE_HANDLE - width
        max_x = 1.0 - RESIZE_HANDLE
        min_y = 0.0  # never above the top: the title bar would be unreachable
        max_y = 1.0 - TITLE_BAR_HEIGHT
        return Rect(
            x=min(max(self.x, min_x), max_x),
            y=min(max(self.y, min_y), max_y),
            width=width,
            height=height,
        )

    def title_bar_contains(self, point: Point) -> bool:
        return (
            self.x <= point.x <= self.right
            and self.y <= point.y <= self.y + TITLE_BAR_HEIGHT
        )

    def resize_corner_contains(self, point: Point) -> bool:
        return (
            self.right - RESIZE_HANDLE <= point.x <= self.right
            and self.bottom - RESIZE_HANDLE <= point.y <= self.bottom
        )


_ids = count(1)


@dataclass(slots=True)
class Window:
    """One floating application window."""

    app: str
    title: str = ""
    rect: Rect = field(default_factory=Rect)
    state: WindowState = WindowState.NORMAL
    #: Higher is nearer the front. Assigned by the workspace.
    z: int = 0
    #: Rectangle to return to when un-maximising.
    restore_rect: Rect | None = None
    id: int = field(default_factory=lambda: next(_ids))
    #: Set when the app has unsaved work; closing then needs confirmation.
    unsaved: bool = False

    def __post_init__(self) -> None:
        if not self.title:
            self.title = self.app.replace("_", " ").title()

    @property
    def visible(self) -> bool:
        return self.state is not WindowState.MINIMIZED

    def grab_kind(self, point: Point) -> GrabKind:
        """What grabbing at ``point`` would do.

        The resize corner is checked first: it overlaps the window body, and
        a corner grab is the more specific intent.
        """
        if not self.visible or not self.rect.contains(point):
            return GrabKind.NONE
        if self.state is WindowState.MAXIMIZED:
            # A maximised window has nowhere to move to and no free corner.
            return GrabKind.NONE
        if self.rect.resize_corner_contains(point):
            return GrabKind.RESIZE
        if self.rect.title_bar_contains(point):
            return GrabKind.MOVE
        return GrabKind.NONE

    def maximize(self) -> None:
        if self.state is WindowState.MAXIMIZED:
            return
        if self.state is WindowState.NORMAL:
            self.restore_rect = self.rect
        self.state = WindowState.MAXIMIZED
        self.rect = Rect(0.0, 0.0, 1.0, 1.0)

    def minimize(self) -> None:
        if self.state is WindowState.NORMAL:
            self.restore_rect = self.rect
        self.state = WindowState.MINIMIZED

    def restore(self) -> None:
        """Return to the pre-maximise/minimise rectangle."""
        if self.state is WindowState.NORMAL:
            return
        if self.restore_rect is not None:
            self.rect = self.restore_rect
            self.restore_rect = None
        self.state = WindowState.NORMAL

    def toggle_maximize(self) -> None:
        if self.state is WindowState.MAXIMIZED:
            self.restore()
        else:
            self.maximize()
