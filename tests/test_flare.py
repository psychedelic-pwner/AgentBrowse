import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentbrowse.flare import FlareClient, FlareResult


class _Handler(BaseHTTPRequestHandler):
    seen: dict = {}

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path != "/health":
            self.send_error(404)
            return
        self._send({"status": "ok"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            incoming = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            incoming = {}
        _Handler.seen = {"path": path, "body": incoming}
        if path != "/v1":
            self._send({"status": "error", "message": "bad path"}, code=404)
            return
        self._send(
            {
                "status": "ok",
                "message": "Challenge solved!",
                "solution": {
                    "url": incoming.get("url"),
                    "status": 200,
                    "userAgent": "AgentBrowseTest/1.0",
                    "cookies": [
                        {
                            "name": "cf_clearance",
                            "value": "token",
                            "domain": "example.com",
                            "path": "/",
                        }
                    ],
                },
            }
        )

    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


@pytest.fixture
def flare_server():
    _Handler.seen = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_solve_reads_cookies_and_user_agent(flare_server):
    client = FlareClient(flare_server)
    assert client.available() is True
    result = client.solve("https://example.com/blocked")
    assert isinstance(result, FlareResult)
    assert result.status == "ok"
    assert result.user_agent == "AgentBrowseTest/1.0"
    assert result.cookies[0]["name"] == "cf_clearance"
    assert result.cookies[0]["value"] == "token"
    assert _Handler.seen["path"] == "/v1"
    assert _Handler.seen["body"]["cmd"] == "request.get"
    assert _Handler.seen["body"]["url"] == "https://example.com/blocked"
    assert result.raw["solution"]["status"] == 200


def test_unreachable_host_returns_an_error_result():
    client = FlareClient("http://127.0.0.1:1")
    assert client.available() is False
    result = client.solve("https://example.com/")
    assert isinstance(result, FlareResult)
    assert result.status == "error"
    assert result.cookies == []
    assert result.user_agent == ""
    assert "error" in result.raw
