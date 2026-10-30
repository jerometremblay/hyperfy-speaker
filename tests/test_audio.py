from hyperfy_speaker.audio import AudioBuffer, find_device


def test_overload_discards_oldest_audio_instead_of_increasing_latency():
    buffer = AudioBuffer(capacity=2)
    for block in (b"old", b"recent", b"newest"):
        buffer.put(block)
    assert buffer.dropped == 1
    assert buffer.queue.get_nowait() == b"recent"
    assert buffer.queue.get_nowait() == b"newest"


def test_never_falls_back_to_a_physical_microphone():
    import pytest

    devices = [{"id": 0, "name": "MacBook Microphone", "channels": 2}]
    with pytest.raises(ValueError, match="not uniquely found"):
        find_device("Hyperfy Speaker", devices)
    devices.append({"id": 4, "name": "Hyperfy Speaker", "channels": 2})
    assert find_device("hyperfy speaker", devices) == 4
    devices.append({"id": 5, "name": "Hyperfy Speaker", "channels": 2})
    with pytest.raises(ValueError, match="not uniquely found"):
        find_device("Hyperfy Speaker", devices)


def test_publisher_avoids_core_audio_bursts_larger_than_capture_buffer(monkeypatch, capsys):
    """The device's high-latency callback delivers 9 blocks at once; only 6 fit."""
    import asyncio
    import json
    import sys
    from types import SimpleNamespace

    from hyperfy_speaker import publisher
    from hyperfy_speaker.audio import CHANNELS, FRAMES
    from hyperfy_speaker.publisher import publish_session

    # Exercise the original 60 ms limit independently of the larger production buffer.
    monkeypatch.setattr(publisher, "AudioBuffer", lambda: AudioBuffer(capacity=6))
    stop = asyncio.Event()
    delivered = []
    block = bytes(FRAMES * CHANNELS * 2)

    class InputStream:
        def __init__(self, **options):
            self.callback = options["callback"]
            self.burst = 1 if options.get("latency") == "low" else 9
            self.remaining = 9
            self.active = False

        def feed(self):
            for _ in range(min(self.burst, self.remaining)):
                self.remaining -= 1
                self.callback(block, FRAMES, None, False)

        def start(self):
            self.active = True
            self.feed()

        def stop(self):
            self.active = False

        def close(self):
            pass

    stream = None

    def create_stream(**options):
        nonlocal stream
        stream = InputStream(**options)
        return stream

    class AudioSource:
        def __init__(self, *_args, **_options):
            pass

        async def capture_frame(self, frame):
            delivered.append(frame)
            if stream.remaining:
                stream.feed()
            elif len(delivered) >= (9 if stream.burst == 1 else 6):
                stop.set()

        async def aclose(self):
            pass

    class Room:
        def __init__(self):
            self.local_participant = self

        def on(self, *_args):
            return lambda handler: handler

        async def connect(self, *_args):
            pass

        async def publish_track(self, *_args):
            pass

        async def disconnect(self):
            pass

    rtc = SimpleNamespace(
        Room=Room,
        RoomOptions=lambda **_options: None,
        AudioSource=AudioSource,
        LocalAudioTrack=SimpleNamespace(create_audio_track=lambda *_args: None),
        TrackSource=SimpleNamespace(SOURCE_MICROPHONE=1),
        TrackPublishOptions=lambda **_options: SimpleNamespace(
            audio_encoding=SimpleNamespace(max_bitrate=0), dtx=True
        ),
        AudioFrame=lambda data, *_args: data,
    )
    monkeypatch.setitem(sys.modules, "livekit", SimpleNamespace(rtc=rtc))
    monkeypatch.setitem(
        sys.modules, "sounddevice",
        SimpleNamespace(RawInputStream=create_stream, PortAudioError=RuntimeError),
    )
    asyncio.run(publish_session({"wsUrl": "test", "token": "test"}, 0, stop))
    assert len(delivered) == 9
    assert all(frame == block for frame in delivered)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0]["state"] == "streaming"


def test_capture_buffer_preserves_a_short_scheduling_burst():
    buffer = AudioBuffer()
    blocks = [bytes([index]) for index in range(12)]
    for block in blocks:
        buffer.put(block)
    assert buffer.dropped == 0
    assert [buffer.queue.get_nowait() for _ in blocks] == blocks
