"""User calibration.

People's hands differ in size, they sit at different distances from the
camera, and they pinch with different comfortable gaps. Calibration samples
the user's actual poses and derives per-user thresholds, rather than making
everyone match hardcoded numbers.

The flow walks through a few steps, each collecting samples of one pose,
then solves for thresholds that sit between the measured extremes.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from enum import Enum

from ..gestures import features as feat
from ..types import Hand, Handedness


class CalibrationStep(str, Enum):
    IDLE = "idle"
    OPEN_HAND = "open_hand"       # flat, fingers spread
    CLOSED_FIST = "closed_fist"   # tight fist
    PINCH_CLOSED = "pinch_closed" # thumb and index touching
    PINCH_OPEN = "pinch_open"     # thumb and index comfortably apart
    REACH_BOUNDS = "reach_bounds" # move hand to each corner
    COMPLETE = "complete"


#: Order the wizard walks through.
STEP_ORDER: tuple[CalibrationStep, ...] = (
    CalibrationStep.OPEN_HAND,
    CalibrationStep.CLOSED_FIST,
    CalibrationStep.PINCH_CLOSED,
    CalibrationStep.PINCH_OPEN,
    CalibrationStep.REACH_BOUNDS,
)

STEP_PROMPTS: dict[CalibrationStep, str] = {
    CalibrationStep.OPEN_HAND: "Hold your hand open, fingers spread, facing the camera.",
    CalibrationStep.CLOSED_FIST: "Make a tight fist.",
    CalibrationStep.PINCH_CLOSED: "Touch your thumb and index finger together.",
    CalibrationStep.PINCH_OPEN: "Hold thumb and index comfortably apart.",
    CalibrationStep.REACH_BOUNDS: "Move your hand to each corner of your comfortable reach.",
}

#: Samples required before a step is considered done.
SAMPLES_PER_STEP = 20


@dataclass(slots=True)
class CalibrationProfile:
    """Per-user thresholds derived from calibration."""

    #: Pinch distance below which a pinch counts, normalised by palm span.
    pinch_threshold: float = 0.42
    #: Finger extension value separating curled from extended.
    extension_threshold: float = 0.5
    #: The user's measured reach in normalised frame coords.
    reach_min_x: float = 0.15
    reach_max_x: float = 0.85
    reach_min_y: float = 0.15
    reach_max_y: float = 0.85
    #: Median palm span, a proxy for typical camera distance.
    palm_span: float = 0.18
    dominant_hand: Handedness = Handedness.RIGHT
    calibrated: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["dominant_hand"] = self.dominant_hand.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> CalibrationProfile:
        d = dict(d)
        if "dominant_hand" in d:
            d["dominant_hand"] = Handedness(d["dominant_hand"])
        known = {f for f in cls.__slots__}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def cursor_margin(self) -> float:
        """Margin the cursor mapper should use, from measured reach."""
        return max(0.05, min(0.3, (self.reach_min_x + (1.0 - self.reach_max_x)) / 2.0))


@dataclass(slots=True)
class _StepSamples:
    pinch: list[float] = field(default_factory=list)
    extension: list[float] = field(default_factory=list)
    palm_span: list[float] = field(default_factory=list)
    xs: list[float] = field(default_factory=list)
    ys: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        # Must count every bucket a step can write to. Counting only some of
        # them leaves the steps that write elsewhere permanently at zero, so
        # they never reach the sample quota and the wizard never advances.
        return max(len(self.pinch), len(self.extension), len(self.xs))


class Calibrator:
    """Step-through calibration wizard.

    Feed it hands with :meth:`add_sample`; it advances automatically once a
    step has enough samples, and produces a profile at the end.
    """

    def __init__(self) -> None:
        self.step: CalibrationStep = CalibrationStep.IDLE
        self._samples: dict[CalibrationStep, _StepSamples] = {}
        self._step_index = -1

    @property
    def active(self) -> bool:
        return self.step not in (CalibrationStep.IDLE, CalibrationStep.COMPLETE)

    @property
    def prompt(self) -> str:
        return STEP_PROMPTS.get(self.step, "")

    @property
    def progress(self) -> float:
        """Progress through the whole wizard, ``[0, 1]``."""
        if self.step is CalibrationStep.COMPLETE:
            return 1.0
        if self._step_index < 0:
            return 0.0
        collected = len(self._samples.get(self.step, _StepSamples()))
        within = min(1.0, collected / SAMPLES_PER_STEP)
        return (self._step_index + within) / len(STEP_ORDER)

    def start(self) -> CalibrationStep:
        self._samples = {s: _StepSamples() for s in STEP_ORDER}
        self._step_index = 0
        self.step = STEP_ORDER[0]
        return self.step

    def cancel(self) -> None:
        self.step = CalibrationStep.IDLE
        self._step_index = -1
        self._samples.clear()

    def add_sample(self, hand: Hand) -> CalibrationStep:
        """Record one sample for the current step; auto-advances when full."""
        if not self.active:
            return self.step

        f = feat.extract(hand)
        bucket = self._samples[self.step]
        bucket.palm_span.append(hand.palm_span)

        if self.step is CalibrationStep.REACH_BOUNDS:
            c = hand.palm_center
            bucket.xs.append(c.x)
            bucket.ys.append(c.y)
        elif self.step in (CalibrationStep.PINCH_CLOSED, CalibrationStep.PINCH_OPEN):
            bucket.pinch.append(f.pinch_distance)
        else:
            bucket.extension.append(sum(f.extension[1:]) / 4.0)

        if len(bucket) >= SAMPLES_PER_STEP:
            self._advance()
        return self.step

    def _advance(self) -> None:
        self._step_index += 1
        if self._step_index >= len(STEP_ORDER):
            self.step = CalibrationStep.COMPLETE
        else:
            self.step = STEP_ORDER[self._step_index]

    def build_profile(
        self, dominant: Handedness = Handedness.RIGHT
    ) -> CalibrationProfile:
        """Solve the collected samples into a profile.

        Any step with too few samples falls back to the default for that
        value, so a partial calibration still yields a usable profile.
        """
        profile = CalibrationProfile(dominant_hand=dominant)

        closed = self._samples.get(CalibrationStep.PINCH_CLOSED, _StepSamples()).pinch
        opened = self._samples.get(CalibrationStep.PINCH_OPEN, _StepSamples()).pinch
        if closed and opened:
            lo, hi = statistics.median(closed), statistics.median(opened)
            if hi > lo:
                # Sit the threshold nearer the closed end: a missed pinch is
                # a much smaller annoyance than a phantom click.
                profile.pinch_threshold = lo + (hi - lo) * 0.4

        open_ext = self._samples.get(CalibrationStep.OPEN_HAND, _StepSamples()).extension
        fist_ext = self._samples.get(CalibrationStep.CLOSED_FIST, _StepSamples()).extension
        if open_ext and fist_ext:
            hi, lo = statistics.median(open_ext), statistics.median(fist_ext)
            if hi > lo:
                profile.extension_threshold = (hi + lo) / 2.0

        reach = self._samples.get(CalibrationStep.REACH_BOUNDS, _StepSamples())
        if len(reach.xs) >= 4:
            profile.reach_min_x = min(reach.xs)
            profile.reach_max_x = max(reach.xs)
            profile.reach_min_y = min(reach.ys)
            profile.reach_max_y = max(reach.ys)

        spans = [s for b in self._samples.values() for s in b.palm_span]
        if spans:
            profile.palm_span = statistics.median(spans)

        profile.calibrated = self.step is CalibrationStep.COMPLETE
        return profile
