#!/usr/bin/env python3
"""Apply this repo's narrow speaker integration to a Hyperfy engine checkout."""

import argparse
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("checkout", type=Path)
args = parser.parse_args()
root = args.checkout.resolve()
if not (root / "src/core/systems/ClientLiveKit.js").is_file():
    parser.error("This is not a Hyperfy engine checkout.")
patch = Path(__file__).with_name("integration.patch")
if not patch.exists():
    parser.error("integration.patch is missing.")
check = subprocess.run(
    ["git", "apply", "--check", str(patch)], cwd=root, capture_output=True, text=True
)
if check.returncode:
    reverse = subprocess.run(
        ["git", "apply", "--reverse", "--check", str(patch)], cwd=root, capture_output=True
    )
    if reverse.returncode == 0:
        print("Hyperfy speaker integration is already applied.")
    else:
        raise SystemExit(
            "The patch conflicts with this checkout. No files were changed.\n" + check.stderr
        )
else:
    subprocess.run(["git", "apply", str(patch)], cwd=root, check=True)
    print("Speaker integration applied. Build and restart your Hyperfy server.")
