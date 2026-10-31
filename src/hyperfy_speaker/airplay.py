from __future__ import annotations

import asyncio
import os
import socket
import tempfile
from pathlib import Path

from .audio import CHANNELS, FRAMES, SAMPLE_RATE, AudioBuffer

AIRPLAY_NAME = "Hyperfy Speaker"
AIRPLAY_PORT = 7001
RECEIVER_CONFIG = f"""
general = {{
    name = "{AIRPLAY_NAME}";
    port = {AIRPLAY_PORT};
    output_backend = "stdout";
    mdns_backend = "dns-sd";
    audio_decoded_buffer_desired_length_in_seconds = 1.0;
}};
sessioncontrol = {{
    allow_session_interruption = "yes";
}};
stdout = {{
    output_rate = {SAMPLE_RATE};
    output_format = "S16_LE";
    output_channels = {CHANNELS};
}};
"""


def receiver_binary() -> Path:
    override = os.environ.get("HYPERFY_SPEAKER_AIRPLAY_BINARY")
    if override:
        path = Path(override)
    else:
        path = Path(__file__).resolve().parents[2] / ".build/airplay/shairport-sync"
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("AirPlay receiver is missing. Install the updated Hyperfy Speaker app.")
    return path


class AirPlayInput:
    """One receiver per worker, kept alive across LiveKit reconnects."""

    def __init__(self):
        # Up to two seconds of scheduling headroom; network buffering is in Shairport Sync.
        self.buffer = AudioBuffer(capacity=200)
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.directory: tempfile.TemporaryDirectory | None = None
        self.received_frames = 0
        self.error: str | None = None

    async def start(self) -> None:
        binary = receiver_binary()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("", AIRPLAY_PORT))
        except OSError:
            raise ValueError(
                f"AirPlay port {AIRPLAY_PORT} is busy. Stop the other Hyperfy Speaker instance."
            ) from None
        self.directory = tempfile.TemporaryDirectory(prefix="hyperfy-airplay-")
        config = Path(self.directory.name) / "receiver.conf"
        config.write_text(RECEIVER_CONFIG)
        try:
            self.process = await asyncio.create_subprocess_exec(
                str(binary),
                "-c",
                str(config),
                "--use-stderr",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                # The network receiver has no need for the world's credentials.
                env={"PATH": os.defpath, "HOME": str(Path.home())},
            )
            self.reader = asyncio.create_task(self._read())
            await asyncio.sleep(0.5)
            self.check()
        except BaseException:
            await self.close()
            raise

    async def _read(self) -> None:
        try:
            while True:
                block = await self.process.stdout.readexactly(FRAMES * CHANNELS * 2)
                self.received_frames += 1
                self.buffer.put(block)
        except asyncio.IncompleteReadError:
            self.error = "AirPlay receiver stopped. Restart streaming and check the local network."
        except OSError:
            self.error = "Could not read audio from the AirPlay receiver."

    def check(self) -> None:
        if self.error or (self.process and self.process.returncode is not None):
            raise ValueError(self.error or "AirPlay receiver could not start.")

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.directory:
            self.directory.cleanup()
