"""Core data types shared by every module.

Everything downstream of capture speaks in these types, so a real MediaPipe
webcam feed and a synthetic test fixture are interchangeable inputs.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

# MediaPipe hand landmark indices, named so the gesture code reads clearly.
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

LANDMARK_COUNT = 21

#: Tip / proximal-joint pairs for the four non-thumb fingers.
FINGER_JOINTS = (
    (INDEX_TIP, INDEX_PIP, INDEX_MCP),
    (MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP),
    (RING_TIP, RING_PIP, RING_MCP),
    (PINKY_TIP, PINKY_PIP, PINKY_MCP),
)


class Handedness(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Point:
    """A landmark in normalised image space.

    ``x`` and ``y`` are in ``[0, 1]`` relative to the frame; ``z`` is
    MediaPipe's relative depth (negative is toward the camera) and is only
    ever used for comparisons, never as an absolute measure.
    """

    x: float
    y: float
    z: float = 0.0

    def distance_to(self, other: Point, *, use_z: bool = False) -> float:
        dx, dy = self.x - other.x, self.y - other.y
        if use_z:
            return math.sqrt(dx * dx + dy * dy + (self.z - other.z) ** 2)
        return math.hypot(dx, dy)

    def __add__(self, other: Point) -> Point:
        return Point(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Point) -> Point:
        return Point(self.x - other.x, self.y - other.y, self.z - other.z)

    def scaled(self, k: float) -> Point:
        return Point(self.x * k, self.y * k, self.z * k)


@dataclass(frozen=True, slots=True)
class Hand:
    """One detected hand: 21 landmarks plus detection metadata."""

    landmarks: tuple[Point, ...]
    handedness: Handedness = Handedness.UNKNOWN
    detection_confidence: float = 1.0

    def __post_init__(self) -> None:
        if len(self.landmarks) != LANDMARK_COUNT:
            raise ValueError(
                f"expected {LANDMARK_COUNT} landmarks, got {len(self.landmarks)}"
            )

    def __getitem__(self, index: int) -> Point:
        return self.landmarks[index]

    @property
    def palm_center(self) -> Point:
        """Average of wrist and the four knuckles — far steadier than any
        single landmark, which matters because this drives the cursor."""
        ids = (WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)
        sx = sum(self.landmarks[i].x for i in ids) / len(ids)
        sy = sum(self.landmarks[i].y for i in ids) / len(ids)
        sz = sum(self.landmarks[i].z for i in ids) / len(ids)
        return Point(sx, sy, sz)

    @property
    def palm_span(self) -> float:
        """Wrist-to-middle-knuckle distance.

        Used to normalise every other measurement so gestures read the same
        whether the hand is near the camera or far from it.
        """
        return max(1e-6, self.landmarks[WRIST].distance_to(self.landmarks[MIDDLE_MCP]))


@dataclass(frozen=True, slots=True)
class Frame:
    """One processed capture frame: zero, one, or two hands."""

    hands: tuple[Hand, ...] = ()
    timestamp: float = field(default_factory=time.monotonic)
    width: int = 640
    height: int = 480
    #: Mean frame brightness in ``[0, 1]`` when the source can measure it.
    #: Used for the low-light warning; ``None`` means "not measured".
    brightness: float | None = None

    @property
    def hand_count(self) -> int:
        return len(self.hands)

    def hand_by(self, handedness: Handedness) -> Hand | None:
        for hand in self.hands:
            if hand.handedness is handedness:
                return hand
        return None


def make_hand(
    points: Sequence[tuple[float, float] | tuple[float, float, float]],
    handedness: Handedness = Handedness.RIGHT,
    detection_confidence: float = 1.0,
) -> Hand:
    """Build a :class:`Hand` from plain tuples. Used by tests and fixtures."""
    landmarks = tuple(
        Point(p[0], p[1], p[2] if len(p) > 2 else 0.0) for p in points
    )
    return Hand(landmarks, handedness, detection_confidence)
