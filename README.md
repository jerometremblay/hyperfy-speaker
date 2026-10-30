# Hyperfy Speaker

A selectable macOS sound output that streams to a movable, spatial speaker in
Hyperfy. The Mac output is named **Hyperfy Speaker**. People near the in-world
speaker hear the stream through their normal Music volume control.

This is a virtual Core Audio device. Phone Bluetooth reception is a separate
feature and is not included.

## Use the Mac app

1. Install `dist/HyperfySpeaker-0.1.0-arm64.pkg` (the build names the current Mac
   architecture). The installer adds the driver and `/Applications/Hyperfy Speaker.app`.
   Installing the driver restarts Core Audio and briefly interrupts current audio.
2. Open **Hyperfy Speaker**. Its streaming status and controls appear in a window
   and in the menu bar. With saved world and speaker settings, streaming starts
   automatically when the app opens.
3. In **Settings**, enter the world URL and that world's **Agent API write token**.
   The app stores the token in macOS Keychain. Save the settings.
4. Choose **Create Speaker in World**, or enter an existing app instance ID.
   Setup creates one stable app and instance; retries preserve its placement.
5. In Hyperfy, open **Preferences → Audio → Audio output** and choose your
   headphones or physical speakers. Chrome/Chromium supports this playback selector.
   Keep the browser on that physical output when changing the Mac's system output.
6. Choose **Hyperfy Speaker** in macOS **System Settings → Sound → Output**, or
   select it as the output of your music application. Keep the helper open and
   check that it says **Streaming to Hyperfy**. If stopped, choose **Start Streaming**.
7. Move the in-world speaker with Hyperfy's existing editor. Its sound follows
   that instance and fades out at 40 meters.

Selecting a separate Hyperfy playback device keeps the received stream from
feeding back into the virtual output. The helper never changes your Mac's
default output automatically. Stop streaming before changing connection settings.

The world needs the included engine integration and a configured LiveKit
service. Ordinary voice chat remains separate. The helper only publishes audio;
it does not subscribe to the room.

## Integrate with Hyperfy

```sh
python3 hyperfy/apply.py /path/to/hyperfy
cd /path/to/hyperfy
npm run build
npm start
```

The patch adds:

- `POST /api/agent/entities/:id/speaker-session`, protected by the existing
  Agent API **write** token.
- A 15-minute, audio-only publisher token for `speaker:<instance-id>`. It
  grants no room subscription, data publication, or metadata updates.
- Positional playback through the Music group. Deleted, disabled, crashed, or
  rebuilding apps are silent. Late joiners receive the active track.
  The receiver uses LiveKit’s normal playback attachment to start browser
  audio playout, with the SDK output muted so the spatial path plays it once.
- A physical playback output selector for browsers supporting `AudioContext.setSinkId`.

Configure `LIVEKIT_WS_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, and
`AGENT_API_WRITE_TOKEN` on the world server. Use your existing LiveKit service
when voice chat already works. LiveKit transports audio; the Mac helper captures
it. Remote worlds require HTTPS and a `wss://` LiveKit endpoint.

For an isolated local test, LiveKit's development server uses
`ws://localhost:7880`, key `devkey`, and secret `secret`:

```sh
livekit-server --dev --bind 127.0.0.1
```

Use production credentials and TLS for a public service.

## Develop and build

Requires macOS 14+, Apple's Command Line Tools, Python 3.11–3.14, and `uv`.
Full Xcode is not required. The driver builds for both Intel and Apple Silicon;
the bundled Python worker targets the Mac architecture used for the build.

```sh
uv sync --all-groups --managed-python
uv run pytest
uv run ruff check .
uv run python scripts/build_macos.py
```

Run the complete transport smoke test with an installed `livekit-server`:

```sh
uv run python scripts/smoke.py --hyperfy /path/to/patched/hyperfy \
  --worker "dist/Hyperfy Speaker.app/Contents/Resources/speaker-worker/speaker-worker"
```

It builds a temporary engine snapshot, creates an isolated world and LiveKit
service, and verifies decoded test-tone frames using the frozen worker. It
does not capture your microphone, change your audio output, or modify a running
world.

The build downloads BlackHole source pinned by commit and SHA-256, builds the
branded two-channel 48 kHz driver, freezes the worker, compiles the native menu
bar app, and packages everything. It includes corresponding source and licenses.
Generated files stay under `.build/` and `dist/`.

The default build uses local ad hoc signatures. For a distributable, notarized
installer, supply your own Apple signing identities and saved notarytool profile:

```sh
uv run python scripts/build_macos.py \
  --sign "Developer ID Application: Your Name (TEAM)" \
  --installer-sign "Developer ID Installer: Your Name (TEAM)" \
  --notary-profile your-profile
```

## Command-line helper

For development, use the existing BlackHole device instead of installing the
branded driver:

```sh
uv run hyperfy-speaker devices
uv run hyperfy-speaker configure \
  --world https://your-world.example --device "BlackHole 2ch"
# Provide the world write token through HYPERFY_SPEAKER_TOKEN, or save it in the Mac app.
uv run hyperfy-speaker setup --position 0 0 -2
uv run hyperfy-speaker run
```

Configuration lives at
`~/Library/Application Support/Hyperfy Speaker/config.json` and contains no token.
`HYPERFY_SPEAKER_CONFIG` selects an isolated configuration file.

`hyperfy-speaker run --tone --duration 5` sends a generated 440 Hz test tone
without capturing a microphone or requiring the virtual driver. JSON status
lines report connection state and dropped frames, never audio samples or tokens.
Capture requests the device's low-latency mode to avoid large Core Audio batches.
Both capture and SDK buffers are bounded at 200 ms to absorb scheduling pauses.
The capture buffer discards old audio under sustained load instead of accumulating
unbounded latency.

## Remove

Quit the helper. Switch the Mac sound output back to a physical device. Remove
`/Applications/Hyperfy Speaker.app` and
`/Library/Audio/Plug-Ins/HAL/HyperfySpeaker.driver`, then restart Core Audio or
restart the Mac. The helper's settings and Keychain token are retained until
you remove them yourself.

## License

GPL-3.0-only, including the customized BlackHole driver. See `LICENSE`,
`THIRD_PARTY.md`, and `macos/blackhole.lock.json`.
