"""Geometric features extracted from a hand.

The recogniser never looks at raw landmarks — it works entirely off these
scale-invariant features, so a hand near the camera and the same pose far
away produce the same numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..types import (
    FINGER_JOINTS,
    INDEX_MCP,
    INDEX_TIP,
    MIDDLE_TIP,
    PINKY_MCP,
    THUMB_TIP,
    WRIST,
    Hand,
)


@dataclass(frozen=True, slots=True)
class HandFeatures:
    """Scale- and position-invariant description of one hand pose."""

    #: Per-finger extension in ``[0, 1]``: thumb, index, middle, ring, pinky.
    extension: tuple[float, float, float, float, float]
    #: Thumb-tip to index-tip distance, normalised by palm span.
    pinch_distance: float
    #: Index-tip to middle-tip distance, normalised by palm span.
    index_middle_distance: float
    #: How square-on the palm faces the camera, ``[0, 1]``.
    palm_facing: float
    #: Rough wrist roll in radians, from the knuckle line's angle.
    wrist_roll: float
    #: Overall curl of the hand, ``0`` = flat open, ``1`` = tight fist.
    curl: float

    @property
    def extended_fingers(self) -> int:
        """Count of fingers considered extended (threshold at the midpoint)."""
        return sum(1 for e in self.extension if e >= 0.5)

    @property
    def is_pinching(self) -> bool:
        return self.pinch_distance < 0.42


def _finger_extension(hand: Hand, tip: int, pip: int, mcp: int) -> float:
    """Extension of one finger in ``[0, 1]``.

    Compares tip-to-MCP distance against the finger's own bone length, which
    keeps the measure independent of hand size and camera distance. A folded
    finger brings its tip close to the knuckle; an extended one pushes it to
    roughly the full bone length away.
    """
    span = hand.palm_span
    tip_to_mcp = hand[tip].distance_to(hand[mcp]) / span
    # Measured against the reference hand geometry: a curled finger sits
    # around 0.28-0.34x palm span from its own knuckle, an extended one
    # around 0.61-0.85x. Map that measured band onto [0, 1].
    return _clamp01((tip_to_mcp - 0.34) / (0.72 - 0.34))


def _thumb_extension(hand: Hand) -> float:
    """Thumb extension, measured sideways rather than by curl.

    The thumb folds across the palm instead of toward its own knuckle, so the
    finger test above doesn't apply — distance from the thumb tip to the
    pinky knuckle separates 'tucked' from 'out' far more reliably.
    """
    span = hand.palm_span
    tip_to_pinky = hand[THUMB_TIP].distance_to(hand[PINKY_MCP]) / span
    # Measured: tucked thumb ~0.44-0.56x palm span from the pinky knuckle,
    # extended ~1.13-1.20x.
    return _clamp01((tip_to_pinky - 0.55) / (1.10 - 0.55))


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def palm_facing(hand: Hand) -> float:
    """How square-on the palm faces the camera, ``[0, 1]``.

    Uses the triangle wrist / index-MCP / pinky-MCP: its area shrinks toward
    zero as the hand turns edge-on to the camera, and peaks when the palm is
    flat to it. Normalised by palm span squared so it stays scale-free.
    """
    a, b, c = hand[WRIST], hand[INDEX_MCP], hand[PINKY_MCP]
    area = abs((b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y)) / 2.0
    normalised = area / (hand.palm_span**2)
    # An open palm square to the camera lands around 0.5 in these units.
    return _clamp01(normalised / 0.5)


def wrist_roll(hand: Hand) -> float:
    """Rotation of the knuckle line in radians, in ``[-pi, pi]``."""
    a, b = hand[INDEX_MCP], hand[PINKY_MCP]
    return math.atan2(b.y - a.y, b.x - a.x)


def extract(hand: Hand) -> HandFeatures:
    """Reduce a hand to its pose features."""
    span = hand.palm_span

    ext = (
        _thumb_extension(hand),
        *(_finger_extension(hand, tip, pip, mcp) for tip, pip, mcp in FINGER_JOINTS),
    )

    pinch = hand[THUMB_TIP].distance_to(hand[INDEX_TIP]) / span
    index_middle = hand[INDEX_TIP].distance_to(hand[MIDDLE_TIP]) / span
    curl = 1.0 - (sum(ext[1:]) / 4.0)

    return HandFeatures(
        extension=ext,  # type: ignore[arg-type]
        pinch_distance=pinch,
        index_middle_distance=index_middle,
        palm_facing=palm_facing(hand),
        wrist_roll=wrist_roll(hand),
        curl=curl,
    )


def hands_crossed(left: Hand, right: Hand) -> bool:
    """True when the two hands are crossed over one another.

    This backs the emergency-stop gesture, so it deliberately requires a
    clear crossing — wrists on opposite sides *and* the arms overlapping —
    rather than merely touching hands.
    """
    # In image space (before mirroring) a right hand normally sits at lower x
    # than the left. Crossed means that ordering has inverted.
    if right.palm_center.x <= left.palm_center.x:
        return False
    separation = abs(right.palm_center.x - left.palm_center.x)
    reach = (left.palm_span + right.palm_span) / 2.0
    # Require them genuinely overlapping, not just swapped and far apart.
    return separation < reach * 3.0
