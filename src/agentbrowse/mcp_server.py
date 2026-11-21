"""Minimal MCP server: newline-delimited JSON-RPC 2.0 over stdio.

No SDK. stdout is reserved for protocol messages, one JSON object per line.
Logs go to stderr. The browser starts on the first browser_open.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from agentbrowse import __version__
from agentbrowse.engine import require_http_url

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agentbrowse"

# Page-context only. These tokens are a Node escape, not DOM script.
_FORBIDDEN_JS = ("require(", "process.", "import(")
_COOKIE_FIELDS = ("name", "domain", "path", "expires")

_NO_ARG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "browser_open",
        "description": "Open an http(s) URL and return a page snapshot. Starts the browser on first use.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) URL to open with GET"},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browser_snapshot",
        "description": "Return the current snapshot: url, title, challenge, text, elements, console_errors.",
        "inputSchema": dict(_NO_ARG_SCHEMA),
    },
    {
        "name": "browser_click",
        "description": "Click a selector. Returns ok, url, and title.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector"},
            },
            "required": ["selector"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browser_type",
        "description": "Fill a selector. submit presses Enter after the fill. Returns ok and url.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "submit": {"type": "boolean", "description": "Press Enter after filling"},
            },
            "required": ["selector", "text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browser_screenshot",
        "description": "Write a PNG screenshot to path. Returns ok and path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path for the PNG"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browser_evaluate",
        "description": (
            "Evaluate a JavaScript expression in the page. "
            "Expressions containing require(, process., or import( are refused."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Page-context JavaScript"},
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browser_cookies",
        "description": (
            "List cookies as name, domain, path, and expires. "
            "Values are credentials and are never returned."
        ),
        "inputSchema": dict(_NO_ARG_SCHEMA),
    },
    {
        "name": "browser_close",
        "description": "Close the browser. Returns ok.",
        "inputSchema": dict(_NO_ARG_SCHEMA),
    },
]


class _JsonRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class McpServer:
    """JSON-RPC MCP server. `engine_factory` is called on the first browser_open."""

    def __init__(self, engine_factory, stdout: TextIO, stdin: TextIO, *, options=None) -> None:
        self._engine_factory = engine_factory
        self._stdout = stdout
        self._stdin = stdin
        self._options = options
        self._engine = None
        self._started = False
        self._handlers = {
            "browser_open": self._tool_open,
            "browser_snapshot": self._tool_snapshot,
            "browser_click": self._tool_click,
            "browser_type": self._tool_type,
            "browser_screenshot": self._tool_screenshot,
            "browser_evaluate": self._tool_evaluate,
            "browser_cookies": self._tool_cookies,
            "browser_close": self._tool_close,
        }

    def serve(self) -> None:
        """Read stdin until EOF. A bad line never ends the loop."""
        for line in self._stdin:
            try:
                self.handle_line(line)
            except Exception as exc:
                self._log(f"internal error: {exc}")
                self._send_error(None, -32603, "Internal error")

    def handle_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError:
            self._send_error(None, -32700, "Parse error")
            return
        if not isinstance(message, dict):
            self._send_error(None, -32600, "Invalid Request")
            return
        method = message.get("method")
        if not isinstance(method, str) or not method:
            request_id = message["id"] if "id" in message else None
            self._send_error(request_id, -32600, "Invalid Request")
            return
        # Notifications, including notifications/initialized, get no response.
        if method.startswith("notifications/") or "id" not in message:
            return
        request_id = message["id"]
        try:
            result = self._dispatch(method, message.get("params"))
        except _JsonRpcError as exc:
            self._send_error(request_id, exc.code, exc.message)
            return
        except Exception as exc:
            self._log(f"{method} failed: {exc}")
            self._send_error(request_id, -32603, "Internal error")
            return
        self._send_result(request_id, result)

    def _dispatch(self, method: str, params: Any) -> Any:
        if method == "initialize":
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
            }
        if method == "tools/list":
            return {"tools": list(TOOLS)}
        if method == "tools/call":
            return self._tools_call(params)
        raise _JsonRpcError(-32601, "Method not found")

    def _tools_call(self, params: Any) -> dict:
        if not isinstance(params, dict):
            raise _JsonRpcError(-32602, "Invalid params")
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return self._tool_error("tool name is required")
        arguments = params.get("arguments", params.get("args", {}))
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return self._tool_error("arguments must be an object")
        handler = self._handlers.get(name)
        if handler is None:
            return self._tool_error(f"unknown tool: {name}")
        try:
            payload = handler(arguments)
            text = json.dumps(payload, ensure_ascii=False)
        except Exception as exc:
            self._log(f"{name} failed: {exc}")
            return self._tool_error(str(exc))
        return {"content": [{"type": "text", "text": text}], "isError": False}

    def _tool_open(self, arguments: dict) -> dict:
        url = arguments.get("url")
        if not isinstance(url, str):
            raise ValueError("url is required")
        url = require_http_url(url)
        engine = self._ensure_started()
        engine.goto(url)
        return self._snapshot(engine)

    def _tool_snapshot(self, _arguments: dict) -> dict:
        return self._snapshot(self._require_started())

    def _tool_click(self, arguments: dict) -> dict:
        selector = _require_selector(arguments)
        engine = self._require_started()
        engine.click(selector)
        snap = self._snapshot(engine)
        return {"ok": True, "url": snap.get("url"), "title": snap.get("title")}

    def _tool_type(self, arguments: dict) -> dict:
        selector = _require_selector(arguments)
        text = arguments.get("text")
        if not isinstance(text, str):
            raise ValueError("text is required")
        submit = arguments.get("submit", False)
        if not isinstance(submit, bool):
            raise ValueError("submit must be a boolean")
        engine = self._require_started()
        engine.type(selector, text)
        if submit:
            press = getattr(engine, "press", None)
            if not callable(press):
                raise RuntimeError("submit is not supported by this engine")
            press(selector, "Enter")
        snap = self._snapshot(engine)
        return {"ok": True, "url": snap.get("url")}

    def _tool_screenshot(self, arguments: dict) -> dict:
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path is required")
        engine = self._require_started()
        engine.screenshot(path)
        return {"ok": True, "path": path}

    def _tool_evaluate(self, arguments: dict) -> dict:
        expression = arguments.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("expression is required")
        for token in _FORBIDDEN_JS:
            if token in expression:
                raise ValueError(
                    f"refused: expression contains {token!r}; "
                    "this is a page-context evaluator, not a Node escape"
                )
        engine = self._require_started()
        result = engine.evaluate(expression)
        return {"ok": True, "result": result}

    def _tool_cookies(self, _arguments: dict) -> dict:
        engine = self._require_started()
        reader = getattr(engine, "cookies", None)
        if not callable(reader):
            raise RuntimeError("engine does not expose cookies")
        return redact_cookies(reader())

    def _tool_close(self, _arguments: dict) -> dict:
        if self._engine is None or not self._started:
            return {"ok": True}
        self._engine.close()
        self._started = False
        return {"ok": True}

    def _ensure_started(self):
        if self._engine is None:
            self._engine = self._engine_factory()
        if not self._started:
            self._start_engine(self._engine)
            self._started = True
        return self._engine

    def _require_started(self):
        if self._engine is None or not self._started:
            raise RuntimeError("browser is not started")
        return self._engine

    def _start_engine(self, engine) -> None:
        options = self._options
        if options is None:
            engine.start(True, None, None, False)
            return
        engine.start(
            bool(options.headless),
            options.proxy,
            options.profile_dir,
            bool(options.ignore_https_errors),
        )

    def _snapshot(self, engine) -> dict:
        snap = engine.snapshot()
        if not isinstance(snap, dict):
            raise RuntimeError("snapshot was not an object")
        return snap

    def _send_result(self, request_id: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _send_error(self, request_id: Any, code: int, message: str) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    def _send(self, payload: dict) -> None:
        self._stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        flush = getattr(self._stdout, "flush", None)
        if callable(flush):
            flush()

    def _log(self, message: str) -> None:
        sys.stderr.write(f"agentbrowse mcp: {message}\n")
        sys.stderr.flush()

    @staticmethod
    def _tool_error(message: str) -> dict:
        return {"content": [{"type": "text", "text": message}], "isError": True}


def _require_selector(arguments: dict) -> str:
    selector = arguments.get("selector")
    if not isinstance(selector, str) or not selector.strip():
        raise ValueError("selector is required")
    return selector


def redact_cookies(cookies: Any) -> dict:
    """Keep name, domain, path, and expires. Drop values and every other field."""
    if not isinstance(cookies, list):
        raise TypeError("cookies must be a list")
    redacted = []
    for item in cookies:
        if not isinstance(item, dict):
            raise TypeError("cookie must be an object")
        redacted.append({key: item.get(key) for key in _COOKIE_FIELDS})
    return {"cookies": redacted}
