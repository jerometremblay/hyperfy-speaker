from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from dataclasses import asdict

from . import __version__
from .audio import input_devices
from .config import Config
from .publisher import run, status
from .world import WorldClient


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stream a Mac virtual audio output to a speaker object in Hyperfy."
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("devices", help="List stereo-capable audio capture devices")
    commands.add_parser("config", help="Print saved configuration (never credentials)")
    configure = commands.add_parser("configure", help="Save connection settings")
    configure.add_argument("--world", required=True)
    configure.add_argument("--speaker-id", default=None)
    configure.add_argument("--device", default=None)
    setup = commands.add_parser(
        "setup", help="Create a speaker app without overwriting existing objects"
    )
    setup.add_argument("--position", type=float, nargs=3, default=[0.0, 0.0, -2.0])
    publish = commands.add_parser("run", help="Publish the selected virtual audio device")
    publish.add_argument(
        "--tone", action="store_true", help="Publish a generated test tone instead"
    )
    publish.add_argument("--duration", type=float, help="Stop after this number of seconds")
    args = parser.parse_args()
    try:
        config = Config.load()
        if args.command == "devices":
            print(json.dumps(input_devices()))
        elif args.command == "config":
            print(json.dumps(asdict(config)))
        elif args.command == "configure":
            config.world_url = args.world
            if args.speaker_id is not None:
                config.speaker_id = args.speaker_id
            if args.device is not None:
                config.device = args.device
            config.save()
            print(json.dumps(asdict(config)))
        elif args.command == "setup":
            if not all(math.isfinite(value) for value in args.position):
                raise ValueError("Speaker position must contain finite numbers.")
            config.speaker_id = WorldClient(config).create_speaker(args.position)
            config.save()
            print(json.dumps({"speaker_id": config.speaker_id}))
        elif args.command == "run":
            if args.duration is not None and (
                not math.isfinite(args.duration) or args.duration <= 0
            ):
                raise ValueError("Duration must be a positive number.")
            asyncio.run(run(config, tone=args.tone, duration=args.duration))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        message = (
            str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        )
        status("error", error=message)
        return 1


if __name__ == "__main__":
    sys.exit(main())
