import io
import json
import sys

from agentbrowse.cli import main
from agentbrowse.mcp_server import McpServer

_SNAPSHOT = {
    "url": "http://127.0.0.1/",
    "title": "Fake",
    "challenge": None,
    "text": "hello",
    "elements": [{"role": "button", "name": "Go", "selector": "#go"}],
    "console_errors": [],
}

_TOOL_NAMES = (
    "browser_open",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_screenshot",
    "browser_evaluate",
    "browser_cookies",
    "browser_close",
)


class FakeEngine:
    """In-process Engine. No browser and no network."""

    def __init__(self, cookies=None):
        self.cookies_jar = list(cookies or [])
        self.calls = []
        self.started = False
        self.closed = False
        self.snapshot_payload = dict(_SNAPSHOT)

    def start(self, headless, proxy, profile_dir, ignore_https_errors):
        self.started = True
        self.calls.append(("start", headless, proxy, profile_dir, ignore_https_errors))

    def goto(self, url):
        self.calls.append(("goto", url))
        self.snapshot_payload = {**self.snapshot_payload, "url": url}

    def snapshot(self):
        self.calls.append(("snapshot",))
        return dict(self.snapshot_payload)

    def click(self, sel):
        self.calls.append(("click", sel))

    def type(self, sel, text):
        self.calls.append(("type", sel, text))

    def press(self, sel, key):
        self.calls.append(("press", sel, key))

    def screenshot(self, path):
        self.calls.append(("screenshot", path))

    def evaluate(self, js):
        self.calls.append(("evaluate", js))
        return {"echo": js}

    def cookies(self):
        self.calls.append(("cookies",))
        return [dict(item) for item in self.cookies_jar]

    def close(self):
        self.closed = True
        self.started = False
        self.calls.append(("close",))


def _run(engine, lines, factory_calls=None):
    stdin = io.StringIO("".join(lines))
    stdout = io.StringIO()
    created = factory_calls if factory_calls is not None else []

    def factory():
        created.append(engine)
        return engine

    McpServer(factory, stdout, stdin).serve()
    raw = [line for line in stdout.getvalue().splitlines() if line.strip()]
    return [json.loads(line) for line in raw], created


def _request(method, request_id, params=None):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message) + "\n"


def _tool(request_id, name, arguments=None):
    return _request(
        "tools/call",
        request_id,
        {"name": name, "arguments": arguments or {}},
    )


def _payload(response):
    content = response["result"]["content"]
    assert content[0]["type"] == "text"
    return json.loads(content[0]["text"])


def test_initialize_handshake_returns_protocol_and_server_info():
    engine = FakeEngine()
    responses, created = _run(
        engine,
        [
            _request(
                "initialize",
                1,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            ),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n",
        ],
    )
    assert len(responses) == 1
    assert responses[0]["id"] == 1
    assert responses[0]["result"] == {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "agentbrowse", "version": "0.1.0"},
    }
    assert created == []
    assert engine.calls == []


def test_tools_list_contains_eight_object_schemas():
    responses, _created = _run(FakeEngine(), [_request("tools/list", 2)])
    tools = responses[0]["result"]["tools"]
    assert [tool["name"] for tool in tools] == list(_TOOL_NAMES)
    for tool in tools:
        assert set(tool) >= {"name", "description", "inputSchema"}
        assert tool["inputSchema"]["type"] == "object"
        assert isinstance(tool["description"], str) and tool["description"]


def test_browser_open_returns_snapshot_and_starts_lazily():
    engine = FakeEngine()
    responses, created = _run(
        engine,
        [
            _request("initialize", 1),
            _tool(2, "browser_open", {"url": "http://127.0.0.1/form"}),
        ],
    )
    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    assert created == [engine]
    assert engine.calls[0][0] == "start"
    assert engine.calls[1] == ("goto", "http://127.0.0.1/form")
    body = _payload(responses[1])
    assert responses[1]["result"]["isError"] is False
    assert body["url"] == "http://127.0.0.1/form"
    assert body["title"] == "Fake"
    assert body["challenge"] is None
    assert body["text"] == "hello"
    assert body["elements"][0]["selector"] == "#go"
    assert body["console_errors"] == []


def test_unknown_method_and_malformed_line_keep_the_server_alive():
    lines = [
        _request("nope", 7),
        "this is not json\n",
        _request("initialize", 8),
    ]
    responses, created = _run(FakeEngine(), lines)
    assert responses[0]["id"] == 7
    assert responses[0]["error"]["code"] == -32601
    assert "result" not in responses[0]
    assert responses[1]["id"] is None
    assert responses[1]["error"]["code"] == -32700
    assert responses[2]["id"] == 8
    assert responses[2]["result"]["serverInfo"]["name"] == "agentbrowse"
    assert created == []


def test_browser_evaluate_refuses_process_exit():
    engine = FakeEngine()
    responses, _created = _run(
        engine,
        [
            _tool(1, "browser_open", {"url": "http://127.0.0.1/"}),
            _tool(2, "browser_evaluate", {"expression": "process.exit()"}),
            _tool(3, "browser_evaluate", {"expression": "require('fs')"}),
            _tool(4, "browser_evaluate", {"expression": "import('fs')"}),
            _request("tools/list", 5),
        ],
    )
    for response in responses[1:4]:
        assert response["result"]["isError"] is True
        assert "refused" in response["result"]["content"][0]["text"]
    assert ("evaluate",) not in [(call[0],) for call in engine.calls]
    assert [call for call in engine.calls if call[0] == "evaluate"] == []
    assert len(responses[4]["result"]["tools"]) == 8


def test_browser_cookies_redacts_values():
    secret = "super-secret-cookie-value"
    engine = FakeEngine(
        cookies=[
            {
                "name": "session",
                "value": secret,
                "domain": ".example.test",
                "path": "/",
                "expires": 123,
                "httpOnly": True,
                "secure": True,
            }
        ]
    )
    responses, _created = _run(
        engine,
        [
            _tool(1, "browser_open", {"url": "http://127.0.0.1/"}),
            _tool(2, "browser_cookies"),
        ],
    )
    assert responses[1]["result"]["isError"] is False
    raw = responses[1]["result"]["content"][0]["text"]
    assert secret not in raw
    assert "httpOnly" not in raw
    body = json.loads(raw)
    assert body == {
        "cookies": [
            {"name": "session", "domain": ".example.test", "path": "/", "expires": 123}
        ]
    }
    assert "value" not in body["cookies"][0]


def test_mcp_cli_banner_stays_on_stderr_and_open_uses_flags(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("AGENTBROWSE_HOME", str(tmp_path))
    engine = FakeEngine()
    monkeypatch.setattr("agentbrowse.cli.create_engine", lambda name="chromium": engine)
    stdin = io.StringIO(
        _request("initialize", 1)
        + _tool(2, "browser_open", {"url": "http://127.0.0.1/mcp"})
    )
    monkeypatch.setattr(sys, "stdin", stdin)
    code = main(
        [
            "mcp",
            "--headed",
            "--insecure",
            "--profile", "box",
            "--proxy", "http://127.0.0.1:8080",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert captured.err == "agentbrowse MCP server on stdio\n"
    responses = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    assert _payload(responses[1])["url"] == "http://127.0.0.1/mcp"
    kind, headless, proxy, profile_dir, insecure = engine.calls[0]
    assert kind == "start"
    assert headless is False
    assert insecure is True
    assert proxy == {"server": "http://127.0.0.1:8080"}
    assert profile_dir == str(tmp_path / "profiles" / "box")
