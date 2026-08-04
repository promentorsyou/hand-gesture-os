"""The end-to-end gesture pipeline.

Frames in, intents out. This is the single place where capture, tracking,
recognition, debouncing, safety, and cursor mapping meet, so the ordering
of those concerns is stated once and testable in isolation.

Ordering matters and is deliberate:

1. **Emergency stop** is checked first — nothing else runs while engaged.
2. **Tracking health** (hand loss, low light) can itself trip the stop.
3. **Recognition** turns the frame into a candidate gesture.
4. **Emergency-stop gesture** is handled before mode filtering, so it works
   from every mode including paused.
5. **Mode filtering** drops gestures that are not live in the current mode.
6. **Debouncing** decides whether the user meant it.
7. **Cursor** updates only for gestures that should move it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .calibration.calibrator import CalibrationProfile
from .control.cursor import CursorConfig, CursorController, CursorState
from .control.safety import ConfirmationGate, EmergencyStop, StopReason
from .gestures.debounce import DebounceConfig, EventType, GestureDebouncer, GestureEvent
from .gestures.recognizer import GestureRecognizer
from .gestures.vocabulary import Gesture, Mode, gesture_allowed
from .types import Frame, Hand, Handedness


class TrackingHealth(str, Enum):
    OK = "ok"
    NO_HANDS = "no_hands"
    LOW_CONFIDENCE = "low_confidence"
    LOW_LIGHT = "low_light"


@dataclass(slots=True)
class PipelineConfig:
    cursor: CursorConfig = field(default_factory=CursorConfig)
    debounce: DebounceConfig = field(default_factory=DebounceConfig)
    min_gesture_confidence: float = 0.55
    #: Frame brightness below which we warn and stop trusting tracking.
    low_light_threshold: float = 0.12
    #: Seconds without a hand before tracking is treated as lost.
    hand_loss_seconds: float = 0.5
    #: Whether losing the hand should trip the emergency stop. On by
    #: default: a dragged file should not be dropped somewhere random
    #: because the camera lost track mid-drag.
    stop_on_hand_loss: bool = True
    #: Which hand drives the cursor when both are visible.
    dominant_hand: Handedness = Handedness.RIGHT
    one_hand_mode: bool = False


@dataclass(slots=True)
class PipelineState:
    """Everything the UI needs to render one frame of feedback."""

    mode: Mode = Mode.NAVIGATION
    gesture: Gesture = Gesture.NONE
    confidence: float = 0.0
    health: TrackingHealth = TrackingHealth.NO_HANDS
    cursor: CursorState | None = None
    hand_count: int = 0
    emergency_stopped: bool = False
    stop_reason: StopReason | None = None
    events: list[GestureEvent] = field(default_factory=list)
    #: Per-gesture scores, for the confidence readout in the UI.
    scores: dict[Gesture, float] = field(default_factory=dict)
    pending_confirmation: str | None = None


#: Gestures that drive the cursor. Everything else leaves it parked, so the
#: pointer does not drift while you are, say, holding a fist to grab.
_CURSOR_GESTURES = frozenset(
    {Gesture.POINT, Gesture.PINCH, Gesture.PINCH_HOLD, Gesture.PINCH_DRAG}
)


class GesturePipeline:
    """Turns frames into debounced, mode-filtered, safety-gated intents."""

    def __init__(
        self,
        config: PipelineConfig | None = None,
        profile: CalibrationProfile | None = None,
    ) -> None:
        self.config = config or PipelineConfig()
        self.profile = profile or CalibrationProfile()

        if profile is not None and profile.calibrated:
            # A calibrated user's measured reach defines the active region.
            self.config.cursor.margin = profile.cursor_margin

        self.recognizer = GestureRecognizer(self.config.min_gesture_confidence)
        self.debouncer = GestureDebouncer(self.config.debounce)
        self.cursor = CursorController(self.config.cursor)
        self.emergency_stop = EmergencyStop()
        self.confirmation = ConfirmationGate()

        self.mode: Mode = Mode.NAVIGATION
        self._last_hand_seen: float | None = None
        self._previous_mode: Mode = Mode.NAVIGATION

    # --- Mode ----------------------------------------------------------
    def set_mode(self, mode: Mode) -> None:
        if mode is self.mode:
            return
        if mode is Mode.PAUSED:
            self._previous_mode = self.mode
        self.mode = mode
        # Never carry a half-built gesture across a mode change.
        self.debouncer.reset()

    def toggle_pause(self) -> Mode:
        self.set_mode(self._previous_mode if self.mode is Mode.PAUSED else Mode.PAUSED)
        return self.mode

    # --- Safety --------------------------------------------------------
    def engage_stop(self, reason: StopReason, now: float) -> None:
        if self.emergency_stop.engage(reason, now):
            self.debouncer.reset()
            self.cursor.reset()

    def release_stop(self) -> None:
        self.emergency_stop.release()
        self.debouncer.reset()

    # --- Main loop -----------------------------------------------------
    def process(self, frame: Frame) -> PipelineState:
        now = frame.timestamp
        state = PipelineState(mode=self.mode, hand_count=frame.hand_count)

        # 1. Emergency stop wins over everything.
        if self.emergency_stop.engaged:
            state.emergency_stopped = True
            state.stop_reason = self.emergency_stop.reason
            state.health = self._health(frame)
            return state

        # 2. Tracking health.
        health = self._health(frame)
        state.health = health

        if frame.hand_count > 0:
            self._last_hand_seen = now
        elif self._last_hand_seen is not None:
            lost_for = now - self._last_hand_seen
            if lost_for > self.config.hand_loss_seconds:
                # Capture what was in flight *before* releasing it — the
                # update below clears active_gesture, so checking after
                # would always see None and never trip the safety stop.
                was_active = self.debouncer.active_gesture
                # Release any in-flight gesture cleanly rather than leaving
                # a drag or a mouse-down dangling.
                state.events.extend(self.debouncer.update(Gesture.NONE, 0.0, now))
                if self.config.stop_on_hand_loss and was_active:
                    self.engage_stop(StopReason.TRACKING_LOST, now)
                    state.emergency_stopped = True
                    state.stop_reason = StopReason.TRACKING_LOST
                self.cursor.reset()
                return state

        if health is TrackingHealth.LOW_LIGHT:
            # Warn but keep running — tracking degrades rather than dies,
            # and yanking control away entirely would be worse.
            state.events.extend(self.debouncer.update(Gesture.NONE, 0.0, now))
            return state

        if frame.hand_count == 0:
            state.events.extend(self.debouncer.update(Gesture.NONE, 0.0, now))
            return state

        # 3. Recognise.
        recognition = self.recognizer.recognize(frame)
        state.scores = recognition.scores
        state.gesture = recognition.gesture
        state.confidence = recognition.confidence

        # 4. Emergency-stop gesture, before mode filtering so it always works.
        if recognition.gesture is Gesture.CROSSED_HANDS:
            events = self.debouncer.update(
                Gesture.CROSSED_HANDS, recognition.confidence, now
            )
            state.events.extend(events)
            if any(e.type is EventType.BEGIN for e in events):
                self.engage_stop(StopReason.GESTURE, now)
                state.emergency_stopped = True
                state.stop_reason = StopReason.GESTURE
            return state

        # 5. Mode filtering.
        gesture = recognition.gesture
        confidence = recognition.confidence
        if not gesture_allowed(self.mode, gesture):
            gesture, confidence = Gesture.NONE, 0.0

        # 6. Debounce.
        state.events.extend(self.debouncer.update(gesture, confidence, now))

        # 7. Cursor.
        active = self.debouncer.active_gesture
        if gesture in _CURSOR_GESTURES or active in _CURSOR_GESTURES:
            hand = self._cursor_hand(frame)
            if hand is not None:
                state.cursor = self.cursor.update(hand.palm_center, now)

        pending = self.confirmation.pending
        state.pending_confirmation = pending.operation if pending else None
        return state

    # --- Helpers -------------------------------------------------------
    def _cursor_hand(self, frame: Frame) -> Hand | None:
        if frame.hand_count == 0:
            return None
        if self.config.one_hand_mode or frame.hand_count == 1:
            return frame.hands[0]
        return frame.hand_by(self.config.dominant_hand) or frame.hands[0]

    def _health(self, frame: Frame) -> TrackingHealth:
        if frame.brightness is not None and frame.brightness < self.config.low_light_threshold:
            return TrackingHealth.LOW_LIGHT
        if frame.hand_count == 0:
            return TrackingHealth.NO_HANDS
        weakest = min(h.detection_confidence for h in frame.hands)
        if weakest < 0.5:
            return TrackingHealth.LOW_CONFIDENCE
        return TrackingHealth.OK
