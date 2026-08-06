"""The file browser.

This is the one app that can destroy something, so it is the one with the
most rules:

* Every path is resolved and checked to be **inside the configured root**.
  A path that escapes — via ``..``, an absolute path, or a symlink pointing
  out — is refused. This is not a security boundary against a hostile
  caller (the process can obviously read its own filesystem); it is a
  guard-rail so a misread gesture cannot wander into ``/``.
* **Deleting and moving are never performed here.** They return an
  :class:`OSRequest` with an operation name the confirmation gate already
  classifies as sensitive, so a confirmation gesture is required before
  anything happens. Copy and rename-in-place are performed directly.
* Nothing here elevates privileges or works around OS permissions. A
  ``PermissionError`` is reported as a failed action, not retried.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import ActionResult, App, OSRequest, register


@dataclass(frozen=True, slots=True)
class Entry:
    name: str
    is_dir: bool
    size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "isDir": self.is_dir, "size": self.size}


class PathOutsideRoot(ValueError):
    """Raised when a requested path escapes the browser's root."""


@register
class FilesApp(App):
    """A sandboxed file browser."""

    name = "files"
    title = "Files"

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or Path.home()).resolve()
        self.cwd = self.root
        self.selection: set[str] = set()
        #: Pending copy/cut, as (paths, is_cut).
        self.clipboard: tuple[tuple[Path, ...], bool] = ((), False)
        self.error: str = ""

    # --- Path safety ------------------------------------------------------

    def _resolve(self, name: str) -> Path:
        """Resolve ``name`` relative to the cwd, refusing anything outside root."""
        candidate = (self.cwd / name).resolve()
        # ``is_relative_to`` on the *resolved* path is what catches symlinks
        # pointing outside; checking the unresolved path would not.
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise PathOutsideRoot(name)
        return candidate

    # --- Listing ----------------------------------------------------------

    def entries(self) -> list[Entry]:
        """Directories first, then files, each alphabetical."""
        try:
            children = list(self.cwd.iterdir())
        except (OSError, PermissionError) as exc:
            self.error = str(exc)
            return []
        self.error = ""

        out: list[Entry] = []
        for child in children:
            try:
                is_dir = child.is_dir()
                size = 0 if is_dir else child.stat().st_size
            except OSError:
                # A broken symlink or a file that vanished mid-listing must
                # not take the whole listing down.
                is_dir, size = False, 0
            out.append(Entry(child.name, is_dir, size))
        return sorted(out, key=lambda e: (not e.is_dir, e.name.lower()))

    @property
    def relative_cwd(self) -> str:
        rel = self.cwd.relative_to(self.root).as_posix()
        return "/" if rel == "." else "/" + rel

    # --- App interface ----------------------------------------------------

    @property
    def actions(self) -> tuple[str, ...]:
        return (
            "open", "up", "home", "select", "select_all", "clear_selection",
            "copy", "cut", "paste", "rename", "delete", "new_folder",
        )

    def state(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "cwd": self.relative_cwd,
            "atRoot": self.cwd == self.root,
            "entries": [e.to_dict() for e in self.entries()],
            "selection": sorted(self.selection),
            "clipboard": {
                "count": len(self.clipboard[0]),
                "cut": self.clipboard[1],
            },
            "error": self.error,
        }

    def action(self, name: str, /, **payload: Any) -> ActionResult:
        try:
            return self._action(name, **payload)
        except PathOutsideRoot as exc:
            return ActionResult.fail(f"outside the browser root: {exc}")
        except PermissionError as exc:
            # Never work around the OS. Report and stop.
            return ActionResult.fail(f"permission denied: {exc}")
        except OSError as exc:
            return ActionResult.fail(str(exc))

    def _action(self, name: str, /, **payload: Any) -> ActionResult:
        if name == "open":
            target = self._resolve(str(payload.get("name", "")))
            if not target.is_dir():
                return ActionResult.fail("not a directory")
            self.cwd = target
            self.selection.clear()
            return ActionResult(message=self.relative_cwd)

        if name == "up":
            if self.cwd == self.root:
                return ActionResult.fail("already at the root")
            self.cwd = self.cwd.parent
            self.selection.clear()
            return ActionResult(message=self.relative_cwd)

        if name == "home":
            self.cwd = self.root
            self.selection.clear()
            return ActionResult(message="/")

        if name == "select":
            entry = str(payload.get("name", ""))
            if payload.get("toggle", True) and entry in self.selection:
                self.selection.discard(entry)
            else:
                self.selection.add(entry)
            return ActionResult(message=f"{len(self.selection)} selected")

        if name == "select_all":
            self.selection = {e.name for e in self.entries()}
            return ActionResult(message=f"{len(self.selection)} selected")

        if name == "clear_selection":
            self.selection.clear()
            return ActionResult()

        if name in ("copy", "cut"):
            if not self.selection:
                return ActionResult.fail("nothing selected")
            paths = tuple(self._resolve(n) for n in sorted(self.selection))
            self.clipboard = (paths, name == "cut")
            return ActionResult(message=f"{len(paths)} {name}")

        if name == "paste":
            return self._paste()

        if name == "new_folder":
            folder = self._resolve(str(payload.get("name", "New folder")))
            if folder.exists():
                return ActionResult.fail("already exists")
            folder.mkdir(parents=True)
            return ActionResult(message=folder.name)

        if name == "rename":
            return self._rename(str(payload.get("name", "")), str(payload.get("to", "")))

        if name == "delete":
            return self._delete()

        return self.unknown(name)

    # --- Operations -------------------------------------------------------

    def _rename(self, old: str, new: str) -> ActionResult:
        if not old or not new:
            return ActionResult.fail("both names are required")
        if "/" in new or "\\" in new:
            return ActionResult.fail("a name cannot contain a path separator")
        source = self._resolve(old)
        target = self._resolve(new)
        if not source.exists():
            return ActionResult.fail("no such file")
        if target.exists():
            return ActionResult.fail("target already exists")
        source.rename(target)
        self.selection.discard(old)
        return ActionResult(message=f"{old} -> {new}")

    def _paste(self) -> ActionResult:
        paths, is_cut = self.clipboard
        if not paths:
            return ActionResult.fail("clipboard is empty")

        if is_cut:
            # Moving is on the sensitive list: it destroys the original
            # location. It goes through the confirmation gate.
            self.clipboard = ((), False)
            return ActionResult(
                message=f"confirm moving {len(paths)} item(s)",
                requests=(
                    OSRequest(
                        "file.move_permanent",
                        f"move {len(paths)} item(s) to {self.relative_cwd}",
                        {"paths": [str(p) for p in paths], "target": str(self.cwd)},
                    ),
                ),
            )

        copied = 0
        for source in paths:
            target = self.cwd / source.name
            if target.exists():
                continue  # never silently overwrite
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
            copied += 1
        skipped = len(paths) - copied
        message = f"copied {copied}"
        if skipped:
            message += f", skipped {skipped} (already present)"
        return ActionResult(message=message)

    def _delete(self) -> ActionResult:
        if not self.selection:
            return ActionResult.fail("nothing selected")
        paths = [str(self._resolve(n)) for n in sorted(self.selection)]
        # Deletion is never performed here — only requested.
        return ActionResult(
            message=f"confirm deleting {len(paths)} item(s)",
            requests=(
                OSRequest(
                    "file.delete",
                    f"delete {len(paths)} item(s) from {self.relative_cwd}",
                    {"paths": paths},
                ),
            ),
        )

    # --- Confirmed execution ----------------------------------------------

    def execute_confirmed(self, operation: str, payload: dict[str, Any]) -> ActionResult:
        """Perform an operation the confirmation gate has approved.

        Called by the dispatcher *after* a confirmation gesture, never
        directly by an action.
        """
        paths = [Path(p) for p in payload.get("paths", [])]
        for path in paths:
            if path.resolve() != self.root and not path.resolve().is_relative_to(self.root):
                # Re-checked here: the payload made a round trip through the
                # confirmation gate, and a stale or tampered payload must not
                # be trusted just because it was confirmed.
                return ActionResult.fail(f"outside the browser root: {path}")

        try:
            if operation == "file.delete":
                for path in paths:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                self.selection.clear()
                return ActionResult(message=f"deleted {len(paths)}")

            if operation == "file.move_permanent":
                target = Path(payload.get("target", self.cwd))
                if target.resolve() != self.root and not target.resolve().is_relative_to(self.root):
                    return ActionResult.fail("target outside the browser root")
                moved = 0
                for path in paths:
                    destination = target / path.name
                    if destination.exists():
                        continue
                    shutil.move(str(path), str(destination))
                    moved += 1
                self.selection.clear()
                return ActionResult(message=f"moved {moved}")
        except PermissionError as exc:
            return ActionResult.fail(f"permission denied: {exc}")
        except OSError as exc:
            return ActionResult.fail(str(exc))

        return ActionResult.fail(f"cannot execute {operation}")
