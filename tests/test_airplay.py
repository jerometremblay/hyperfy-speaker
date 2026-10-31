import asyncio
import socket
from pathlib import Path

import pytest

from hyperfy_speaker import airplay
from hyperfy_speaker.audio import CHANNELS, FRAMES
from hyperfy_speaker.config import Config


def test_old_configuration_keeps_virtual_output_and_airplay_roundtrips(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("HYPERFY_SPEAKER_CONFIG", str(path))
    path.write_text('{"world_url": "https://world.example", "speaker_id": "speaker"}')
    config = Config.load()
    assert config.input_mode == "device"
    config.input_mode = "airplay"
    config.save()
    assert Config.load().input_mode == "airplay"
    config.input_mode = "microphone"
    with pytest.raises(ValueError, match="Audio input"):
        config.save()


def fake_receiver(tmp_path, monkeypatch, body):
    receiver = tmp_path / "receiver"
    receiver.write_text("#!/usr/bin/python3\nimport os, sys, time\n" + body)
    receiver.chmod(0o755)
    monkeypatch.setenv("HYPERFY_SPEAKER_AIRPLAY_BINARY", str(receiver))
    monkeypatch.setenv("HYPERFY_SPEAKER_TOKEN", "must-not-reach-receiver")
    # Let the kernel choose the probe port; only the collision test reserves one.
    monkeypatch.setattr(airplay, "AIRPLAY_PORT", 0)
    return receiver


def test_fragmented_pcm_reads_preserve_every_frame_and_child_stops(tmp_path, monkeypatch):
    size = FRAMES * CHANNELS * 2
    fake_receiver(
        tmp_path,
        monkeypatch,
        f"""
assert 'HYPERFY_SPEAKER_TOKEN' not in os.environ
config = open(sys.argv[2]).read()
assert 'output_rate = 48000' in config and 'output_format = "S16_LE"' in config
payload = (bytes(range(256)) * ({size} * 3 // 256 + 1))[:{size} * 3] + b'partial'
os.write(1, payload[:71])
os.write(1, payload[71:])
time.sleep(60)
""",
    )

    async def scenario():
        receiver = airplay.AirPlayInput()
        await receiver.start()
        directory = Path(receiver.directory.name)
        try:
            blocks = [receiver.buffer.queue.get_nowait() for _ in range(3)]
            assert b"".join(blocks) == (bytes(range(256)) * (size * 3 // 256 + 1))[: size * 3]
            assert receiver.received_frames == 3
            assert receiver.buffer.dropped == 0
            assert receiver.buffer.queue.empty()
        finally:
            await receiver.close()
        assert receiver.process.returncode is not None
        assert not directory.exists()

    asyncio.run(scenario())


def test_receiver_startup_failure_is_visible_and_cleaned_up(tmp_path, monkeypatch):
    fake_receiver(tmp_path, monkeypatch, "sys.exit(42)\n")

    async def scenario():
        receiver = airplay.AirPlayInput()
        with pytest.raises(ValueError, match="receiver stopped|could not start"):
            await receiver.start()
        assert receiver.process.returncode == 42
        assert not Path(receiver.directory.name).exists()

    asyncio.run(scenario())


def test_busy_receiver_port_does_not_start_a_second_child(tmp_path, monkeypatch):
    fake_receiver(tmp_path, monkeypatch, "time.sleep(60)\n")

    async def scenario():
        receiver = airplay.AirPlayInput()
        with socket.socket() as busy:
            busy.bind(("", airplay.AIRPLAY_PORT))
            busy.listen()
            monkeypatch.setattr(airplay, "AIRPLAY_PORT", busy.getsockname()[1])
            with pytest.raises(ValueError, match="port.*busy"):
                await receiver.start()
        assert receiver.process is None

    asyncio.run(scenario())


def test_airplay_buffer_tolerates_a_one_second_burst():
    buffer = airplay.AirPlayInput().buffer
    for index in range(100):
        buffer.put(bytes([index]))
    assert buffer.dropped == 0
    assert [buffer.queue.get_nowait() for _ in range(100)] == [bytes([n]) for n in range(100)]
