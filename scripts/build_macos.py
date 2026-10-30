#!/usr/bin/env python3
"""Build the native driver, standalone worker, menu bar app, and macOS installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".build"
DIST = ROOT / "dist"
VERSION = "0.1.0"
BUNDLE_ID = "org.hyperfy.speaker"
MINIMUM_MACOS = "14.0"


def command(*args: str, **kwargs) -> None:
    subprocess.run(args, check=True, **kwargs)


def fetch_blackhole() -> Path:
    lock = json.loads((ROOT / "macos/blackhole.lock.json").read_text())
    source = BUILD / "blackhole-source"
    source.mkdir(parents=True, exist_ok=True)
    for relative, expected in lock["files"].items():
        output = source / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.exists():
            url = f"https://raw.githubusercontent.com/ExistentialAudio/BlackHole/{lock['commit']}/{relative}"
            output.write_bytes(urllib.request.urlopen(url, timeout=30).read())
        if hashlib.sha256(output.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"BlackHole source checksum mismatch: {relative}")
    return source


def build_driver(signing_identity: str) -> Path:
    source = fetch_blackhole()
    contents = BUILD / "HyperfySpeaker.driver/Contents"
    executable = contents / "MacOS/HyperfySpeaker"
    executable.parent.mkdir(parents=True, exist_ok=True)
    resources = contents / "Resources"
    resources.mkdir(exist_ok=True)
    shutil.copy(source / "BlackHole/BlackHole.icns", resources)
    command(
        "/usr/bin/clang",
        "-arch",
        "arm64",
        "-arch",
        "x86_64",
        "-mmacosx-version-min=11.0",
        "-O2",
        "-fblocks",
        "-Wno-format-extra-args",
        "-bundle",
        "-framework",
        "CoreAudio",
        "-framework",
        "CoreFoundation",
        "-framework",
        "Accelerate",
        '-DkDriver_Name="Hyperfy Speaker"',
        '-DkDevice_Name="Hyperfy Speaker"',
        '-DkPlugIn_BundleID="org.hyperfy.speaker.driver"',
        "-DkHas_Driver_Name_Format=0",
        "-DkNumber_Of_Channels=2",
        "-DkSampleRates=48000",
        str(source / "BlackHole/BlackHole.c"),
        "-o",
        str(executable),
    )
    info = plistlib.loads((source / "BlackHole/BlackHole.plist").read_bytes())
    info.update(
        CFBundleExecutable="HyperfySpeaker",
        CFBundleIdentifier=BUNDLE_ID + ".driver",
        CFBundleName="Hyperfy Speaker",
        CFBundleShortVersionString=VERSION,
        CFBundleVersion="1",
    )
    # Independent factory ID lets the branded driver coexist with stock BlackHole.
    factory = "44f2e3a9-2176-4737-8cfe-c3c721b0fc65"
    info["CFPlugInFactories"] = {factory: "BlackHole_Create"}
    info["CFPlugInTypes"] = {"443ABAB8-E7B3-491A-B985-BEB9187030DB": [factory]}
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    command("/usr/bin/codesign", "--force", "--sign", signing_identity, str(contents.parent))
    harness = BUILD / "verify-driver"
    command(
        "/usr/bin/clang",
        "-framework",
        "CoreAudio",
        "-framework",
        "CoreFoundation",
        str(ROOT / "tests/verify_driver.c"),
        "-o",
        str(harness),
    )
    command(str(harness), str(executable))
    return contents.parent


def build_app(signing_identity: str) -> Path:
    app = DIST / "Hyperfy Speaker.app"
    if app.exists():
        shutil.rmtree(app)
    contents = app / "Contents"
    binary = contents / "MacOS/HyperfySpeaker"
    binary.parent.mkdir(parents=True)
    resources = contents / "Resources"
    resources.mkdir()
    worker_environment = {
        **os.environ,
        "PYINSTALLER_CONFIG_DIR": str(BUILD / "pyinstaller-cache"),
    }
    command(
        "/usr/bin/swiftc",
        "-O",
        "-target",
        f"{platform.machine()}-apple-macos{MINIMUM_MACOS}",
        "-module-cache-path",
        str(BUILD / "swift-module-cache"),
        "-framework",
        "Cocoa",
        "-framework",
        "Security",
        str(ROOT / "macos/HyperfySpeaker.swift"),
        "-o",
        str(binary),
    )
    command(
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        "speaker-worker",
        "--onedir",
        "--console",
        "--collect-all",
        "livekit.rtc",
        "--collect-all",
        "sounddevice",
        "--collect-all",
        "_sounddevice_data",
        "--collect-submodules",
        "hyperfy_speaker",
        "--paths",
        str(ROOT / "src"),
        "--distpath",
        str(BUILD / "worker-dist"),
        "--workpath",
        str(BUILD / "worker-build"),
        "--specpath",
        str(BUILD),
        str(ROOT / "scripts/worker.py"),
        env=worker_environment,
    )
    shutil.copytree(BUILD / "worker-dist/speaker-worker", resources / "speaker-worker")
    shutil.copy(ROOT / "LICENSE", resources)
    info = {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": "Hyperfy Speaker",
        "CFBundleDisplayName": "Hyperfy Speaker",
        "CFBundleExecutable": "HyperfySpeaker",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": "1",
        "LSUIElement": True,
        "LSMinimumSystemVersion": MINIMUM_MACOS,
        "NSMicrophoneUsageDescription": "Capture audio from the Hyperfy Speaker virtual output.",
        "NSLocalNetworkUsageDescription": "Connect to a Hyperfy world on your local network.",
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    # Sign nested code before its parent. Never add a blanket deep-sign operation.
    runtime_flags = [] if signing_identity == "-" else ["--options", "runtime", "--timestamp"]
    for item in sorted(resources.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if item.is_file() and item.suffix in (".so", ".dylib"):
            command(
                "/usr/bin/codesign",
                "--force",
                "--sign",
                signing_identity,
                *runtime_flags,
                str(item),
            )
    command(
        "/usr/bin/codesign",
        "--force",
        "--sign",
        signing_identity,
        *runtime_flags,
        "--entitlements",
        str(ROOT / "macos/worker-entitlements.plist"),
        str(resources / "speaker-worker/speaker-worker"),
    )
    command("/usr/bin/codesign", "--force", "--sign", signing_identity, *runtime_flags, str(app))
    verify_deployment_target(app)
    return app


def verify_deployment_target(app: Path) -> None:
    candidates = [
        app / "Contents/MacOS/HyperfySpeaker",
        app / "Contents/Resources/speaker-worker/speaker-worker",
        *[item for item in app.rglob("*") if item.suffix in (".so", ".dylib")],
    ]
    minimum = tuple(int(part) for part in MINIMUM_MACOS.split("."))
    for binary in candidates:
        result = subprocess.run(
            ["/usr/bin/vtool", "-show-build", str(binary)],
            check=True,
            capture_output=True,
            text=True,
        )
        versions = re.findall(r"^\s+minos\s+([0-9.]+)", result.stdout, flags=re.MULTILINE)
        if not versions:
            versions = re.findall(r"^\s+version\s+([0-9.]+)", result.stdout, flags=re.MULTILINE)
        if any(
            tuple(int(part) for part in version.split(".")[:2]) > minimum for version in versions
        ):
            raise RuntimeError(
                f"{binary.name} requires a newer macOS than {MINIMUM_MACOS}. "
                "Build with `uv sync --all-groups --managed-python`."
            )
    print(f"All bundled native libraries support macOS {MINIMUM_MACOS} or earlier.")


def build_package(driver: Path, app: Path, installer_identity: str | None) -> Path:
    payload = BUILD / "payload"
    if payload.exists():
        shutil.rmtree(payload)
    target_driver = payload / "Library/Audio/Plug-Ins/HAL/HyperfySpeaker.driver"
    target_driver.parent.mkdir(parents=True)
    shutil.copytree(driver, target_driver)
    applications = payload / "Applications"
    applications.mkdir()
    shutil.copytree(app, applications / app.name)
    sources = payload / "Library/Application Support/Hyperfy Speaker/Sources"
    sources.mkdir(parents=True)
    archive = sources / f"hyperfy-speaker-{VERSION}-source.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for relative in [
            "src",
            "macos",
            "scripts",
            "tests",
            "hyperfy",
            "pyproject.toml",
            ".python-version",
            ".gitignore",
            "uv.lock",
            "README.md",
            "LICENSE",
            "THIRD_PARTY.md",
            "AGENTS.md",
        ]:
            path = ROOT / relative
            if path.exists():
                output.add(path, arcname="hyperfy-speaker/" + relative, filter=source_filter)
        output.add(BUILD / "blackhole-source", arcname="hyperfy-speaker/third_party/BlackHole")
    scripts = BUILD / "installer-scripts"
    scripts.mkdir(exist_ok=True)
    postinstall = scripts / "postinstall"
    postinstall.write_text(
        "#!/bin/sh\n"
        "# Loading the new HAL driver requires an audio service restart.\n"
        "/usr/bin/killall -TERM coreaudiod 2>/dev/null || true\n"
        "exit 0\n"
    )
    postinstall.chmod(0o755)
    component = BUILD / "hyperfy-speaker-component.pkg"
    command(
        "/usr/bin/pkgbuild",
        "--root",
        str(payload),
        "--scripts",
        str(scripts),
        "--install-location",
        "/",
        "--identifier",
        BUNDLE_ID + ".installer",
        "--version",
        VERSION,
        str(component),
    )
    package = DIST / f"HyperfySpeaker-{VERSION}-{platform.machine()}.pkg"
    args = [
        "/usr/bin/productbuild",
        "--package",
        str(component),
        "--resources",
        str(ROOT / "macos"),
        "--version",
        VERSION,
    ]
    if installer_identity:
        args += ["--sign", installer_identity]
    command(*args, str(package))
    return package


def source_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if "__pycache__" in info.name or info.name.endswith((".pyc", ".DS_Store")):
        return None
    return info


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver-only", action="store_true")
    parser.add_argument(
        "--sign", default="-", help="Application/driver signing identity; default ad hoc"
    )
    parser.add_argument("--installer-sign", help="Developer ID Installer identity")
    parser.add_argument(
        "--notary-profile", help="Saved notarytool profile; requires both signing identities"
    )
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("Build the macOS installer on a Mac.")
    if args.notary_profile and (args.sign == "-" or not args.installer_sign):
        parser.error("Notarization requires --sign and --installer-sign.")
    BUILD.mkdir(exist_ok=True)
    DIST.mkdir(exist_ok=True)
    driver = build_driver(args.sign)
    if args.driver_only:
        print(driver)
        return
    app = build_app(args.sign)
    package = build_package(driver, app, args.installer_sign)
    if args.notary_profile:
        command(
            "/usr/bin/xcrun",
            "notarytool",
            "submit",
            str(package),
            "--keychain-profile",
            args.notary_profile,
            "--wait",
        )
        command("/usr/bin/xcrun", "stapler", "staple", str(package))
    print(f"Built {package}")


if __name__ == "__main__":
    main()
