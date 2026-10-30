import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hyperfy_speaker.config import Config
from hyperfy_speaker.world import WorldClient, WorldError


@pytest.fixture
def server():
    objects = {}
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def handle_request(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            status = 200
            path = self.path.removeprefix("/api/agent")
            if self.headers.get("Authorization") != "Bearer writer":
                status, output = 401, {}
            elif path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/api/agent/redirect-target")
                self.end_headers()
                return
            elif path == "/scripts":
                output = {"url": "asset://" + "a" * 64 + ".js"}
            elif self.command == "POST" and path in ("/apps", "/entities"):
                prefix = path + "/" + data["id"]
                objects[prefix] = data
                status, output = 201, data
            elif path in objects:
                output = objects[path]
            elif path.endswith("/speaker-session"):
                output = {"wsUrl": "wss://rtc.example", "token": "speaker-only-token"}
            else:
                status, output = 404, {}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(output).encode())

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{http.server_port}", objects, calls
    http.shutdown()
    http.server_close()
    thread.join()


def test_setup_retries_resume_the_same_objects_and_preserve_placement(server):
    origin, objects, calls = server
    client = WorldClient(Config(origin), token="writer")
    speaker_id = client.create_speaker([1, 0, 2])
    assert client.create_speaker([9, 9, 9]) == speaker_id
    assert objects["/entities/" + speaker_id]["position"] == [1, 0, 2]
    assert len([call for call in calls if call[:2] == ("POST", "/api/agent/entities")]) == 1
    assert client.session(speaker_id)["token"] == "speaker-only-token"


def test_setup_refuses_to_overwrite_an_edited_speaker(server):
    origin, objects, _ = server
    client = WorldClient(Config(origin), token="writer")
    client.create_speaker([0, 0, 0])
    app = next(value for key, value in objects.items() if key.startswith("/apps/"))
    app["script"] = "asset://changed.js"
    with pytest.raises(ValueError, match="will not overwrite"):
        client.create_speaker([0, 0, 0])


def test_authentication_errors_do_not_echo_the_token(server):
    origin, _, _ = server
    client = WorldClient(Config(origin), token="do-not-log-me")
    with pytest.raises(WorldError) as error:
        client.session("speaker")
    assert error.value.status == 401
    assert "do-not-log-me" not in str(error.value)


def test_redirects_never_forward_the_write_token(server):
    origin, _, calls = server
    client = WorldClient(Config(origin), token="writer")
    with pytest.raises(WorldError) as error:
        client.request("GET", "/redirect")
    assert error.value.status == 302
    assert not any(call[1].endswith("/redirect-target") for call in calls)
