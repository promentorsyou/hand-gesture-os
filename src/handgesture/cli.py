"""Command-line entry points.

    handgesture serve      # run the web UI + gesture server
    handgesture simulate   # run the pipeline on synthetic input, no camera
    handgesture doctor     # report what this machine can actually do
    handgesture bench      # measure the frame path, headlessly
"""

from __future__ import annotations

import argparse
import sys


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed. Run: pip install -e '.[server]'", file=sys.stderr)
        return 1

    from .configuration import load as load_config
    from .osadapter.base import get_adapter
    from .osadapter.null import NullAdapter
    from .server.app import create_app

    config = load_config(args.config, args.profile)
    print(f"Config: {config.source}" + (f" (profile: {config.profile})" if config.profile else ""))
    for problem in config.problems:
        # Loudly, on stderr: a config problem that scrolls past unnoticed is
        # how you end up tuning a file that is not being read.
        print(f"  config problem: {problem}", file=sys.stderr)

    adapter = NullAdapter() if args.simulate else get_adapter()
    print(f"OS adapter: {adapter.name}")
    if isinstance(adapter, NullAdapter) and not args.simulate:
        print(
            "  NOTE: falling back to the null adapter — no real OS control.\n"
            "  Install the platform extra for your OS to enable it."
        )
    print(f"UI: http://{args.host}:{args.port}/")

    uvicorn.run(
        create_app(adapter, config.pipeline),
        host=args.host,
        port=args.port,
        log_level="warning",
    )
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """Measure the frame path. No camera, no display, no OS.

    Worth having as a command rather than a one-off script: it is the only
    honest way to answer "is the Python side fast enough", and the answer
    changes as the pipeline grows.
    """
    import json
    import statistics
    import time

    from .capture.simulation import pose_point
    from .osadapter.null import NullAdapter
    from .server.app import Session

    def payload(hand, timestamp):
        return {
            "timestamp": timestamp,
            "hands": [{
                "handedness": hand.handedness.value,
                "confidence": hand.detection_confidence,
                "landmarks": [{"x": p.x, "y": p.y, "z": p.z} for p in hand.landmarks],
            }],
        }

    hands = [pose_point(center=(0.40 + 0.002 * i, 0.5)) for i in range(100)]
    rows = []

    for label, apps in (
        ("idle", []),
        ("one app", ["music"]),
        ("keyboard focused", ["keyboard"]),
        ("six apps", ["files", "browser", "music", "photos", "settings", "keyboard"]),
    ):
        session = Session(NullAdapter())
        for app in apps:
            session.spatial.workspace.open(app)

        for i in range(50):                       # warm up
            session.handle_frame(payload(hands[i % 100], i * 0.033))

        times, size = [], 0
        for i in range(args.frames):
            frame = payload(hands[i % 100], 100 + i * 0.033)
            start = time.perf_counter()
            out = session.handle_frame(frame)
            times.append(time.perf_counter() - start)
            if i == 0:
                size = len(json.dumps(out))

        times.sort()
        rows.append((label, statistics.mean(times), times[int(len(times) * 0.95)], size))

    print(f"{args.frames} frames per case, {len(hands)} distinct poses\n")
    print(f"{'case':<20} {'mean':>9} {'p95':>9} {'payload':>10} {'at 30fps':>10}")
    for label, mean, p95, size in rows:
        print(
            f"{label:<20} {mean * 1000:>7.3f}ms {p95 * 1000:>7.3f}ms "
            f"{size:>8}B {size * 30 / 1024:>8.1f}KB/s"
        )

    worst = max(r[1] for r in rows)
    print(f"\nBudget at 30fps is 33.3ms/frame; worst case here uses "
          f"{worst * 1000 / 33.3 * 100:.1f}% of it.")
    print("This measures the Python side only. Hand tracking runs in the")
    print("browser and is not included — it is the real frame-rate limit.")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    """Run the full pipeline against synthetic gestures. No camera needed.

    This is the headless proof that the pipeline works: it feeds known poses
    in and prints the gesture events that come out.
    """
    from .capture.simulation import (
        pose_fist,
        pose_open_palm,
        pose_pinch,
        pose_point,
    )
    from .pipeline import GesturePipeline, PipelineConfig
    from .types import Frame, Handedness

    script: list[tuple[str, object, int]] = [
        ("point at the screen", pose_point(center=(0.4, 0.5)), 8),
        ("move right", pose_point(center=(0.6, 0.5)), 8),
        ("pinch to click", pose_pinch(gap=0.08), 6),
        ("release", pose_point(center=(0.6, 0.5)), 6),
        ("make a fist", pose_fist(), 8),
        ("open palm", pose_open_palm(), 8),
        (
            "cross hands (emergency stop)",
            (
                pose_open_palm(center=(0.35, 0.5), handedness=Handedness.LEFT),
                pose_open_palm(center=(0.60, 0.5), handedness=Handedness.RIGHT),
            ),
            8,
        ),
    ]

    pipe = GesturePipeline(PipelineConfig())
    t = 0.0
    dt = 1.0 / 30.0
    total_events = 0

    for label, pose, frames in script:
        print(f"\n\x1b[1m{label}\x1b[0m")
        hands = pose if isinstance(pose, tuple) else (pose,)
        for _ in range(frames):
            state = pipe.process(Frame(hands=hands, timestamp=t))
            for e in state.events:
                total_events += 1
                if e.type.value != "hold" or args.verbose:
                    cursor = ""
                    if state.cursor:
                        cursor = f"  cursor=({state.cursor.x:.0f},{state.cursor.y:.0f})"
                    print(
                        f"  [{t:5.2f}s] {e.type.value:<5} {e.gesture.value:<14}"
                        f" conf={e.confidence:.2f}{cursor}"
                    )
            if state.emergency_stopped:
                print(f"  \x1b[31mEMERGENCY STOP: {state.stop_reason.value}\x1b[0m")
            t += dt

    print(f"\n{total_events} events. Emergency stop engaged: {pipe.emergency_stop.engaged}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report what this machine supports. Honest about what is untested."""
    import platform

    from .osadapter.base import Capability, get_adapter
    from .osadapter.null import NullAdapter

    print(f"Platform : {platform.system()} {platform.release()}")
    print(f"Python   : {platform.python_version()}")

    adapter = get_adapter()
    print(f"Adapter  : {adapter.name}")
    if isinstance(adapter, NullAdapter):
        print("           (simulation only — no real OS control on this host)")

    caps = adapter.capabilities
    print("\nCapabilities:")
    for cap in Capability:
        mark = "yes" if cap in caps else " no"
        print(f"  [{mark}] {cap.value}")

    print("\nOptional dependencies:")
    for module, purpose in [
        ("fastapi", "web server"),
        ("uvicorn", "ASGI server"),
        ("pyautogui", "desktop automation"),
        ("numpy", "numeric helpers"),
        ("yaml", "config files"),
    ]:
        try:
            __import__(module)
            print(f"  [yes] {module:<12} {purpose}")
        except ImportError:
            print(f"  [ no] {module:<12} {purpose}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="handgesture", description="Webcam hand-gesture control system"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the web UI and gesture server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument(
        "--simulate",
        action="store_true",
        help="force the null adapter (no real OS control)",
    )
    p_serve.add_argument("--config", help="path to a gestures.yaml (default: search, then built-in)")
    p_serve.add_argument("--profile", help="a profile name or path to layer on top")
    p_serve.set_defaults(func=cmd_serve)

    p_sim = sub.add_parser("simulate", help="run the pipeline on synthetic gestures")
    p_sim.add_argument("-v", "--verbose", action="store_true", help="include hold events")
    p_sim.set_defaults(func=cmd_simulate)

    p_doc = sub.add_parser("doctor", help="report platform capabilities")
    p_doc.set_defaults(func=cmd_doctor)

    p_bench = sub.add_parser("bench", help="measure the frame path headlessly")
    p_bench.add_argument("--frames", type=int, default=2000)
    p_bench.set_defaults(func=cmd_bench)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
