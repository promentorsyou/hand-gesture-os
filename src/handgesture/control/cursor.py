"""Cursor mapping.

Phase 1 drives a *simulated* cursor only — it produces screen coordinates
and click intents but does not touch the real pointer. Phase 2 hands the
same output to an OS adapter.

The interesting work here is mapping a small, shaky region of camera space
onto the whole screen without the cursor becoming unusable at the edges:

* an **active region** inset from the frame edge, so you never have to push
  your hand out of view to reach a screen corner
* **exponential gain** — slow hand movement maps finely for precision,
  fast movement covers distance
* a **precision mode** that scales gain down for fine positioning
"""

from __future__ import annotations

from dataclasses import dataclass

from ..tracking.filters import PointFilter
from ..types import Point


@dataclass(slots=True)
class CursorConfig:
    """Tunables for camera-space to screen-space mapping."""

    screen_width: int = 1920
    screen_height: int = 1080
    #: Fraction of the frame inset on each side to form the active region.
    #: 0.15 means the middle 70% of the frame covers the whole screen.
    margin: float = 0.15
    #: Base movement multiplier.
    gain: float = 1.0
    #: How much fast movement is amplified (0 disables acceleration).
    acceleration: float = 0.6
    #: Gain multiplier while precision mode is active.
    precision_scale: float = 0.35
    #: One Euro smoothing parameters.
    smoothing_min_cutoff: float = 1.2
    smoothing_beta: float = 0.012
    #: Mirror horizontally so moving your hand right moves the cursor right
    #: in a mirrored self-view.
    mirror_x: bool = True


@dataclass(frozen=True, slots=True)
class CursorState:
    x: float
    y: float
    #: Normalised speed of the last movement, for UI feedback.
    speed: float
    precision: bool
    #: True when the hand is outside the active region and the cursor is
    #: pinned to an edge.
    clamped: bool


class CursorController:
    """Maps a smoothed hand position onto screen coordinates."""

    def __init__(self, config: CursorConfig | None = None) -> None:
        self.config = config or CursorConfig()
        self._filter = PointFilter(
            self.config.smoothing_min_cutoff, self.config.smoothing_beta
        )
        self._x = self.config.screen_width / 2.0
        self._y = self.config.screen_height / 2.0
        self._last_norm: Point | None = None
        self._precision = False

    @property
    def position(self) -> tuple[float, float]:
        return (self._x, self._y)

    @property
    def precision(self) -> bool:
        return self._precision

    def set_precision(self, enabled: bool) -> None:
        """Precision mode: finer gain, heavier smoothing."""
        if enabled == self._precision:
            return
        self._precision = enabled
        cfg = self.config
        if enabled:
            self._filter.set_params(cfg.smoothing_min_cutoff * 0.4, cfg.smoothing_beta * 0.3)
        else:
            self._filter.set_params(cfg.smoothing_min_cutoff, cfg.smoothing_beta)

    def reset(self) -> None:
        self._filter.reset()
        self._last_norm = None

    def center(self) -> None:
        self._x = self.config.screen_width / 2.0
        self._y = self.config.screen_height / 2.0
        self.reset()

    def _to_active_region(self, p: Point) -> tuple[float, float, bool]:
        """Map normalised frame coords into the active region, in ``[0, 1]``."""
        m = self.config.margin
        span = max(1e-6, 1.0 - 2.0 * m)
        x = (p.x - m) / span
        y = (p.y - m) / span
        clamped = x < 0.0 or x > 1.0 or y < 0.0 or y > 1.0
        return (min(1.0, max(0.0, x)), min(1.0, max(0.0, y)), clamped)

    def update(self, hand_point: Point, timestamp: float) -> CursorState:
        """Advance the cursor from a hand position in normalised frame space."""
        cfg = self.config
        smoothed = self._filter.filter(hand_point, timestamp)

        nx, ny, clamped = self._to_active_region(smoothed)
        if cfg.mirror_x:
            nx = 1.0 - nx

        target_x = nx * cfg.screen_width
        target_y = ny * cfg.screen_height

        if self._last_norm is None:
            # First frame: jump straight there rather than sliding in from
            # wherever the cursor happened to be.
            self._x, self._y = target_x, target_y
            self._last_norm = smoothed
            return CursorState(self._x, self._y, 0.0, self._precision, clamped)

        dx = target_x - self._x
        dy = target_y - self._y
        distance = (dx * dx + dy * dy) ** 0.5
        speed = distance / max(cfg.screen_width, cfg.screen_height)

        gain = cfg.gain * (cfg.precision_scale if self._precision else 1.0)
        # Exponential acceleration: gentle for small corrections, stronger
        # once you are clearly travelling.
        gain *= 1.0 + cfg.acceleration * min(1.0, speed * 8.0)

        self._x = min(float(cfg.screen_width), max(0.0, self._x + dx * min(1.0, gain)))
        self._y = min(float(cfg.screen_height), max(0.0, self._y + dy * min(1.0, gain)))
        self._last_norm = smoothed

        return CursorState(self._x, self._y, speed, self._precision, clamped)
