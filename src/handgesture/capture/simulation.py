"""Synthetic and recorded landmark sources.

This is the backbone of testing. Real hand tracking needs a camera and a
human; these sources produce the same :class:`Frame` objects from generated
geometry or a recorded JSON session, so the entire pipeline downstream of
capture can be exercised deterministically and without hardware.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Sequence
from pathlib import Path

from ..types import (
    INDEX_MCP,
    LANDMARK_COUNT,
    MIDDLE_MCP,
    PINKY_MCP,
    RING_MCP,
    THUMB_TIP,
    Frame,
    Hand,
    Handedness,
    Point,
    make_hand,
)
from .base import LandmarkSource

# Canonical hand geometry, in palm-span units relative to the wrist at the
# origin, with the hand pointing up the -y axis (image coordinates).
# Index order matches MediaPipe's 21 landmarks.
_BASE_POSE: tuple[tuple[float, float], ...] = (
    (0.00, 0.00),    # 0  wrist
    (-0.32, -0.18),  # 1  thumb CMC
    (-0.52, -0.42),  # 2  thumb MCP
    (-0.62, -0.62),  # 3  thumb IP
    (-0.70, -0.80),  # 4  thumb tip
    (-0.22, -0.90),  # 5  index MCP
    (-0.26, -1.24),  # 6  index PIP
    (-0.28, -1.44),  # 7  index DIP
    (-0.30, -1.62),  # 8  index tip
    (0.00, -0.96),   # 9  middle MCP
    (0.00, -1.34),   # 10 middle PIP
    (0.00, -1.56),   # 11 middle DIP
    (0.00, -1.74),   # 12 middle tip
    (0.20, -0.92),   # 13 ring MCP
    (0.24, -1.26),   # 14 ring PIP
    (0.26, -1.46),   # 15 ring DIP
    (0.28, -1.62),   # 16 ring tip
    (0.38, -0.82),   # 17 pinky MCP
    (0.46, -1.08),   # 18 pinky PIP
    (0.50, -1.24),   # 19 pinky DIP
    (0.54, -1.38),   # 20 pinky tip
)

#: Landmarks belonging to each finger, ordered base -> tip, for curling.
_FINGER_CHAINS: tuple[tuple[int, ...], ...] = (
    (1, 2, 3, 4),        # thumb
    (5, 6, 7, 8),        # index
    (9, 10, 11, 12),     # middle
    (13, 14, 15, 16),    # ring
    (17, 18, 19, 20),    # pinky
)


def synth_hand(
    *,
    curl: Sequence[float] = (0.0, 0.0, 0.0, 0.0, 0.0),
    center: tuple[float, float] = (0.5, 0.5),
    scale: float = 0.18,
    rotation: float = 0.0,
    handedness: Handedness = Handedness.RIGHT,
    pinch: float | None = None,
    confidence: float = 0.95,
) -> Hand:
    """Build a synthetic hand.

    ``curl`` gives per-finger curl in ``[0, 1]`` (thumb, index, middle,
    ring, pinky) where 0 is fully extended and 1 is fully folded into the
    palm. ``pinch``, when given, overrides the thumb so its tip sits that
    many palm-spans from the index tip — the direct way to synthesise a
    pinch of a known size.
    """
    if len(curl) != 5:
        raise ValueError("curl needs one value per finger")

    pts = [list(p) for p in _BASE_POSE]

    # Curl each finger by pulling its joints toward the palm centre, scaling
    # the pull along the chain so the tip moves most.
    palm_x = sum(_BASE_POSE[i][0] for i in (INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)) / 4
    palm_y = sum(_BASE_POSE[i][1] for i in (INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)) / 4
    for finger, chain in enumerate(_FINGER_CHAINS):
        c = min(1.0, max(0.0, curl[finger]))
        if c <= 0.0:
            continue
        for depth, idx in enumerate(chain):
            weight = (depth + 1) / len(chain)
            t = c * weight
            # Thumb folds across the palm; fingers fold down into it.
            tx = palm_x if finger == 0 else _BASE_POSE[idx][0] * 0.35
            ty = palm_y * 0.65 if finger == 0 else palm_y * 0.55
            pts[idx][0] = _BASE_POSE[idx][0] * (1 - t) + tx * t
            pts[idx][1] = _BASE_POSE[idx][1] * (1 - t) + ty * t

    if pinch is not None:
        # Place the thumb tip a set distance from the index tip along the
        # line between the index tip and the thumb's natural rest position.
        ix, iy = pts[8]
        tx, ty = _BASE_POSE[THUMB_TIP]
        vx, vy = tx - ix, ty - iy
        length = math.hypot(vx, vy) or 1.0
        pts[THUMB_TIP] = [ix + vx / length * pinch, iy + vy / length * pinch]
        # Drag the IP joint along so the thumb stays plausible.
        pts[3] = [
            (pts[2][0] + pts[THUMB_TIP][0]) / 2,
            (pts[2][1] + pts[THUMB_TIP][1]) / 2,
        ]

    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    cx, cy = center
    out: list[tuple[float, float, float]] = []
    for x, y in pts:
        rx = x * cos_r - y * sin_r
        ry = x * sin_r + y * cos_r
        out.append((cx + rx * scale, cy + ry * scale, 0.0))

    if handedness is Handedness.LEFT:
        # Mirror about the hand's own centre so the geometry stays valid.
        mx = sum(p[0] for p in out) / len(out)
        out = [(2 * mx - p[0], p[1], p[2]) for p in out]

    return make_hand(out, handedness, confidence)


# --- Named poses, used by tests and the demo -------------------------------

def pose_open_palm(**kw) -> Hand:
    return synth_hand(curl=(0.0, 0.0, 0.0, 0.0, 0.0), **kw)


def pose_fist(**kw) -> Hand:
    return synth_hand(curl=(0.85, 0.95, 0.95, 0.95, 0.95), **kw)


def pose_point(**kw) -> Hand:
    return synth_hand(curl=(0.7, 0.0, 0.95, 0.95, 0.95), **kw)


def pose_peace(**kw) -> Hand:
    return synth_hand(curl=(0.7, 0.0, 0.0, 0.95, 0.95), **kw)


def pose_pinch(gap: float = 0.12, **kw) -> Hand:
    return synth_hand(curl=(0.0, 0.0, 0.6, 0.7, 0.7), pinch=gap, **kw)


def pose_thumbs_up(**kw) -> Hand:
    return synth_hand(curl=(0.0, 0.95, 0.95, 0.95, 0.95), **kw)


class SimulationSource(LandmarkSource):
    """Replays a fixed list of frames. Deterministic by construction."""

    name = "simulation"

    def __init__(self, frames: Sequence[Frame], loop: bool = False) -> None:
        self._frames = list(frames)
        self._loop = loop
        self._stopped = False

    def frames(self) -> Iterator[Frame]:
        self._stopped = False
        while True:
            for frame in self._frames:
                if self._stopped:
                    return
                yield frame
            if not self._loop:
                return

    def stop(self) -> None:
        self._stopped = True

    @classmethod
    def from_poses(
        cls,
        poses: Sequence[Hand | tuple[Hand, ...]],
        fps: float = 30.0,
        loop: bool = False,
    ) -> SimulationSource:
        """Build a source from a sequence of hands, spaced at ``fps``."""
        dt = 1.0 / fps
        frames = []
        for i, item in enumerate(poses):
            hands = item if isinstance(item, tuple) else (item,)
            frames.append(Frame(hands=hands, timestamp=i * dt))
        return cls(frames, loop=loop)


class RecordedSource(LandmarkSource):
    """Replays a session recorded to JSON by :class:`SessionRecorder`."""

    name = "recorded"

    def __init__(self, path: str | Path, loop: bool = False) -> None:
        self.path = Path(path)
        self._loop = loop
        self._stopped = False

    def frames(self) -> Iterator[Frame]:
        self._stopped = False
        data = json.loads(self.path.read_text())
        while True:
            for raw in data["frames"]:
                if self._stopped:
                    return
                yield _frame_from_json(raw)
            if not self._loop:
                return

    def stop(self) -> None:
        self._stopped = True


def _frame_from_json(raw: dict) -> Frame:
    hands = []
    for h in raw.get("hands", []):
        pts = [Point(p[0], p[1], p[2] if len(p) > 2 else 0.0) for p in h["landmarks"]]
        if len(pts) != LANDMARK_COUNT:
            continue
        hands.append(
            Hand(
                tuple(pts),
                Handedness(h.get("handedness", "unknown")),
                h.get("confidence", 1.0),
            )
        )
    return Frame(
        hands=tuple(hands),
        timestamp=raw.get("timestamp", 0.0),
        width=raw.get("width", 640),
        height=raw.get("height", 480),
        brightness=raw.get("brightness"),
    )


class SessionRecorder:
    """Captures frames to JSON so a real session can be replayed later.

    This is how a bug reported against live tracking becomes a regression
    test: record the session, commit the JSON, replay it in CI.
    """

    def __init__(self) -> None:
        self._frames: list[dict] = []

    def add(self, frame: Frame) -> None:
        self._frames.append(
            {
                "timestamp": frame.timestamp,
                "width": frame.width,
                "height": frame.height,
                "brightness": frame.brightness,
                "hands": [
                    {
                        "handedness": h.handedness.value,
                        "confidence": h.detection_confidence,
                        "landmarks": [[p.x, p.y, p.z] for p in h.landmarks],
                    }
                    for h in frame.hands
                ],
            }
        )

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"frames": self._frames}, indent=1))
        return target

    def __len__(self) -> int:
        return len(self._frames)
