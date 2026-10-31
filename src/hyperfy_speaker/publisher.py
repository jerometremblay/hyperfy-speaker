from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import struct
import time
from pathlib import Path
from queue import Empty

from .airplay import AIRPLAY_NAME, AirPlayInput
from .audio import CHANNELS, FRAMES, SAMPLE_RATE, AudioBuffer, find_device
from .config import Config
from .world import WorldClient, WorldError


def status(state: str, **fields) -> None:
    print(json.dumps({"state": state, **fields}), flush=True)


def configure_livekit() -> None:
    # Packaged workers already contain this library. Source runs use the local build.
    library = Path(__file__).resolve().parents[2] / ".build/livekit/liblivekit_ffi.dylib"
    if library.is_file():
        os.environ.setdefault("LIVEKIT_LIB_PATH", str(library))


class AudioInputError(ValueError):
    pass


async def publish_session(
    session: dict,
    device: int | None,
    stop: asyncio.Event,
    *,
    tone: bool = False,
    airplay: AirPlayInput | None = None,
) -> None:
    configure_livekit()
    from livekit import rtc

    room = rtc.Room()
    disconnected = asyncio.Event()

    @room.on("disconnected")
    def on_disconnected(*_):
        disconnected.set()

    source = rtc.AudioSource(SAMPLE_RATE, CHANNELS, queue_size_ms=500 if airplay else 200)
    stream = None
    buffer = airplay.buffer if airplay else AudioBuffer()
    try:
        await asyncio.wait_for(
            room.connect(session["wsUrl"], session["token"], rtc.RoomOptions(auto_subscribe=False)),
            timeout=20,
        )
        track = rtc.LocalAudioTrack.create_audio_track("hyperfy-speaker", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        options.audio_encoding.max_bitrate = 128_000
        options.dtx = False
        await room.local_participant.publish_track(track, options)
        if not tone and airplay is None:
            import sounddevice as sd

            def callback(data, frames, timing, flags):
                if frames == FRAMES:
                    buffer.put(bytes(data))

            try:
                stream = sd.RawInputStream(
                    device=device,
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=FRAMES,
                    # Low latency avoids Core Audio batching ~85 ms of input at once.
                    latency="low",
                    callback=callback,
                )
                stream.start()
            except sd.PortAudioError:
                raise AudioInputError(
                    "Could not open the virtual audio input. Check the installed driver "
                    "and allow microphone access for Hyperfy Speaker."
                ) from None
        status(
            "airplay_ready" if airplay else "streaming",
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
            receiver=AIRPLAY_NAME if airplay else None,
        )
        sample_index = 0
        deadline = time.monotonic()
        last_status = deadline
        last_audio = 0.0
        while not stop.is_set() and not disconnected.is_set():
            if airplay:
                airplay.check()
            if tone:
                data = bytearray()
                for index in range(FRAMES):
                    value = int(
                        3000 * math.sin(2 * math.pi * 440 * (sample_index + index) / SAMPLE_RATE)
                    )
                    right = int(
                        math.sin(2 * math.pi * 660 * (sample_index + index) / SAMPLE_RATE) * 3276
                    )
                    data.extend(struct.pack("<hh", value, right))
                sample_index += FRAMES
                deadline += FRAMES / SAMPLE_RATE
                await asyncio.sleep(max(0, deadline - time.monotonic()))
            else:
                try:
                    data = await asyncio.to_thread(buffer.queue.get, True, 0.2)
                except Empty:
                    if airplay and time.monotonic() - last_status >= 5:
                        status(
                            "airplay_ready",
                            received_frames=airplay.received_frames,
                            dropped_frames=buffer.dropped,
                        )
                        last_status = time.monotonic()
                    if stream is not None and not stream.active:
                        raise AudioInputError(
                            "Audio input stopped. Check the driver and microphone permission."
                        )
                    continue
            if airplay and time.monotonic() - last_audio > 1:
                status("streaming", input_mode="airplay")
            last_audio = time.monotonic()
            await source.capture_frame(rtc.AudioFrame(data, SAMPLE_RATE, CHANNELS, FRAMES))
            if time.monotonic() - last_status >= 5:
                fields = {"dropped_frames": buffer.dropped}
                if airplay:
                    fields["received_frames"] = airplay.received_frames
                status("streaming", **fields)
                last_status = time.monotonic()
    finally:
        if stream is not None:
            stream.stop()
            stream.close()
        await room.disconnect()
        await source.aclose()


async def run(config: Config, *, tone: bool = False, duration: float | None = None) -> None:
    if not config.speaker_id:
        raise ValueError("Create a speaker first using Settings or `hyperfy-speaker setup`.")
    client = WorldClient(config)
    config.validate_input()
    airplay = AirPlayInput() if config.input_mode == "airplay" and not tone else None
    device = None if tone or airplay else find_device(config.device)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for value in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(value, stop.set)
    timer = loop.call_later(duration, stop.set) if duration else None
    attempt = 0
    try:
        if airplay:
            await airplay.start()
        while not stop.is_set():
            try:
                status("connecting")
                session = await asyncio.to_thread(client.session, config.speaker_id)
                await publish_session(session, device, stop, tone=tone, airplay=airplay)
                attempt = 0
            except WorldError as error:
                if error.status in (401, 403, 404):
                    raise
                status("reconnecting", error=str(error))
            except (ValueError, OSError):
                raise
            except Exception:
                # SDK exceptions can contain credentials; keep diagnostic output bounded and safe.
                status("reconnecting", error="Audio connection interrupted.")
            if not stop.is_set():
                attempt += 1
                try:
                    await asyncio.wait_for(stop.wait(), timeout=min(2**attempt, 15))
                except TimeoutError:
                    pass
    finally:
        if airplay:
            await airplay.close()
        if timer:
            timer.cancel()
        status("stopped")
