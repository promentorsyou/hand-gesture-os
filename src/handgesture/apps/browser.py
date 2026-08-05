"""Web-browser controls.

This does not embed a browser. It models the *tab and history state* the
user manipulates with gestures, and emits key chords for the real browser
to act on. The model is what makes back/forward semantics testable; whether
the chord reaches Chrome is **UNVERIFIED** here.

The one rule worth stating: opening a URL is a navigation, and a navigation
truncates the forward history. Getting that wrong is the classic browser
bug where "forward" resurrects a page you have navigated away from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import ActionResult, App, OSRequest, register

#: Cap per tab, so a long session cannot grow history without limit.
MAX_HISTORY = 100


@dataclass(slots=True)
class Tab:
    title: str = "New tab"
    history: list[str] = field(default_factory=lambda: ["about:blank"])
    index: int = 0

    @property
    def url(self) -> str:
        return self.history[self.index]

    @property
    def can_go_back(self) -> bool:
        return self.index > 0

    @property
    def can_go_forward(self) -> bool:
        return self.index < len(self.history) - 1

    def navigate(self, url: str) -> None:
        # Everything ahead of the current entry is discarded — that is what
        # navigating means.
        del self.history[self.index + 1:]
        self.history.append(url)
        if len(self.history) > MAX_HISTORY:
            del self.history[: len(self.history) - MAX_HISTORY]
        self.index = len(self.history) - 1
        self.title = _title_for(url)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "canGoBack": self.can_go_back,
            "canGoForward": self.can_go_forward,
        }


def _title_for(url: str) -> str:
    trimmed = url.split("://", 1)[-1].strip("/")
    return trimmed.split("/", 1)[0] or url


@register
class BrowserApp(App):
    """Tabs, history, and the key chords that drive a real browser."""

    name = "browser"
    title = "Browser"

    def __init__(self) -> None:
        self.tabs: list[Tab] = [Tab()]
        self.index = 0

    @property
    def current(self) -> Tab:
        return self.tabs[self.index]

    @property
    def actions(self) -> tuple[str, ...]:
        return (
            "navigate", "back", "forward", "reload", "new_tab", "close_tab",
            "select_tab", "next_tab", "previous_tab", "zoom_in", "zoom_out",
            "find", "bookmark",
        )

    def state(self) -> dict[str, Any]:
        return {
            "tabs": [t.to_dict() for t in self.tabs],
            "index": self.index,
            "url": self.current.url,
            "canGoBack": self.current.can_go_back,
            "canGoForward": self.current.can_go_forward,
        }

    def action(self, name: str, /, **payload: Any) -> ActionResult:
        tab = self.current

        if name == "navigate":
            url = str(payload.get("url", "")).strip()
            if not url:
                return ActionResult.fail("a URL is required")
            tab.navigate(url)
            return ActionResult(
                message=url,
                requests=(OSRequest("browser.navigate", f"open {url}", {"url": url}),),
            )

        if name == "back":
            if not tab.can_go_back:
                return ActionResult.fail("no history behind")
            tab.index -= 1
            return self._chord("back", ("alt", "left"), tab.url)

        if name == "forward":
            if not tab.can_go_forward:
                return ActionResult.fail("no history ahead")
            tab.index += 1
            return self._chord("forward", ("alt", "right"), tab.url)

        if name == "reload":
            return self._chord("reload", ("ctrl", "r"), tab.url)

        if name == "new_tab":
            self.tabs.append(Tab())
            self.index = len(self.tabs) - 1
            return self._chord("new_tab", ("ctrl", "t"), "new tab")

        if name == "close_tab":
            if len(self.tabs) == 1:
                return ActionResult.fail("cannot close the last tab")
            del self.tabs[self.index]
            self.index = min(self.index, len(self.tabs) - 1)
            return self._chord("close_tab", ("ctrl", "w"), self.current.title)

        if name == "select_tab":
            index = int(payload.get("index", -1))
            if not 0 <= index < len(self.tabs):
                return ActionResult.fail("no such tab")
            self.index = index
            return ActionResult(message=self.current.title)

        if name in ("next_tab", "previous_tab"):
            step = 1 if name == "next_tab" else -1
            self.index = (self.index + step) % len(self.tabs)
            return ActionResult(message=self.current.title)

        if name in ("zoom_in", "zoom_out"):
            key = "plus" if name == "zoom_in" else "minus"
            return self._chord(name, ("ctrl", key), name)

        if name == "find":
            return self._chord("find", ("ctrl", "f"), "find")

        if name == "bookmark":
            return self._chord("bookmark", ("ctrl", "d"), "bookmark")

        return self.unknown(name)

    def _chord(self, operation: str, keys: tuple[str, ...], message: str) -> ActionResult:
        return ActionResult(
            message=message,
            requests=(
                OSRequest(
                    "keyboard.chord", "+".join(keys), {"keys": list(keys)}
                ),
            ),
        )
