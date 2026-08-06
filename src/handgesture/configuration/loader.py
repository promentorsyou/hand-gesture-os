"""Loading the user-editable configuration.

Until now ``gestures.yaml`` and the profiles were documentation: the README
told you to edit them and nothing read them. This makes them real.

Three decisions worth stating:

* **Defaults ship inside the package.** They used to sit at the repository
  root, outside the installed package, so a ``pip install`` produced a copy
  with no configuration at all. They now live in
  ``handgesture/configuration/defaults`` and travel with the wheel.
* **Unknown keys are reported, not ignored.** A typo in a config file that
  silently does nothing is the worst possible outcome for a file whose whole
  purpose is tuning behaviour. Loading returns the list of problems
  alongside the config so the CLI can print them.
* **A bad value never takes the system down.** Out-of-range numbers are
  reported and the default is kept. A config file should not be able to
  leave you unable to gesture your way back to a working state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..control.cursor import CursorConfig
from ..gestures.debounce import DebounceConfig
from ..pipeline import PipelineConfig
from ..types import Handedness

DEFAULTS_DIR = Path(__file__).parent / "defaults"
DEFAULT_GESTURES = DEFAULTS_DIR / "gestures.yaml"
PROFILES_DIR = DEFAULTS_DIR / "profiles"

#: Where a config file is looked for when none is named, in order.
SEARCH_PATHS = (
    Path("handgesture.yaml"),
    Path("config/gestures.yaml"),
    Path.home() / ".config" / "handgesture" / "gestures.yaml",
)

#: Allowed range for every numeric setting. A value outside its range is
#: refused with a message rather than clamped, for the same reason the
#: settings app refuses them: a silently corrected value hides the mistake.
_RANGES: dict[str, tuple[float, float]] = {
    "recognition.min_confidence": (0.1, 0.99),
    "recognition.pinch_threshold": (0.05, 1.0),
    "recognition.extension_threshold": (0.05, 1.0),
    "debounce.hold_frames": (1, 60),
    "debounce.hold_seconds": (0.0, 3.0),
    "debounce.cooldown_seconds": (0.0, 5.0),
    "debounce.min_confidence": (0.1, 0.99),
    "debounce.dropout_grace_seconds": (0.0, 2.0),
    "cursor.margin": (0.0, 0.45),
    "cursor.gain": (0.05, 5.0),
    "cursor.acceleration": (0.0, 3.0),
    "cursor.precision_scale": (0.05, 1.0),
    "cursor.smoothing_min_cutoff": (0.05, 10.0),
    "cursor.smoothing_beta": (0.0, 1.0),
    "tracking.low_light_threshold": (0.0, 0.9),
    "tracking.hand_loss_seconds": (0.05, 10.0),
}

_BOOLS = frozenset(
    {"cursor.mirror_x", "tracking.stop_on_hand_loss", "tracking.one_hand_mode"}
)


@dataclass(slots=True)
class LoadedConfig:
    """A config file turned into live settings, plus whatever was wrong with it."""

    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    #: gesture -> action name, per mode.
    bindings: dict[str, dict[str, str]] = field(default_factory=dict)
    #: operation -> "critical" | "confirm".
    confirmations: dict[str, str] = field(default_factory=dict)
    #: Human-readable problems. Empty means the file was clean.
    problems: list[str] = field(default_factory=list)
    source: Path | None = None
    profile: str | None = None

    @property
    def ok(self) -> bool:
        return not self.problems


def _read_yaml(path: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        import yaml
    except ImportError:
        return {}, ["pyyaml is not installed; using built-in defaults"]

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, [f"{path}: {exc}"]

    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # yaml raises several types
        return {}, [f"{path}: invalid YAML: {exc}"]

    if data is None:
        return {}, []
    if not isinstance(data, dict):
        return {}, [f"{path}: expected a mapping at the top level"]
    return data, []


def find_config(explicit: str | Path | None = None) -> Path:
    """Locate a config file: explicit, then the search path, then defaults."""
    if explicit is not None:
        return Path(explicit)
    for candidate in SEARCH_PATHS:
        if candidate.is_file():
            return candidate
    return DEFAULT_GESTURES


def available_profiles() -> list[str]:
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


def load(
    path: str | Path | None = None, profile: str | None = None
) -> LoadedConfig:
    """Load configuration, applying ``profile`` on top if given."""
    resolved = find_config(path)
    out = LoadedConfig(source=resolved)

    if not resolved.is_file():
        out.problems.append(f"{resolved}: no such file; using built-in defaults")
        return out

    data, problems = _read_yaml(resolved)
    out.problems.extend(problems)

    _apply_sections(out, data)
    out.bindings = _read_bindings(out, data)
    out.confirmations = _read_confirmations(out, data)

    if profile:
        _apply_profile(out, profile)

    return out


# --- Sections --------------------------------------------------------------

_KNOWN_SECTIONS = frozenset(
    {"version", "recognition", "debounce", "cursor", "tracking", "bindings",
     "confirmations", "keyboard"}
)


def _apply_sections(out: LoadedConfig, data: dict[str, Any]) -> None:
    for section in data:
        if section not in _KNOWN_SECTIONS:
            out.problems.append(f"unknown section {section!r}")

    config = out.pipeline
    get = _reader(out, data)

    value = get("recognition.min_confidence")
    if value is not None:
        config.min_gesture_confidence = value

    debounce: DebounceConfig = config.debounce
    for key, attr in (
        ("debounce.hold_frames", "hold_frames"),
        ("debounce.hold_seconds", "hold_seconds"),
        ("debounce.cooldown_seconds", "cooldown_seconds"),
        ("debounce.min_confidence", "min_confidence"),
        ("debounce.dropout_grace_seconds", "dropout_grace_seconds"),
    ):
        value = get(key)
        if value is not None:
            setattr(debounce, attr, int(value) if attr == "hold_frames" else value)

    cursor: CursorConfig = config.cursor
    for key, attr in (
        ("cursor.margin", "margin"),
        ("cursor.gain", "gain"),
        ("cursor.acceleration", "acceleration"),
        ("cursor.precision_scale", "precision_scale"),
        ("cursor.smoothing_min_cutoff", "smoothing_min_cutoff"),
        ("cursor.smoothing_beta", "smoothing_beta"),
        ("cursor.mirror_x", "mirror_x"),
    ):
        value = get(key)
        if value is not None:
            setattr(cursor, attr, value)

    for key, attr in (
        ("tracking.low_light_threshold", "low_light_threshold"),
        ("tracking.hand_loss_seconds", "hand_loss_seconds"),
        ("tracking.stop_on_hand_loss", "stop_on_hand_loss"),
    ):
        value = get(key)
        if value is not None:
            setattr(config, attr, value)

    hand = _get_path(data, "tracking.dominant_hand")
    if hand is not None:
        if hand in ("left", "right"):
            config.dominant_hand = (
                Handedness.LEFT if hand == "left" else Handedness.RIGHT
            )
        else:
            out.problems.append("tracking.dominant_hand must be 'left' or 'right'")

    one_hand = _get_path(data, "tracking.one_hand_mode")
    if one_hand is not None:
        if isinstance(one_hand, bool):
            config.one_hand_mode = one_hand
        else:
            out.problems.append("tracking.one_hand_mode must be true or false")


def _reader(out: LoadedConfig, data: dict[str, Any]):
    """Return a getter that validates as it reads."""

    def get(dotted: str):
        raw = _get_path(data, dotted)
        if raw is None:
            return None
        if dotted in _BOOLS:
            if not isinstance(raw, bool):
                out.problems.append(f"{dotted} must be true or false")
                return None
            return raw
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            out.problems.append(f"{dotted} must be a number")
            return None
        low, high = _RANGES.get(dotted, (float("-inf"), float("inf")))
        if not low <= raw <= high:
            out.problems.append(f"{dotted} must be between {low} and {high}")
            return None
        return float(raw)

    return get


def _get_path(data: dict[str, Any], dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _read_bindings(out: LoadedConfig, data: dict[str, Any]) -> dict[str, dict[str, str]]:
    from ..gestures.vocabulary import Gesture, Mode

    raw = data.get("bindings") or {}
    if not isinstance(raw, dict):
        out.problems.append("bindings must be a mapping of mode -> gesture -> action")
        return {}

    modes = {m.value for m in Mode}
    gestures = {g.value for g in Gesture}
    bindings: dict[str, dict[str, str]] = {}

    for mode, mapping in raw.items():
        if mode not in modes:
            out.problems.append(f"bindings: unknown mode {mode!r}")
            continue
        if not isinstance(mapping, dict):
            out.problems.append(f"bindings.{mode} must be a mapping")
            continue
        clean: dict[str, str] = {}
        for gesture, action in mapping.items():
            if gesture not in gestures:
                out.problems.append(f"bindings.{mode}: unknown gesture {gesture!r}")
                continue
            clean[gesture] = str(action)
        bindings[mode] = clean
    return bindings


def _read_confirmations(out: LoadedConfig, data: dict[str, Any]) -> dict[str, str]:
    raw = data.get("confirmations") or {}
    if not isinstance(raw, dict):
        out.problems.append("confirmations must be a mapping")
        return {}

    result: dict[str, str] = {}
    for level, operations in raw.items():
        if level not in ("critical", "confirm"):
            out.problems.append(f"confirmations: unknown level {level!r}")
            continue
        if not isinstance(operations, list):
            out.problems.append(f"confirmations.{level} must be a list")
            continue
        for operation in operations:
            result[str(operation)] = level
    return result


def _apply_profile(out: LoadedConfig, profile: str) -> None:
    path = Path(profile)
    if not path.is_file():
        path = PROFILES_DIR / f"{profile}.yaml"
    if not path.is_file():
        known = ", ".join(available_profiles()) or "none"
        out.problems.append(f"unknown profile {profile!r} (available: {known})")
        return

    data, problems = _read_yaml(path)
    out.problems.extend(problems)
    out.profile = data.get("name", path.stem)

    config = out.pipeline
    flat = {
        "cursor_margin": (config.cursor, "margin", (0.0, 0.45)),
        "cursor_gain": (config.cursor, "gain", (0.05, 5.0)),
        "smoothing_min_cutoff": (config.cursor, "smoothing_min_cutoff", (0.05, 10.0)),
        "debounce_hold_frames": (config.debounce, "hold_frames", (1, 60)),
        "debounce_hold_seconds": (config.debounce, "hold_seconds", (0.0, 3.0)),
        "debounce_cooldown_seconds": (config.debounce, "cooldown_seconds", (0.0, 5.0)),
        "min_confidence": (config.debounce, "min_confidence", (0.1, 0.99)),
    }
    for key, (target, attr, (low, high)) in flat.items():
        if key not in data:
            continue
        raw = data[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            out.problems.append(f"profile {out.profile}: {key} must be a number")
            continue
        if not low <= raw <= high:
            out.problems.append(
                f"profile {out.profile}: {key} must be between {low} and {high}"
            )
            continue
        setattr(target, attr, int(raw) if attr == "hold_frames" else float(raw))

    if "min_confidence" in data and isinstance(data["min_confidence"], (int, float)):
        config.min_gesture_confidence = config.debounce.min_confidence

    if isinstance(data.get("one_hand_mode"), bool):
        config.one_hand_mode = data["one_hand_mode"]
    if data.get("dominant_hand") in ("left", "right"):
        config.dominant_hand = (
            Handedness.LEFT if data["dominant_hand"] == "left" else Handedness.RIGHT
        )
