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
