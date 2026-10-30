from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

KEYCHAIN_SERVICE = "org.hyperfy.speaker"
DEFAULT_DEVICE = "Hyperfy Speaker"


def config_path() -> Path:
    override = os.environ.get("HYPERFY_SPEAKER_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support/Hyperfy Speaker/config.json"


def normalize_world(value: str) -> str:
    parts = urlsplit(value.strip())
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if (
        parts.scheme not in ("http", "https")
        or (parts.scheme == "http" and not local)
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path.rstrip("/") not in ("", "/api", "/api/agent")
    ):
        raise ValueError("Use a world HTTPS URL. HTTP is allowed only for localhost.")
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


@dataclass
class Config:
    world_url: str = ""
    speaker_id: str = ""
    device: str = DEFAULT_DEVICE

    @classmethod
    def load(cls) -> Config:
        path = config_path()
        if not path.exists():
            return cls()
        values = json.loads(path.read_text())
        if not isinstance(values, dict):
            raise ValueError("Configuration must be a JSON object.")
        known = {key: values[key] for key in ("world_url", "speaker_id", "device") if key in values}
        if any(not isinstance(value, str) for value in known.values()):
            raise ValueError("Configuration fields must be strings.")
        return cls(**known)

    def save(self) -> None:
        if self.world_url:
            self.world_url = normalize_world(self.world_url)
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(asdict(self), stream, indent=2)
                stream.write("\n")
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def token(self) -> str:
        value = os.environ.get("HYPERFY_SPEAKER_TOKEN", "")
        if not value and sys.platform == "darwin" and self.world_url:
            result = subprocess.run(
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-s",
                    KEYCHAIN_SERVICE,
                    "-a",
                    normalize_world(self.world_url),
                    "-w",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                value = result.stdout.rstrip("\n")
        if not value:
            raise ValueError(
                "Save the world's Agent API write token in the Mac app's Settings, "
                "or set HYPERFY_SPEAKER_TOKEN."
            )
        return value
