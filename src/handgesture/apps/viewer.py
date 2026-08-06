"""The image viewer.

Zoom, pan and rotation are exactly the operations two hands are good at, so
this is the app where spread/converge and wrist roll pay off. All of it is
pure geometry, which makes it fully testable here.

The one rule that needs stating: **panning is clamped to the zoom level.**
At 1x there is nothing to pan, and at 3x you can only pan as far as the
image extends. Without that you can pan the picture off the screen and have
no way to find it again.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .base import ActionResult, App, register

MIN_ZOOM = 1.0
MAX_ZOOM = 8.0


@dataclass(slots=True)
class Image:
    name: str
    width: int = 1920
    height: int = 1080

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "width": self.width, "height": self.height}


@register
class ViewerApp(App):
    """An image viewer with zoom, pan, and rotation."""

    name = "photos"
    title = "Photos"

    def __init__(self, images: list[Image] | None = None) -> None:
        self.images = images if images is not None else _demo_images()
        self.index = 0
        self.zoom = MIN_ZOOM
        #: Pan offset as a fraction of the image, -0.5..0.5 at full zoom.
        self.pan_x = 0.0
        self.pan_y = 0.0
        #: Rotation in degrees, always normalised to 0/90/180/270.
        self.rotation = 0

    @property
    def current(self) -> Image | None:
        return self.images[self.index] if self.images else None

    @property
    def actions(self) -> tuple[str, ...]:
        return ("next", "previous", "select", "zoom", "set_zoom", "pan",
                "rotate", "reset", "fit")

    def state(self) -> dict[str, Any]:
        image = self.current
        return {
            "images": [i.to_dict() for i in self.images],
            "index": self.index,
            "current": image.to_dict() if image else None,
            "zoom": round(self.zoom, 3),
            "pan": [round(self.pan_x, 4), round(self.pan_y, 4)],
            "rotation": self.rotation,
        }

    def action(self, name: str, /, **payload: Any) -> ActionResult:
        if name in ("next", "previous"):
            if not self.images:
                return ActionResult.fail("no images")
            step = 1 if name == "next" else -1
            self.index = (self.index + step) % len(self.images)
            self._reset_view()
            return ActionResult(message=self.images[self.index].name)

        if name == "select":
            index = int(payload.get("index", -1))
            if not 0 <= index < len(self.images):
                return ActionResult.fail("no such image")
            self.index = index
            self._reset_view()
            return ActionResult(message=self.images[index].name)

        if name == "zoom":
            # Relative zoom, as produced by a two-hand spread.
            return self._set_zoom(self.zoom * (1.0 + float(payload.get("delta", 0.0))))

        if name == "set_zoom":
            return self._set_zoom(float(payload.get("zoom", MIN_ZOOM)))

        if name == "pan":
            return self._pan(float(payload.get("dx", 0.0)), float(payload.get("dy", 0.0)))

        if name == "rotate":
            degrees = int(payload.get("degrees", 90))
            if degrees % 90 != 0:
                return ActionResult.fail("rotation must be a multiple of 90")
            self.rotation = (self.rotation + degrees) % 360
            return ActionResult(message=f"{self.rotation}°")

        if name in ("reset", "fit"):
            self._reset_view()
            return ActionResult(message="fit")

        return self.unknown(name)

    # --- Geometry ---------------------------------------------------------

    def _reset_view(self) -> None:
        self.zoom = MIN_ZOOM
        self.pan_x = self.pan_y = 0.0
        self.rotation = 0

    def _set_zoom(self, zoom: float) -> ActionResult:
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        # Zooming out can leave the pan outside the newly smaller range.
        self._clamp_pan()
        return ActionResult(message=f"{self.zoom:.2f}x")

    def _pan(self, dx: float, dy: float) -> ActionResult:
        if math.isclose(self.zoom, MIN_ZOOM):
            return ActionResult.fail("nothing to pan at 1x")
        self.pan_x += dx
        self.pan_y += dy
        self._clamp_pan()
        return ActionResult(message=f"{self.pan_x:+.2f},{self.pan_y:+.2f}")

    def _clamp_pan(self) -> None:
        # At zoom z, the visible window is 1/z of the image, so the centre
        # can move at most (1 - 1/z) / 2 in each direction.
        limit = max(0.0, (1.0 - 1.0 / self.zoom) / 2.0)
        self.pan_x = max(-limit, min(limit, self.pan_x))
        self.pan_y = max(-limit, min(limit, self.pan_y))

    @property
    def pan_limit(self) -> float:
        return max(0.0, (1.0 - 1.0 / self.zoom) / 2.0)


def _demo_images() -> list[Image]:
    return [
        Image("calibration-grid.png", 1600, 900),
        Image("gesture-reference.png", 2000, 1400),
        Image("workspace.png", 1920, 1080),
    ]
