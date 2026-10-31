#!/usr/bin/env python3
"""Build a standalone classic AirPlay receiver; all dependencies are pinned and static."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".build/airplay"
PREFIX = BUILD / "install"
BINARY = BUILD / "shairport-sync"


def command(*args: str, cwd: Path, env: dict) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True)


def sources() -> dict[str, Path]:
    paths = {}
    for name, entry in json.loads((ROOT / "macos/airplay.lock.json").read_text()).items():
        archive = BUILD / Path(entry["url"]).name
        if not archive.exists():
            archive.write_bytes(urllib.request.urlopen(entry["url"], timeout=90).read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError(f"AirPlay dependency checksum mismatch: {name}")
        folder = BUILD / "sources" / name
        if not folder.exists():
            folder.mkdir(parents=True)
            with tarfile.open(archive) as source:
                source.extractall(folder, filter="data")
        paths[name] = next(item for item in folder.iterdir() if item.is_dir())
    return paths


def build_receiver() -> Path:
    receipt = BUILD / "recipe.sha256"
    recipe_hash = hashlib.sha256(
        Path(__file__).read_bytes() + (ROOT / "macos/airplay.lock.json").read_bytes()
    ).hexdigest()
    if BINARY.exists() and receipt.exists() and receipt.read_text() == recipe_hash:
        return BINARY
    if platform.system() != "Darwin":
        raise RuntimeError("Build the bundled AirPlay receiver on macOS.")
    BUILD.mkdir(parents=True, exist_ok=True)
    deps = sources()
    env = {
        **os.environ,
        "MACOSX_DEPLOYMENT_TARGET": "14.0",
        "CFLAGS": "-O2 -mmacosx-version-min=14.0",
        "CXXFLAGS": "-O2 -mmacosx-version-min=14.0",
        "PKG_CONFIG_PATH": str(PREFIX / "lib/pkgconfig"),
        "PKG_CONFIG": shutil.which("pkg-config") or "/opt/homebrew/bin/pkg-config",
    }
    jobs = str(min(os.cpu_count() or 2, 8))
    prefix = f"--prefix={PREFIX}"
    if not (PREFIX / "lib/libconfig.a").exists():
        command("autoreconf", "-fi", cwd=deps["libconfig"], env=env)
        command(
            "./configure",
            prefix,
            "--disable-shared",
            "--disable-cxx",
            "--disable-examples",
            cwd=deps["libconfig"],
            env=env,
        )
        command("make", "-j", jobs, "install", cwd=deps["libconfig"], env=env)
    if not (PREFIX / "lib/libpopt.a").exists():
        command(
            "./configure", prefix, "--disable-shared", "--disable-nls", cwd=deps["popt"], env=env
        )
        command("make", "-j", jobs, "install", cwd=deps["popt"], env=env)
    if not (PREFIX / "lib/libcrypto.a").exists():
        target = "darwin64-arm64-cc" if platform.machine() == "arm64" else "darwin64-x86_64-cc"
        command(
            "./Configure",
            target,
            prefix,
            "--libdir=lib",
            "no-shared",
            "no-tests",
            "no-apps",
            cwd=deps["openssl"],
            env=env,
        )
        command("make", "-j", jobs, cwd=deps["openssl"], env=env)
        command("make", "install_sw", cwd=deps["openssl"], env=env)
    codec_config = deps["ffmpeg"] / "config_components.h"
    has_pcm = (
        codec_config.exists() and "#define CONFIG_PCM_S16BE_DECODER 1" in codec_config.read_text()
    )
    if not (PREFIX / "lib/libavcodec.a").exists() or not has_pcm:
        command(
            "./configure",
            prefix,
            "--disable-everything",
            "--disable-autodetect",
            "--disable-shared",
            "--enable-static",
            "--disable-programs",
            "--disable-doc",
            "--disable-avdevice",
            "--disable-avfilter",
            "--disable-swscale",
            "--enable-decoder=alac",
            "--enable-decoder=pcm_s16be",
            "--extra-cflags=-mmacosx-version-min=14.0",
            "--extra-ldflags=-mmacosx-version-min=14.0",
            cwd=deps["ffmpeg"],
            env=env,
        )
        command("make", "-j", jobs, "install", cwd=deps["ffmpeg"], env=env)
    libraries = subprocess.check_output(
        [env["PKG_CONFIG"], "--static", "--libs", "libavcodec"], env=env, text=True
    )
    harness = BUILD / "verify-airplay"
    command(
        "/usr/bin/cc",
        "-mmacosx-version-min=14.0",
        "-I" + str(PREFIX / "include"),
        str(ROOT / "tests/verify_airplay.c"),
        *shlex.split(libraries),
        "-o",
        str(harness),
        cwd=BUILD,
        env=env,
    )
    command(str(harness), cwd=BUILD, env=env)
    command("autoreconf", "-fi", cwd=deps["shairport-sync"], env=env)
    # Static package flags keep the installed app independent of Homebrew.
    env["PKG_CONFIG"] += " --static"
    command(
        "./configure",
        prefix,
        "--with-os=darwin",
        "--with-stdout",
        "--with-dns_sd",
        "--with-ssl=openssl",
        "--with-ffmpeg",
        "--without-configfiles",
        "--without-create-user-group",
        cwd=deps["shairport-sync"],
        env=env,
    )
    (deps["shairport-sync"] / "shairport-sync").unlink(missing_ok=True)
    command("make", "-j", jobs, cwd=deps["shairport-sync"], env=env)
    shutil.copy2(deps["shairport-sync"] / "shairport-sync", BINARY)
    result = subprocess.check_output(["otool", "-L", str(BINARY)], text=True)
    if any("/opt/" in line or str(BUILD) in line for line in result.splitlines()[1:]):
        BINARY.unlink()
        raise RuntimeError("AirPlay receiver has an unbundled dependency.")
    receipt.write_text(recipe_hash)
    return BINARY


if __name__ == "__main__":
    print(build_receiver())
