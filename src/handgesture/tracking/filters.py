"""Signal smoothing.

Raw MediaPipe landmarks jitter by a few pixels every frame. Feeding that
straight into a cursor makes it unusable, so everything positional goes
through a One Euro filter: it smooths hard when the hand is still (killing
jitter) and barely at all when the hand moves fast (killing lag).
"""

from __future__ import annotations

import math

from ..types import Point


class LowPassFilter:
    """Exponential moving average with a settable alpha."""

    __slots__ = ("_value", "_initialised")

    def __init__(self) -> None:
        self._value = 0.0
        self._initialised = False

    @property
    def initialised(self) -> bool:
        return self._initialised

    @property
    def value(self) -> float:
        return self._value

    def filter(self, x: float, alpha: float) -> float:
        if not self._initialised:
            self._value = x
            self._initialised = True
        else:
            self._value = alpha * x + (1.0 - alpha) * self._value
        return self._value

    def reset(self) -> None:
        self._value = 0.0
        self._initialised = False


class OneEuroFilter:
    """One Euro filter (Casiez, Roussel & Vogel, CHI 2012).

    ``min_cutoff`` sets the floor on smoothing (lower = steadier when still)
    and ``beta`` sets how aggressively the filter loosens as speed rises
    (higher = less lag when moving fast).
    """

    __slots__ = ("min_cutoff", "beta", "d_cutoff", "_x", "_dx", "_last_time")

    def __init__(
        self,
        min_cutoff: float = 1.0,
        beta: float = 0.007,
        d_cutoff: float = 1.0,
    ) -> None:
        if min_cutoff <= 0:
            raise ValueError("min_cutoff must be > 0")
        if d_cutoff <= 0:
            raise ValueError("d_cutoff must be > 0")
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x = LowPassFilter()
        self._dx = LowPassFilter()
        self._last_time: float | None = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def filter(self, x: float, timestamp: float) -> float:
        if self._last_time is None or timestamp <= self._last_time:
            # First sample, or a non-monotonic clock: seed and pass through.
            self._last_time = timestamp
            self._x.filter(x, 1.0)
            return x

        dt = timestamp - self._last_time
        self._last_time = timestamp

        prev = self._x.value
        dx = (x - prev) / dt if self._x.initialised else 0.0
        edx = self._dx.filter(dx, self._alpha(self.d_cutoff, dt))

        cutoff = self.min_cutoff + self.beta * abs(edx)
        return self._x.filter(x, self._alpha(cutoff, dt))

    def reset(self) -> None:
        self._x.reset()
        self._dx.reset()
        self._last_time = None


class PointFilter:
    """A One Euro filter per axis, for smoothing a 2D/3D landmark."""

    __slots__ = ("_fx", "_fy", "_fz")

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.007) -> None:
        self._fx = OneEuroFilter(min_cutoff, beta)
        self._fy = OneEuroFilter(min_cutoff, beta)
        self._fz = OneEuroFilter(min_cutoff, beta)

    def filter(self, point: Point, timestamp: float) -> Point:
        return Point(
            self._fx.filter(point.x, timestamp),
            self._fy.filter(point.y, timestamp),
            self._fz.filter(point.z, timestamp),
        )

    def reset(self) -> None:
        self._fx.reset()
        self._fy.reset()
        self._fz.reset()

    def set_params(self, min_cutoff: float, beta: float) -> None:
        for f in (self._fx, self._fy, self._fz):
            f.min_cutoff = min_cutoff
            f.beta = beta
