#!/usr/bin/env python3
"""Build the installed official LiveKit SDK with its stereo-advertisement fix."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".build/livekit"


def build_library() -> Path:
    lock_file = ROOT / "macos/livekit.lock.json"
    lock = json.loads(lock_file.read_text())
    if importlib.metadata.version("livekit") != lock["pythonSdkVersion"]:
        raise RuntimeError("The LiveKit Python SDK version does not match its native library lock")
    recipe = hashlib.sha256(Path(__file__).read_bytes() + lock_file.read_bytes()).hexdigest()
    source = BUILD / "source" / ("rust-sdks-" + lock["rustSdkCommit"])
    library = BUILD / "liblivekit_ffi.dylib"
    receipt = BUILD / "recipe.sha256"
    if library.exists() and receipt.exists() and receipt.read_text() == recipe:
        return library
    BUILD.mkdir(parents=True, exist_ok=True)
    archive = BUILD / "rust-sdks.tar.gz"
    if not archive.exists():
        url = "https://codeload.github.com/livekit/rust-sdks/tar.gz/" + lock["rustSdkCommit"]
        archive.write_bytes(urllib.request.urlopen(url, timeout=60).read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != lock["sourceSha256"]:
        raise RuntimeError("LiveKit source checksum mismatch")
    if not source.exists():
        with tarfile.open(archive) as files:
            files.extractall(BUILD / "source", filter="data")
    for module in lock["submodules"]:
        target = source / module["path"]
        if (target / module["probe"]).exists():
            continue
        archive_file = BUILD / (module["name"] + ".tar.gz")
        if not archive_file.exists():
            archive_file.write_bytes(urllib.request.urlopen(module["url"], timeout=60).read())
        if hashlib.sha256(archive_file.read_bytes()).hexdigest() != module["sha256"]:
            raise RuntimeError("LiveKit submodule checksum mismatch: " + module["name"])
        with tarfile.open(archive_file) as files:
            if module["archiveRoot"]:
                files.extractall(BUILD / "submodules", filter="data")
                shutil.copytree(
                    BUILD / "submodules" / module["archiveRoot"], target, dirs_exist_ok=True
                )
            else:
                files.extractall(target, filter="data")
    participant = source / "livekit/src/room/participant/local_participant.rs"
    original = participant.read_text()
    marker = "        if options.preconnect_buffer {"
    fix = """        // Advertise two-channel sources for Opus stereo (upstream PR #1337).
        if let LocalTrack::Audio(audio_track) = &track {
            if audio_track.rtc_source().num_channels() == 2 {
                req.audio_features.push(proto::AudioTrackFeature::TfStereo as i32);
            }
        }

"""
    if "audio_track.rtc_source().num_channels() == 2" not in original:
        if original.count(marker) != 1:
            raise RuntimeError("LiveKit stereo patch context changed")
        participant.write_text(original.replace(marker, fix + marker))
    cargo = shutil.which("cargo") or str(BUILD / "cargo/bin/cargo")
    if not Path(cargo).is_file():
        raise RuntimeError("Building stereo LiveKit requires Rust and Cargo")
    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
        if key in os.environ
    }
    environment.update(MACOSX_DEPLOYMENT_TARGET="14.0", RUSTUP_TOOLCHAIN=lock["rustToolchain"])
    environment["PATH"] = str(Path(cargo).parent) + os.pathsep + environment.get("PATH", os.defpath)
    if Path(cargo) == BUILD / "cargo/bin/cargo":
        environment.update(CARGO_HOME=str(BUILD / "cargo"), RUSTUP_HOME=str(BUILD / "rustup"))
    subprocess.run(
        [cargo, "build", "-p", "livekit-ffi", "--release", "--locked", "--jobs", "6"],
        cwd=source,
        env=environment,
        check=True,
    )
    shutil.copy2(source / "target/release/liblivekit_ffi.dylib", library)
    receipt.write_text(recipe)
    return library


if __name__ == "__main__":
    print(build_library())
