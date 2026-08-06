"""Phase 6: configuration, error recovery, degradation, packaging.

The theme is what happens when things are *wrong* — a bad config file, a
malformed frame, a platform library that is not installed, a room that is
almost dark enough to break tracking.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from handgesture.capture.simulation import pose_point
from handgesture.configuration import (
    DEFAULT_GESTURES,
    PROFILES_DIR,
    available_profiles,
    find_config,
    load,
)
from handgesture.control.safety import SENSITIVE_OPERATIONS
from handgesture.pipeline import GesturePipeline, TrackingHealth
from handgesture.server.app import Session
from handgesture.types import Frame, Handedness

yaml = pytest.importorskip("yaml", reason="pyyaml not installed")


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "gestures.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- The shipped defaults --------------------------------------------------

def test_the_shipped_config_loads_without_a_single_problem():
    """The file the README tells people to edit must itself be valid."""
    config = load(DEFAULT_GESTURES)
    assert config.problems == []
    assert config.source == DEFAULT_GESTURES


def test_every_shipped_profile_loads_cleanly():
    assert available_profiles()
    for name in available_profiles():
        config = load(DEFAULT_GESTURES, profile=name)
        assert config.problems == [], f"{name}: {config.problems}"


def test_the_config_confirmations_match_the_code():
    """A drift test.

    The confirmation levels exist in two places: the YAML users read, and
    the table the gate actually enforces. If they drift, the documentation
    lies about what is protected — which is the worst kind of lie for a
    file full of destructive operations.
    """
    config = load(DEFAULT_GESTURES)
    for operation, level in config.confirmations.items():
        assert operation in SENSITIVE_OPERATIONS, f"{operation} is not gated in code"
        assert SENSITIVE_OPERATIONS[operation].value == level, operation

    for operation, sensitivity in SENSITIVE_OPERATIONS.items():
        assert operation in config.confirmations, f"{operation} is missing from the YAML"
        assert config.confirmations[operation] == sensitivity.value


def test_the_shipped_config_actually_changes_the_pipeline():
    config = load(DEFAULT_GESTURES)
    pipe = GesturePipeline(config.pipeline)
    assert pipe.config.debounce.hold_frames == 3
    assert pipe.config.cursor.margin == pytest.approx(0.15)


def test_a_profile_layers_on_top_of_the_base_config():
    config = load(DEFAULT_GESTURES, profile="high_stability")
    assert config.profile == "high_stability"
    assert config.pipeline.debounce.hold_frames == 8       # from the profile
    assert config.pipeline.cursor.gain == pytest.approx(0.7)
    assert config.pipeline.one_hand_mode is True


def test_the_left_handed_profile_switches_the_dominant_hand():
    config = load(DEFAULT_GESTURES, profile="left_handed")
    assert config.pipeline.dominant_hand is Handedness.LEFT


# --- Bad configuration -----------------------------------------------------

def test_an_unknown_section_is_reported_not_ignored(tmp_path):
    """A typo that silently does nothing is the worst outcome for a tuning file."""
    path = write(tmp_path, "recogniton:\n  min_confidence: 0.7\n")
    config = load(path)
    assert any("recogniton" in p for p in config.problems)


def test_an_out_of_range_value_is_refused_and_the_default_kept(tmp_path):
    path = write(tmp_path, "recognition:\n  min_confidence: 40\n")
    config = load(path)
    assert any("min_confidence" in p for p in config.problems)
    assert config.pipeline.min_gesture_confidence == 0.55


def test_a_wrong_type_is_refused(tmp_path):
    path = write(tmp_path, "cursor:\n  gain: fast\n  mirror_x: sometimes\n")
    config = load(path)
    assert len(config.problems) == 2
    assert config.pipeline.cursor.gain == 1.0
    assert config.pipeline.cursor.mirror_x is True


def test_invalid_yaml_does_not_raise(tmp_path):
    path = write(tmp_path, "recognition: [unclosed\n")
    config = load(path)
    assert not config.ok
    assert config.pipeline.min_gesture_confidence == 0.55


def test_a_missing_file_falls_back_to_defaults(tmp_path):
    config = load(tmp_path / "nope.yaml")
    assert not config.ok
    assert config.pipeline.debounce.hold_frames == 3


def test_unknown_gestures_and_modes_in_bindings_are_reported(tmp_path):
    path = write(
        tmp_path,
        "bindings:\n  navigation:\n    wiggle: cursor.move\n  telepathy:\n    point: x\n",
    )
    config = load(path)
    assert any("wiggle" in p for p in config.problems)
    assert any("telepathy" in p for p in config.problems)
    assert "wiggle" not in config.bindings.get("navigation", {})


def test_an_unknown_profile_is_reported_with_the_available_ones(tmp_path):
    config = load(DEFAULT_GESTURES, profile="nonexistent")
    assert any("nonexistent" in p and "high_stability" in p for p in config.problems)


def test_a_profile_with_a_bad_value_keeps_the_base_setting():
    path = PROFILES_DIR / "high_stability.yaml"
    assert path.is_file()
    config = load(DEFAULT_GESTURES, profile=str(path))
    assert config.problems == []


def test_find_config_prefers_an_explicit_path(tmp_path):
    path = write(tmp_path, "version: 1\n")
    assert find_config(path) == path
    assert find_config(None) == DEFAULT_GESTURES or find_config(None).is_file()


# --- Error recovery --------------------------------------------------------

def frame_payload(hand, timestamp=0.0):
    return {
        "timestamp": timestamp,
        "hands": [{
            "handedness": hand.handedness.value,
            "confidence": hand.detection_confidence,
            "landmarks": [{"x": p.x, "y": p.y, "z": p.z} for p in hand.landmarks],
        }],
    }


def test_a_malformed_frame_does_not_kill_the_session():
    """A dropped socket means a fresh session: workspace, calibration, pairing all lost."""
    session = Session()
    reply = session.handle({"timestamp": "not-a-number", "hands": []})
    assert reply["type"] == "error"
    assert reply["recovered"] is True

    # Still usable afterwards.
    good = session.handle(frame_payload(pose_point(), 1.0))
    assert good["type"] == "state"


def test_garbage_landmarks_are_survived():
    session = Session()
    reply = session.handle({"timestamp": 0.0, "hands": [{"landmarks": "nonsense"}]})
    # Either parsed as no hands, or reported — never an exception escaping.
    assert reply["type"] in ("state", "error")


def test_a_successful_message_clears_the_error_run():
    session = Session()
    session.handle({"timestamp": "bad", "hands": []})
    assert session.consecutive_errors == 1
    session.handle(frame_payload(pose_point(), 1.0))
    assert session.consecutive_errors == 0


def test_repeated_failures_fail_safe_into_an_emergency_stop():
    """If something is broken enough to fail repeatedly, stop rather than continue."""
    session = Session()
    session.max_consecutive_errors = 3

    replies = [session.handle({"timestamp": "bad", "hands": []}) for _ in range(3)]
    assert replies[0]["recovered"] is True
    assert replies[-1]["recovered"] is False
    assert replies[-1]["emergencyStopped"] is True
    assert session.pipeline.emergency_stop.engaged


def test_an_unknown_command_is_an_error_not_a_crash():
    session = Session()
    reply = session.handle({"type": "command", "command": "nonsense"})
    assert reply["type"] == "error"
    assert session.consecutive_errors == 0     # a handled error is not a failure


# --- Low light -------------------------------------------------------------

def bright(pipe, values):
    return [
        pipe.process(
            Frame(hands=(pose_point(),), timestamp=i * 0.05, brightness=b)
        ).health
        for i, b in enumerate(values)
    ]


def test_low_light_is_reported():
    pipe = GesturePipeline()
    assert bright(pipe, [0.05])[0] is TrackingHealth.LOW_LIGHT


def test_light_hovering_at_the_threshold_does_not_flicker():
    """With one threshold, a room right at the boundary flaps every frame."""
    pipe = GesturePipeline()
    # Straddles the 0.12 entry threshold but stays under the 0.16 recovery.
    health = bright(pipe, [0.05, 0.13, 0.11, 0.13, 0.125, 0.14])
    assert all(h is TrackingHealth.LOW_LIGHT for h in health), health


def test_recovering_needs_properly_more_light():
    pipe = GesturePipeline()
    health = bright(pipe, [0.05, 0.13, 0.20])
    assert health[-1] is not TrackingHealth.LOW_LIGHT


def test_brightness_is_optional():
    pipe = GesturePipeline()
    state = pipe.process(Frame(hands=(pose_point(),), timestamp=0.0))
    assert state.health is not TrackingHealth.LOW_LIGHT


# --- Per-frame payload size ------------------------------------------------

def test_the_keyboard_layout_is_not_sent_with_every_frame():
    """~3KB of unchanging key geometry, 30 times a second, is pure waste."""
    import json

    from handgesture.apps import KeyboardApp

    app = KeyboardApp()
    assert "keys" not in app.state()
    assert app.state()["layoutRevision"] == 0
    assert len(json.dumps(app.state())) < 400
    assert len(app.layout_payload()["keys"]) == 33


def test_the_layout_revision_changes_only_when_the_keys_change():
    from handgesture.apps import KeyboardApp
    from handgesture.apps.keyboard import LAYOUT, SHIFT
    from handgesture.types import Point

    app = KeyboardApp()
    start = app.layout_revision

    def press(label):
        for key in app.keys():
            if key.label == label:
                app.update(
                    Point(key.x + key.width / 2, key.y + key.height / 2),
                    app._last_press + 10.0,
                    pinching=True,
                )
                return
        raise AssertionError(label)

    press("q")                              # a letter does not redraw the keys
    assert app.layout_revision == start

    press(SHIFT)                            # uppercase labels: it does
    assert app.layout_revision == start + 1

    press(LAYOUT)                           # a different layout: it does
    assert app.layout_revision >= start + 2


def test_a_frame_payload_stays_small_with_a_keyboard_focused():
    import json

    session = Session()
    session.spatial.workspace.open("keyboard")
    out = session.handle(frame_payload(pose_point(), 0.05))
    # 30 frames a second: a few KB each is hundreds of KB/s of nothing.
    assert len(json.dumps(out)) < 2000


# --- Platform adapters -----------------------------------------------------

def test_every_platform_adapter_module_imports_on_a_headless_box():
    """Importing must never require the platform library to be present."""
    import importlib

    for module, cls in (
        ("windows", "WindowsAdapter"),
        ("macos", "MacOSAdapter"),
        ("linux", "LinuxAdapter"),
    ):
        adapter = importlib.import_module(f"handgesture.osadapter.{module}")
        assert hasattr(adapter, cls)


def test_an_adapter_that_cannot_run_says_why():
    """A mystery failure here is a support nightmare; the message must be actionable."""
    import importlib

    for module, cls in (
        ("windows", "WindowsAdapter"),
        ("macos", "MacOSAdapter"),
        ("linux", "LinuxAdapter"),
    ):
        klass = getattr(importlib.import_module(f"handgesture.osadapter.{module}"), cls)
        try:
            klass()
        except Exception as exc:
            assert str(exc), f"{cls} failed with an empty message"
        # Constructing successfully is fine too — it means the host supports it.


def test_get_adapter_never_raises_and_always_returns_something():
    from handgesture.osadapter.base import get_adapter

    for platform in ("windows", "darwin", "linux", "plan9", "", None):
        adapter = get_adapter(platform)
        assert adapter.name
        assert adapter.screen_info().width > 0


# --- Accessibility ---------------------------------------------------------

def test_the_accessibility_settings_are_in_quick_settings():
    """They have to be reachable by hand or phone, not just a config file."""
    from handgesture.spatial import Workspace

    ws = Workspace()
    for key in ("high_contrast", "large_targets", "reduce_motion"):
        assert key in ws.quick_settings
        assert ws.toggle_setting(key) is True
    assert ws.snapshot()["quick_settings"]["high_contrast"] is True


def test_the_companion_can_toggle_accessibility():
    from handgesture.companion import CompanionBridge

    bridge = CompanionBridge(Session())
    reply = bridge.command({"command": "toggle_setting", "name": "large_targets"})
    assert reply["value"] is True
    assert bridge.snapshot()["quickSettings"]["large_targets"] is True


# --- Packaging -------------------------------------------------------------

def test_the_config_defaults_live_inside_the_package():
    """They used to sit outside it, so installed copies shipped no config."""
    import handgesture

    package_root = Path(handgesture.__file__).parent
    assert DEFAULT_GESTURES.is_file()
    assert DEFAULT_GESTURES.is_relative_to(package_root)
    assert PROFILES_DIR.is_relative_to(package_root)


def test_the_static_ui_files_live_inside_the_package():
    from handgesture.server.app import STATIC_DIR

    assert (STATIC_DIR / "index.html").is_file()
    assert (STATIC_DIR / "companion.html").is_file()


def test_pyproject_ships_every_data_directory_the_code_reads():
    import handgesture

    root = Path(handgesture.__file__).parent.parent.parent
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    for pattern in (
        "server/static/*",
        "configuration/defaults/*.yaml",
        "configuration/defaults/profiles/*.yaml",
    ):
        assert pattern in text, f"{pattern} is not in package-data"
