"""Static gesture recognition.

Each candidate gesture scores itself against the extracted features; the
best score wins, but only if it clears the configured confidence floor.
Scoring (rather than a chain of if/else) is what makes the confidence value
meaningful — and confidence is what the debouncer uses to reject the
half-formed poses your hand passes through on the way to a real gesture.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..types import Frame, Hand, Handedness
from . import features as feat
from .features import HandFeatures
from .vocabulary import Gesture


@dataclass(frozen=True, slots=True)
class Recognition:
    """A recognised gesture plus the confidence behind it."""

    gesture: Gesture
    confidence: float
    #: Score of every candidate, for the UI's confidence readout and for
    #: debugging why a gesture did or didn't fire.
    scores: dict[Gesture, float]

    @property
    def is_confident(self) -> bool:
        return self.gesture is not Gesture.NONE and self.confidence > 0.0


def _band(value: float, low: float, high: float) -> float:
    """1.0 inside ``[low, high]``, tapering to 0 within half a band outside.

    Soft edges matter: a hard threshold makes confidence jump between 0 and
    1 on a single noisy frame, which is exactly the flicker the debouncer
    then has to clean up.
    """
    if low <= value <= high:
        return 1.0
    width = max(1e-6, (high - low) / 2.0)
    if value < low:
        return max(0.0, 1.0 - (low - value) / width)
    return max(0.0, 1.0 - (value - high) / width)


def _at_least(value: float, threshold: float, softness: float = 0.15) -> float:
    """1.0 at or above ``threshold``, falling to 0 within ``softness`` below.

    Needed where ``_band``'s proportional taper is too gentle: with a wide
    band the taper spans units, which leaves genuinely failing values still
    scoring high.
    """
    if value >= threshold:
        return 1.0
    return max(0.0, 1.0 - (threshold - value) / max(1e-6, softness))


def _score_point(f: HandFeatures) -> float:
    thumb, index, middle, ring, pinky = f.extension
    return min(
        _band(index, 0.75, 1.0),
        _band(middle, 0.0, 0.35),
        _band(ring, 0.0, 0.35),
        _band(pinky, 0.0, 0.45),
        # Critical: a point and a pinch share the same finger curl pattern
        # and differ only in whether the thumb has closed on the index.
        # Without this the two are indistinguishable.
        _at_least(f.pinch_distance, 0.55),
    )


def _score_pinch(f: HandFeatures) -> float:
    # Pinch is defined by the thumb/index gap closing; the other fingers are
    # deliberately not constrained, since people pinch with a loose hand.
    return min(
        _band(f.pinch_distance, 0.0, 0.40),
        _band(f.extension[1], 0.45, 1.0),
    )


def _score_peace(f: HandFeatures) -> float:
    thumb, index, middle, ring, pinky = f.extension
    return min(
        _band(index, 0.7, 1.0),
        _band(middle, 0.7, 1.0),
        _band(ring, 0.0, 0.4),
        _band(pinky, 0.0, 0.45),
        # Must be a deliberate two-finger pose, not a pinch with fingers up.
        _at_least(f.pinch_distance, 0.55),
    )


def _score_fist(f: HandFeatures) -> float:
    thumb, index, middle, ring, pinky = f.extension
    return min(
        _band(index, 0.0, 0.3),
        _band(middle, 0.0, 0.3),
        _band(ring, 0.0, 0.3),
        _band(pinky, 0.0, 0.35),
        _band(thumb, 0.0, 0.5),
    )


def _score_open_palm(f: HandFeatures) -> float:
    thumb, index, middle, ring, pinky = f.extension
    return min(
        _band(index, 0.75, 1.0),
        _band(middle, 0.75, 1.0),
        _band(ring, 0.7, 1.0),
        _band(pinky, 0.6, 1.0),
        _band(thumb, 0.5, 1.0),
    )


def _score_palm_forward(f: HandFeatures) -> float:
    # An open palm turned square to the camera. Strictly a superset of
    # OPEN_PALM, so it only wins when the palm really is facing forward.
    return min(_score_open_palm(f), _band(f.palm_facing, 0.75, 1.0))


def _score_thumbs_up(f: HandFeatures) -> float:
    thumb, index, middle, ring, pinky = f.extension
    return min(
        _band(thumb, 0.7, 1.0),
        _band(index, 0.0, 0.3),
        _band(middle, 0.0, 0.3),
        _band(ring, 0.0, 0.3),
        _band(pinky, 0.0, 0.35),
    )


#: Single-hand scorers, evaluated every frame.
_SCORERS: dict[Gesture, callable] = {
    Gesture.POINT: _score_point,
    Gesture.PINCH: _score_pinch,
    Gesture.PEACE: _score_peace,
    Gesture.FIST: _score_fist,
    Gesture.OPEN_PALM: _score_open_palm,
    Gesture.PALM_FORWARD: _score_palm_forward,
    Gesture.THUMBS_UP: _score_thumbs_up,
}


class GestureRecognizer:
    """Classifies a single frame into at most one gesture."""

    def __init__(self, min_confidence: float = 0.55) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be in [0, 1]")
        self.min_confidence = min_confidence

    def recognize_hand(self, hand: Hand) -> Recognition:
        f = feat.extract(hand)
        scores = {g: scorer(f) for g, scorer in _SCORERS.items()}
        best = max(scores, key=lambda g: scores[g])
        best_score = scores[best]

        if best_score < self.min_confidence:
            return Recognition(Gesture.NONE, best_score, scores)
        return Recognition(best, best_score, scores)

    def recognize(self, frame: Frame) -> Recognition:
        """Classify a frame, preferring two-hand gestures when both are up.

        Two-hand gestures outrank single-hand ones because they are
        unambiguous and carry the highest-stakes action (emergency stop).
        """
        if frame.hand_count == 0:
            return Recognition(Gesture.NONE, 0.0, {})

        if frame.hand_count >= 2:
            two = self._recognize_two_hand(frame)
            if two.is_confident:
                return two

        return self.recognize_hand(frame.hands[0])

    def _recognize_two_hand(self, frame: Frame) -> Recognition:
        a, b = frame.hands[0], frame.hands[1]

        # Resolve which is which; fall back to x-order when handedness is
        # unknown so the crossing test still has a stable reference.
        left = frame.hand_by(Handedness.LEFT)
        right = frame.hand_by(Handedness.RIGHT)
        if left is None or right is None:
            left, right = (a, b) if a.palm_center.x <= b.palm_center.x else (b, a)

        scores: dict[Gesture, float] = {}

        if feat.hands_crossed(left, right):
            # Both hands should be reasonably open for a deliberate cross —
            # this keeps it from firing while hands merely pass each other.
            fl, fr = feat.extract(left), feat.extract(right)
            openness = min(
                _band(fl.curl, 0.0, 0.6),
                _band(fr.curl, 0.0, 0.6),
            )
            scores[Gesture.CROSSED_HANDS] = openness

        if scores:
            best = max(scores, key=lambda g: scores[g])
            if scores[best] >= self.min_confidence:
                return Recognition(best, scores[best], scores)
            return Recognition(Gesture.NONE, scores[best], scores)

        return Recognition(Gesture.NONE, 0.0, scores)
