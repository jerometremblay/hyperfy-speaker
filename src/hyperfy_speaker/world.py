from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import NAMESPACE_URL, uuid5

from .config import Config, normalize_world

SPEAKER_SOURCE = """
// Audio is anchored to this app by Hyperfy's native LiveKit speaker integration.
if (world.isClient) {
  const box = app.create('prim', {
    type: 'box', size: [0.45, 0.7, 0.3],
    color: '#202329', roughness: 0.85, position: [0, 0.35, 0],
  })
  app.add(box)
  for (const [height, radius] of [[0.24, 0.13], [0.52, 0.07]]) {
    const cone = app.create('prim', {
      type: 'sphere', size: [radius], color: '#07090c', roughness: 0.7,
      position: [0, height, 0.151], scale: [1, 1, 0.15],
    })
    app.add(cone)
  }
}
""".strip()


class WorldError(RuntimeError):
    def __init__(self, status: int, endpoint: str):
        self.status = status
        super().__init__(f"World API returned HTTP {status} for {endpoint}.")


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # Never forward the world write token to a redirected host.
        return None


class WorldClient:
    def __init__(self, config: Config, token: str | None = None):
        self.origin = normalize_world(config.world_url)
        self.token = token if token is not None else config.token()
        self.opener = build_opener(NoRedirects())

    def request(self, method: str, endpoint: str, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(
            self.origin + "/api/agent" + endpoint,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                content = response.read(1024 * 1024 + 1)
                if len(content) > 1024 * 1024:
                    raise ValueError("World API response is too large.")
                return json.loads(content) if content else None
        except HTTPError as error:
            raise WorldError(error.code, endpoint) from None
        except URLError:
            raise RuntimeError("Could not connect to the world API.") from None

    def create_speaker(self, position: list[float]) -> str:
        # Stable IDs make a retried setup resume instead of making duplicate speakers.
        blueprint_id = str(uuid5(NAMESPACE_URL, self.origin + "/hyperfy-speaker/blueprint"))
        entity_id = str(uuid5(NAMESPACE_URL, self.origin + "/hyperfy-speaker/entity"))
        script = self.request("POST", "/scripts", {"source": SPEAKER_SOURCE})
        try:
            blueprint = self.request("GET", "/apps/" + blueprint_id)
            if blueprint.get("script") != script["url"]:
                raise ValueError(
                    "The existing speaker script has changed. Setup will not overwrite it."
                )
        except WorldError as error:
            if error.status != 404:
                raise
            self.request(
                "POST",
                "/apps",
                {
                    "id": blueprint_id,
                    "name": "Hyperfy Speaker",
                    "model": "script-only",
                    "script": script["url"],
                    "props": {},
                    "preload": True,
                },
            )
        try:
            entity = self.request("GET", "/entities/" + entity_id)
            if entity.get("blueprint") != blueprint_id:
                raise ValueError("The existing speaker ID belongs to another app.")
        except WorldError as error:
            if error.status != 404:
                raise
            self.request(
                "POST",
                "/entities",
                {"id": entity_id, "blueprint": blueprint_id, "position": position, "pinned": True},
            )
        return entity_id

    def session(self, speaker_id: str) -> dict:
        endpoint = "/entities/" + quote(speaker_id, safe="") + "/speaker-session"
        session = self.request("POST", endpoint, {})
        if not isinstance(session, dict):
            raise ValueError("World returned an invalid speaker session.")
        parts = urlsplit(session.get("wsUrl", ""))
        local = parts.hostname in ("localhost", "127.0.0.1", "::1")
        if (
            parts.scheme not in ("ws", "wss")
            or (parts.scheme == "ws" and not local)
            or not parts.hostname
            or parts.username
            or parts.password
            or not isinstance(session.get("token"), str)
            or not session["token"]
        ):
            raise ValueError("World returned an invalid or insecure LiveKit session.")
        return session
