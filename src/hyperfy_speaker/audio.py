from __future__ import annotations

from queue import Empty, Full, Queue

SAMPLE_RATE = 48_000
CHANNELS = 2
FRAMES = 480


class AudioBuffer:
    """A bounded 200 ms buffer; discard stale audio rather than accumulating delay."""

    def __init__(self, capacity: int = 20):
        self.queue: Queue[bytes] = Queue(maxsize=capacity)
        self.dropped = 0

    def put(self, block: bytes) -> None:
        try:
            self.queue.put_nowait(block)
            return
        except Full:
            self.dropped += 1
        try:
            self.queue.get_nowait()
        except Empty:
            pass
        try:
            self.queue.put_nowait(block)
        except Full:
            self.dropped += 1


def input_devices() -> list[dict]:
    import sounddevice as sd

    return [
        {"id": index, "name": device["name"], "channels": device["max_input_channels"]}
        for index, device in enumerate(sd.query_devices())
        if device["max_input_channels"] >= CHANNELS
    ]


def find_device(name: str, devices: list[dict] | None = None) -> int:
    choices = input_devices() if devices is None else devices
    matches = [device for device in choices if device["name"].casefold() == name.casefold()]
    if len(matches) != 1:
        raise ValueError(
            f'Audio input "{name}" was not uniquely found. Install the driver, '
            "then choose an input in Settings or run `hyperfy-speaker devices`."
        )
    return matches[0]["id"]
