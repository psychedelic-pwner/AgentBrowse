import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentbrowse.engine import PlaywrightEngine

_PAGE = """<!DOCTYPE html>
<html>
<head><title>Agent Fixture</title></head>
<body>
  <h1>Fixture page</h1>
  <p>Visible body text for the agent.</p>
  <form action="/submit" method="post">
    <label for="name">Your name</label>
    <input id="name" name="name" type="text" />
    <button type="submit" id="go">Submit</button>
  </form>
  <script>console.error("boom-from-fixture");</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        body = _PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


@pytest.fixture
def form_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        yield url
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_goto_snapshot_returns_title_text_and_elements(form_server, chromium_ready):
    engine = PlaywrightEngine()
    engine.start(headless=True, proxy=None, profile_dir=None, ignore_https_errors=False)
    try:
        engine.goto(form_server)
        snap = engine.snapshot()
    finally:
        engine.close()

    assert snap["title"] == "Agent Fixture"
    assert "Visible body text for the agent." in snap["text"]
    assert snap["challenge"] is None
    assert snap["url"].startswith(form_server)
    assert any(el["role"] == "textbox" and el["selector"] == "#name" for el in snap["elements"])
    assert any(el["role"] == "textbox" and "Your name" in el["name"] for el in snap["elements"])
    assert any(el["role"] == "button" and "Submit" in el["name"] for el in snap["elements"])
    assert any("boom-from-fixture" in err for err in snap["console_errors"])


def test_persistent_profile_and_insecure_option(form_server, tmp_path, chromium_ready):
    profile = tmp_path / "prof"
    engine = PlaywrightEngine()
    engine.start(
        headless=True,
        proxy=None,
        profile_dir=str(profile),
        ignore_https_errors=True,
    )
    try:
        assert engine.options is not None
        assert engine.options.ignore_https_errors is True
        assert engine.options.persistent_kwargs()["ignore_https_errors"] is True
        engine.goto(form_server)
        snap = engine.snapshot()
    finally:
        engine.close()
    assert snap["title"] == "Agent Fixture"
    assert profile.is_dir()
    assert any(profile.iterdir())
