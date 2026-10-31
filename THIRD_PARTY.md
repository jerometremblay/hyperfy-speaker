# Third-party software

The virtual driver is built from Existential Audio's BlackHole, pinned by commit
and SHA-256 checksums in `macos/blackhole.lock.json`. Its original copyright
notice is preserved. BlackHole and this project are GPL-3.0-only.

The build changes device and bundle names, selects two channels at 48 kHz, and
assigns an independent Core Audio factory identifier. It does not change
BlackHole's audio processing source.

The helper uses the official LiveKit Python SDK (Apache-2.0) and sounddevice
(MIT), which includes PortAudio. Their licenses are retained by the standalone
worker build. The installer includes this project's corresponding source and
the exact BlackHole source used to build its driver.

AirPlay input uses Shairport Sync (MIT), pinned with SHA-256 in
`macos/airplay.lock.json`. The bundled receiver uses FFmpeg's maintained ALAC
decoder and resampler (LGPL-2.1-or-later), OpenSSL (Apache-2.0), libconfig
(LGPL-2.1-or-later), and popt (MIT). Its only audio backend is stdout and its
Bonjour backend is Apple's system DNS-SD library. It is a classic AirPlay build.
These libraries are built statically for macOS 14 or later. The app includes their
license notices; the installer includes all original source archives, the pinned
manifest, and the complete build recipe. No upstream processing code is modified.

## LiveKit stereo support

The Python RTC SDK is still the official LiveKit 1.2.0 SDK. Its native library is
built from the exact Rust SDK commit used by that release, checksum-pinned in
`macos/livekit.lock.json`. `scripts/build_livekit.py` adds the upstream stereo
advertisement fix described in LiveKit rust-sdks PR #1337: a two-channel native
audio source publishes `TF_STEREO`, allowing the server to negotiate Opus stereo.
No streaming protocol, track count, or Python FFI interface changes. The SDK
and WebRTC retain their existing Apache-2.0/BSD notices, included in the app; the
installer includes the original SDK source and the patch recipe.
