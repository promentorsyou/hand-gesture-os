"""Landmark source interface.

Anything that can produce :class:`~handgesture.types.Frame` objects is a
valid input: a real webcam through MediaPipe, a recorded session replayed
from disk, or synthetic poses generated in a test.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..types import Frame


class LandmarkSource(ABC):
    """Produces frames of hand landmarks."""

    name: str = "abstract"

    @abstractmethod
    def frames(self) -> Iterator[Frame]:
        """Yield frames until the source is exhausted or stopped."""

    def start(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Acquire resources. Safe to call more than once.

        Optional: sources with nothing to acquire inherit the no-op.
        """

    def stop(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Release resources. Safe to call more than once."""

    def __enter__(self) -> LandmarkSource:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
