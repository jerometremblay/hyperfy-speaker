#!/usr/bin/env python3
"""Build an isolated Hyperfy world and verify real LiveKit audio delivery."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from livekit import rtc

from hyperfy_speaker.config import Config
from hyperfy_speaker.world import WorldClient

ROOT = Path(__file__).resolve().parents[1]


def free_port(kind=socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def await_http(url: str, process: subprocess.Popen, *, attempts=100) -> None:
    for _ in range(attempts):
        if process.poll() is not None:
            raise RuntimeError("An isolated test service exited before startup.")
        try:
            with urllib.request.urlopen(url, timeout=0.2):
                return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.1)
    raise RuntimeError("An isolated test service did not start in time.")


async def verify_audio(
    session: dict, environment: dict, worker: list[str], workspace: Path
) -> tuple[int, int]:
    room = rtc.Room()
    received = asyncio.Event()
    count = 0
    peak = 0
    tasks = []

    async def consume(track):
        nonlocal count, peak
        stream = rtc.AudioStream(track, sample_rate=48_000, num_channels=2)
        try:
            async for event in stream:
                count += 1
                peak = max(peak, max(abs(sample) for sample in event.frame.data))
                if count >= 5 and peak > 500:
                    received.set()
                    return
        finally:
            await stream.aclose()

    @room.on("track_subscribed")
    def on_track(track, publication, participant):
        if participant.identity.startswith("speaker:"):
            tasks.append(asyncio.create_task(consume(track)))

    process = None
    try:
        await room.connect(session["wsUrl"], session["token"])
        process = subprocess.Popen(
            [*worker, "run", "--tone", "--duration", "3"],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=(workspace / "worker.log").open("wb"),
        )
        await asyncio.wait_for(received.wait(), timeout=15)
        output, _ = await asyncio.to_thread(process.communicate, timeout=10)
        assert process.returncode == 0, "Publisher worker failed."
        assert any(json.loads(line).get("state") == "streaming" for line in output.splitlines()), (
            "Publisher never reached streaming state."
        )
        return count, peak
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            await asyncio.to_thread(process.wait, timeout=5)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await room.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hyperfy", type=Path, required=True)
    parser.add_argument("--worker", type=Path, help="Test the frozen worker executable instead")
    args = parser.parse_args()
    checkout = args.hyperfy.resolve()
    if not (checkout / "src/core/systems/ClientLiveKit.js").is_file():
        parser.error("Provide a Hyperfy engine checkout with the speaker patch applied.")
    processes = []
    with tempfile.TemporaryDirectory(prefix="hyperfy-speaker-smoke-") as temporary:
        workspace = Path(temporary)
        engine = workspace / "engine"
        engine.mkdir()
        for directory in ("src", "scripts", "hyperfy-skill"):
            shutil.copytree(checkout / directory, engine / directory)
        shutil.copy(checkout / "package.json", engine)
        (engine / "node_modules").symlink_to(checkout / "node_modules", target_is_directory=True)
        (engine / ".env").write_text("")
        environment = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
            if key in os.environ
        }
        # Building the snapshot leaves the user's build and running world untouched.
        with (workspace / "build.log").open("wb") as log:
            subprocess.run(
                ["npm", "run", "build"],
                cwd=engine,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        world_port, rtc_port = free_port(), free_port()
        rtc_tcp, rtc_udp = free_port(), free_port(socket.SOCK_DGRAM)
        livekit = workspace / "livekit.yaml"
        livekit.write_text(
            f"port: {rtc_port}\n"
            "bind_addresses: [127.0.0.1]\n"
            f"rtc:\n  tcp_port: {rtc_tcp}\n  udp_port: {rtc_udp}\n"
            "  use_external_ip: false\n  node_ip: 127.0.0.1\n"
            "keys:\n  devkey: secret\n"
        )
        config_file = workspace / "config.json"
        base = f"http://127.0.0.1:{world_port}"
        environment.update(
            NODE_ENV="test",
            DB_URI="local",
            WORLD="test-world",
            PORT=str(world_port),
            HOST="127.0.0.1",
            DOTENV_FLOW_PATH=str(engine),
            JWT_SECRET="isolated-speaker-smoke-secret",
            ADMIN_CODE="isolated-speaker-admin",
            AGENT_API_WRITE_TOKEN="isolated-speaker-writer",
            SAVE_INTERVAL="0",
            PUBLIC_MAX_UPLOAD_SIZE="1048576",
            PUBLIC_WS_URL=base.replace("http:", "ws:") + "/ws",
            PUBLIC_API_URL=base + "/api",
            ASSETS="local",
            ASSETS_BASE_URL=base + "/assets",
            CLEAN="false",
            LIVEKIT_WS_URL=f"ws://127.0.0.1:{rtc_port}",
            LIVEKIT_API_KEY="devkey",
            LIVEKIT_API_SECRET="secret",
            HYPERFY_SPEAKER_TOKEN="isolated-speaker-writer",
            HYPERFY_SPEAKER_CONFIG=str(config_file),
        )
        try:
            rtc_process = subprocess.Popen(
                ["livekit-server", "--dev", "--config", str(livekit)],
                env=environment,
                stdout=(workspace / "livekit.log").open("wb"),
                stderr=subprocess.STDOUT,
            )
            processes.append(rtc_process)
            await_http(f"http://127.0.0.1:{rtc_port}", rtc_process)
            server = subprocess.Popen(
                ["node", str(engine / "build/index.js")],
                cwd=engine,
                env=environment,
                stdout=(workspace / "world.log").open("wb"),
                stderr=subprocess.STDOUT,
            )
            processes.append(server)
            await_http(base, server)
            config = Config(base)
            client = WorldClient(config, token=environment["HYPERFY_SPEAKER_TOKEN"])
            config.speaker_id = client.create_speaker([0, 0, -2])
            assert client.create_speaker([8, 8, 8]) == config.speaker_id
            config_file.write_text(
                json.dumps(
                    {
                        "world_url": base,
                        "speaker_id": config.speaker_id,
                        "device": "Hyperfy Speaker",
                    }
                )
            )
            # Join a real world session to obtain its standard observer LiveKit token.
            script = """
                import { readPacket } from './src/core/packets.js';
                const ws = new WebSocket(process.env.PUBLIC_WS_URL + '?v=2');
                ws.binaryType = 'arraybuffer';
                ws.addEventListener('message', event => {
                    const [name, data] = readPacket(event.data);
                    if (name === 'onSnapshot') console.log(JSON.stringify({
                        wsUrl: data.livekit.wsUrl, token: data.livekit.token
                    }));
                });
                setTimeout(() => process.exit(1), 30000).unref();
            """
            observer = subprocess.Popen(
                ["node", "--input-type=module", "-e", script],
                cwd=engine,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=(workspace / "observer.log").open("wb"),
            )
            processes.append(observer)
            line = observer.stdout.readline()
            if not line:
                raise RuntimeError("World observer could not obtain its LiveKit session.")
            session = json.loads(line)
            worker = (
                [str(args.worker.resolve())]
                if args.worker
                else [str(ROOT / ".venv/bin/python"), "-m", "hyperfy_speaker.cli"]
            )
            count, peak = asyncio.run(verify_audio(session, environment, worker, workspace))
            print(f"PASS: idempotent world setup; {count} received audio frames; peak {peak}.")
        except Exception:
            # Only isolated, synthetic credentials appear in these test service logs.
            for name in ("build.log", "world.log", "worker.log", "observer.log"):
                path = workspace / name
                if path.exists():
                    tail = path.read_text(errors="replace")[-3000:]
                    if tail:
                        print(name + ":\n" + tail)
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
            for process in reversed(processes):
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
