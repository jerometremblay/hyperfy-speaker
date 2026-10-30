import json
import stat

import pytest

from hyperfy_speaker.config import Config, normalize_world


@pytest.mark.parametrize(
    "url",
    [
        "http://world.example",
        "https://user:password@world.example",
        "https://world.example/?token=secret",
        "https://world.example/#token",
        "file:///tmp/world",
        "https://world.example/another-app",
    ],
)
def test_refuses_insecure_or_credential_bearing_world_urls(url):
    with pytest.raises(ValueError):
        normalize_world(url)


def test_configuration_does_not_store_tokens_and_is_owner_readable(tmp_path, monkeypatch):
    file = tmp_path / "config.json"
    monkeypatch.setenv("HYPERFY_SPEAKER_CONFIG", str(file))
    monkeypatch.setenv("HYPERFY_SPEAKER_TOKEN", "secret-token")
    config = Config("https://world.example/api/agent/", "speaker-id")
    config.save()
    saved = json.loads(file.read_text())
    assert saved["world_url"] == "https://world.example"
    assert "secret-token" not in file.read_text()
    assert "token" not in saved
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert Config.load().token() == "secret-token"


def test_localhost_and_ipv6_are_supported():
    assert normalize_world("http://localhost:3000/api") == "http://localhost:3000"
    assert normalize_world("http://[::1]:3000") == "http://[::1]:3000"
