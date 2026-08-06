"""Driving the spatial workspace with hands.

The workspace is a pure state machine; this is the layer that decides which
gesture means which workspace operation. It sits *beside* the OS dispatcher
rather than inside it: window management in the spatial interface moves
windows drawn over the camera view, which is a different thing from asking
the host OS to move its own windows.

The gesture map, for ``Mode.WINDOW``:

============  ==================================================
gesture       meaning
============  ==================================================
fist          grab — title bar moves, bottom-right corner resizes
spread        maximise the focused window
converge      restore it
swipe up      show the multitasking deck
swipe down    minimise the focused window
swipe L/R     cycle apps (previous / next)
palm forward  home screen
peace         quick-settings shade
============  ==================================================

A grab is bound to the gesture that started it. If the fist ends, the hand
is lost, or the emergency stop engages, the grab ends too — a window left
stuck to a hand that is no longer there is the spatial equivalent of the
stuck mouse button Phase 2 had to fix.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..gestures.debounce import EventType, GestureEvent
from ..gestures.vocabulary import Gesture, Mode
from ..types import Point
from .window import GrabKind
from .workspace import CloseResult, Overlay, Workspace


@dataclass(frozen=True, slots=True)
class SpatialAction:
    """A workspace change, for the UI and for tests."""

    name: str
    window_id: int | None = None
    detail: str = ""


#: Zoom magnitude (in palm spans of two-hand separation change) needed to
#: maximise or restore. Well above the motion tracker's own deadzone so an
#: unsteady two-handed pose cannot flip a window's state.
ZOOM_THRESHOLD = 0.18


class SpatialController:
    """Maps debounced gestures and motion onto workspace operations."""

    def __init__(
        self,
        workspace: Workspace | None = None,
        *,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> None:
        self.workspace = workspace or Workspace()
        self.screen_width = screen_width
        self.screen_height = screen_height
        self._grab_gesture: Gesture | None = None

    # --- Coordinates ----------------------------------------------------

    def to_workspace(self, cursor: tuple[float, float] | None) -> Point | None:
        """Screen pixels -> normalised workspace coordinates."""
        if cursor is None:
            return None
        return Point(
            x=cursor[0] / max(1, self.screen_width),
            y=cursor[1] / max(1, self.screen_height),
        )

    # --- Main entry point ------------------------------------------------

    def handle(self, state) -> list[SpatialAction]:
        """Apply one pipeline state to the workspace.

        Takes the ``PipelineState`` rather than raw gestures so the
        controller sees the cursor, the motion result, and the debounced
        events together — a grab needs all three.
        """
        if state.emergency_stopped:
            return self._abort("emergency_stop")
        if state.mode is not Mode.WINDOW:
            # Leaving window mode must not strand a grab.
            return self._abort("mode_changed") if self.workspace.grabbing else []

        actions: list[SpatialAction] = []
        point = self.to_workspace(
            (state.cursor.x, state.cursor.y) if state.cursor is not None else None
        )

        actions.extend(self._handle_events(state.events, point))
        actions.extend(self._handle_grab_motion(state, point))
        actions.extend(self._handle_motion(state))
        return actions

    # --- Pieces ----------------------------------------------------------

    def _abort(self, reason: str) -> list[SpatialAction]:
        if not self.workspace.grabbing:
            self._grab_gesture = None
            return []
        window = self.workspace.end_grab()
        self._grab_gesture = None
        return [
            SpatialAction(
                "grab_end", window.id if window else None, detail=reason
            )
        ]

    def _handle_events(
        self, events: list[GestureEvent], point: Point | None
    ) -> list[SpatialAction]:
        out: list[SpatialAction] = []
        for event in events:
            if event.gesture is Gesture.FIST:
                if event.type is EventType.BEGIN and point is not None:
                    kind = self.workspace.begin_grab(point)
                    self._grab_gesture = Gesture.FIST
                    if kind is not GrabKind.NONE:
                        grab = self.workspace.grab
                        out.append(
                            SpatialAction(
                                "grab_begin",
                                grab.window_id if grab else None,
                                detail=kind.value,
                            )
                        )
                elif event.type is EventType.END:
                    out.extend(self._release_grab())
            elif event.gesture is Gesture.PALM_FORWARD and event.type is EventType.BEGIN:
                self.workspace.toggle_overlay(Overlay.HOME)
                out.append(SpatialAction("home", detail=self.workspace.overlay.value))
            elif event.gesture is Gesture.PEACE and event.type is EventType.BEGIN:
                self.workspace.toggle_overlay(Overlay.QUICK_SETTINGS)
                out.append(
                    SpatialAction("quick_settings", detail=self.workspace.overlay.value)
                )
        return out

    def _release_grab(self) -> list[SpatialAction]:
        self._grab_gesture = None
        if not self.workspace.grabbing:
            return []
        window = self.workspace.end_grab()
        return [SpatialAction("grab_end", window.id if window else None)]

    def _handle_grab_motion(self, state, point: Point | None) -> list[SpatialAction]:
        if not self.workspace.grabbing or point is None:
            return []
        # The grab only follows the hand while the gesture that started it
        # is still held.
        if self._grab_gesture is not None and state.gesture is not self._grab_gesture:
            return []
        grab = self.workspace.grab
        target = self.workspace.get(grab.window_id) if grab else None
        before = target.rect if target else None
        window = self.workspace.update_grab(point)
        if window is None:
            return []
        # The frame the grab began on has zero delta. Reporting a move that
        # did not happen would make the UI flash a drag indicator on every
        # grab that only meant to focus.
        if window.rect == before:
            return []
        name = "window_move" if grab and grab.kind is GrabKind.MOVE else "window_resize"
        return [SpatialAction(name, window.id)]

    def _handle_motion(self, state) -> list[SpatialAction]:
        motion = state.motion
        if motion is None:
            return []
        out: list[SpatialAction] = []

        if abs(motion.zoom) >= ZOOM_THRESHOLD and not self.workspace.grabbing:
            window = (
                self.workspace.maximize()
                if motion.zoom > 0
                else self.workspace.restore()
            )
            if window is not None:
                out.append(
                    SpatialAction(
                        "maximize" if motion.zoom > 0 else "restore", window.id
                    )
                )

        gesture = motion.gesture
        if gesture is Gesture.SWIPE_UP:
            self.workspace.toggle_overlay(Overlay.MULTITASK)
            out.append(
                SpatialAction("multitask", detail=self.workspace.overlay.value)
            )
        elif gesture is Gesture.SWIPE_DOWN:
            window = self.workspace.minimize()
            if window is not None:
                out.append(SpatialAction("minimize", window.id))
        elif gesture in (Gesture.SWIPE_LEFT, Gesture.SWIPE_RIGHT):
            step = -1 if gesture is Gesture.SWIPE_LEFT else 1
            window = self.workspace.cycle(step)
            if window is not None:
                out.append(SpatialAction("switch_app", window.id, detail=window.app))
        return out

    # --- Closing ----------------------------------------------------------

    def request_close(self, window_id: int) -> CloseResult:
        """Close a window, deferring to confirmation when work is unsaved.

        Exposed here so the server can route the confirmation through the
        same gate as every other sensitive operation rather than inventing
        a second path.
        """
        return self.workspace.request_close(window_id)
