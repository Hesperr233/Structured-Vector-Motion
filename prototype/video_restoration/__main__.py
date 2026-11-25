"""External controlled video repair. Development fixtures have a separate entry point."""

import argparse
import sys
from pathlib import Path

from . import formal_pipeline as pipeline
from .formal_bundle import StageError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("--source", required=True, type=Path)
    ingest.add_argument("--output", required=True, type=Path)
    ingest.add_argument("--decoder-profile")
    ingest.add_argument("--decoder", default="ffmpeg")
    ingest.add_argument("--ffprobe", default="ffprobe")
    ingest.add_argument("--drop-audio", action="store_true")
    repair = commands.add_parser("repair")
    for name in ("input", "masks", "config", "output"):
        repair.add_argument(f"--{name}", required=True, type=Path)
    for name in ("export", "evaluate", "verify"):
        stage = commands.add_parser(name)
        stage.add_argument("--run", required=True, type=Path)
        stage.add_argument(
            "--diagnostic", action="store_true", help="retain evidence of an incomplete run"
        )
        if name in ("export", "verify"):
            stage.add_argument("--encoder", default="ffmpeg")
            stage.add_argument("--ffprobe", default="ffprobe")
        if name in ("evaluate", "verify"):
            stage.add_argument("--reference", type=Path, required=True)
            stage.add_argument("--output", type=Path, required=True)
        if name == "verify":
            stage.add_argument("--contract", required=True)
            stage.add_argument("--expected-code-sha", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "ingest":
            return pipeline.ingest(args)
        if args.command == "repair":
            return pipeline.repair(args)
        return pipeline.stage(args)
    except (StageError, ValueError, KeyError, TypeError, OSError) as exc:
        code = getattr(exc, "exit_code", 8)
        label = getattr(exc, "code", "CVR-E030")
        print(f"{args.command}: {label} {exc}", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
