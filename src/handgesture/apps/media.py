"""Music and video playback.

The player owns a playlist and a transport state; the actual sound comes
from whatever the OS is playing, driven by media keys. That split is why
this is testable: position, track selection, repeat and shuffle logic are
all pure, and the only OS contact is a media-key request.

Video is the same state machine with a couple of extra actions, so it
subclasses rather than duplicating.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .base import ActionResult, App, OSRequest, register


class Repeat(str, Enum):
    OFF = "off"
    ALL = "all"
    ONE = "one"


@dataclass(slots=True)
class Track:
    title: str
    artist: str = ""
    #: Length in seconds. Zero means unknown (a live stream, say).
    duration: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "artist": self.artist, "duration": self.duration}


@dataclass(slots=True)
class Transport:
    playing: bool = False
    position: float = 0.0
    volume: float = 0.6
    muted: bool = False


@register
class MusicApp(App):
    """A playlist-driven music player."""

    name = "music"
    title = "Music"

    def __init__(self, tracks: list[Track] | None = None) -> None:
        self.tracks: list[Track] = tracks if tracks is not None else _demo_tracks()
        self.index = 0
        self.transport = Transport()
        self.repeat = Repeat.OFF
        self.shuffle = False
        self._rng = random.Random(0)  # deterministic, so shuffle is testable

    @property
    def current(self) -> Track | None:
        if not self.tracks:
            return None
        return self.tracks[self.index]

    @property
    def actions(self) -> tuple[str, ...]:
        return (
            "play", "pause", "play_pause", "next", "previous", "seek",
            "set_volume", "mute", "unmute", "select", "repeat", "shuffle",
        )

    def state(self) -> dict[str, Any]:
        track = self.current
        return {
            "tracks": [t.to_dict() for t in self.tracks],
            "index": self.index,
            "current": track.to_dict() if track else None,
            "playing": self.transport.playing,
            "position": round(self.transport.position, 2),
            "volume": round(self.transport.volume, 3),
            "muted": self.transport.muted,
            "repeat": self.repeat.value,
            "shuffle": self.shuffle,
        }

    def action(self, name: str, /, **payload: Any) -> ActionResult:
        if not self.tracks and name in ("play", "play_pause", "next", "previous"):
            return ActionResult.fail("playlist is empty")

        if name == "play":
            self.transport.playing = True
            return self._transport_result("play_pause", "playing")

        if name == "pause":
            self.transport.playing = False
            return self._transport_result("play_pause", "paused")

        if name == "play_pause":
            self.transport.playing = not self.transport.playing
            return self._transport_result(
                "play_pause", "playing" if self.transport.playing else "paused"
            )

        if name == "next":
            return self._step(1)

        if name == "previous":
            # Matches every music player ever: part-way into a track,
            # "previous" restarts it rather than skipping back.
            if self.transport.position > 3.0:
                self.transport.position = 0.0
                return ActionResult(message="restarted")
            return self._step(-1)

        if name == "seek":
            return self._seek(float(payload.get("position", 0.0)))

        if name == "set_volume":
            level = max(0.0, min(1.0, float(payload.get("level", 0.0))))
            self.transport.volume = level
            self.transport.muted = level == 0.0
            return ActionResult(
                message=f"{int(level * 100)}%",
                requests=(OSRequest("audio.set_volume", "set volume",
                                    {"level": level}),),
            )

        if name in ("mute", "unmute"):
            self.transport.muted = name == "mute"
            return ActionResult(
                message=name,
                requests=(OSRequest(f"audio.{name}", name),),
            )

        if name == "select":
            index = int(payload.get("index", -1))
            if not 0 <= index < len(self.tracks):
                return ActionResult.fail("no such track")
            self.index = index
            self.transport.position = 0.0
            return ActionResult(message=self.tracks[index].title)

        if name == "repeat":
            order = [Repeat.OFF, Repeat.ALL, Repeat.ONE]
            self.repeat = order[(order.index(self.repeat) + 1) % len(order)]
            return ActionResult(message=self.repeat.value)

        if name == "shuffle":
            self.shuffle = not self.shuffle
            return ActionResult(message="on" if self.shuffle else "off")

        return self.unknown(name)

    # --- Helpers ----------------------------------------------------------

    def _transport_result(self, key: str, message: str) -> ActionResult:
        return ActionResult(
            message=message, requests=(OSRequest(f"media.{key}", message),)
        )

    def _seek(self, position: float) -> ActionResult:
        track = self.current
        if track is None:
            return ActionResult.fail("nothing playing")
        limit = track.duration if track.duration > 0 else position
        self.transport.position = max(0.0, min(limit, position))
        return ActionResult(message=f"{self.transport.position:.1f}s")

    def _step(self, direction: int) -> ActionResult:
        self.transport.position = 0.0

        if self.repeat is Repeat.ONE:
            return ActionResult(message="repeating track")

        if self.shuffle and len(self.tracks) > 1:
            # Never shuffle to the track already playing — it reads as a
            # broken skip button.
            choices = [i for i in range(len(self.tracks)) if i != self.index]
            self.index = self._rng.choice(choices)
            return ActionResult(message=self.tracks[self.index].title)

        nxt = self.index + direction
        if nxt >= len(self.tracks) or nxt < 0:
            if self.repeat is Repeat.ALL:
                nxt %= len(self.tracks)
            else:
                self.index = max(0, min(len(self.tracks) - 1, nxt))
                self.transport.playing = False
                return ActionResult(message="end of playlist")
        self.index = nxt
        return ActionResult(
            message=self.tracks[self.index].title,
            requests=(OSRequest(
                "media.next" if direction > 0 else "media.previous", "skip"
            ),),
        )


@register
class VideoApp(MusicApp):
    """The music player plus fullscreen, subtitles, and frame stepping."""

    name = "video"
    title = "Video"

    def __init__(self, tracks: list[Track] | None = None) -> None:
        super().__init__(tracks if tracks is not None else _demo_videos())
        self.fullscreen = False
        self.subtitles = False

    @property
    def actions(self) -> tuple[str, ...]:
        return super().actions + ("fullscreen", "subtitles", "skip")

    def state(self) -> dict[str, Any]:
        return super().state() | {
            "fullscreen": self.fullscreen,
            "subtitles": self.subtitles,
        }

    def action(self, name: str, /, **payload: Any) -> ActionResult:
        if name == "fullscreen":
            self.fullscreen = not self.fullscreen
            return ActionResult(message="on" if self.fullscreen else "off")
        if name == "subtitles":
            self.subtitles = not self.subtitles
            return ActionResult(message="on" if self.subtitles else "off")
        if name == "skip":
            return self._seek(self.transport.position + float(payload.get("seconds", 10)))
        return super().action(name, **payload)


def _demo_tracks() -> list[Track]:
    return [
        Track("Aurora", "Nightfall", 214.0),
        Track("Long Way Down", "Nightfall", 187.0),
        Track("Static Bloom", "Kite String", 243.0),
    ]


def _demo_videos() -> list[Track]:
    return [
        Track("Calibration walkthrough", "hand-gesture-os", 96.0),
        Track("Gesture reference", "hand-gesture-os", 240.0),
    ]


__all__ = ["MusicApp", "Repeat", "Track", "Transport", "VideoApp"]
