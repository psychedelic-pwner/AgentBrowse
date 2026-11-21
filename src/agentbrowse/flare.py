"""Optional client for a FlareSolverr the operator already runs locally.

No credentials are bundled. If the service is down, `solve` returns an error
result instead of raising, and the caller keeps going.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_TIMEOUT_S = 10


@dataclass(frozen=True)
class FlareResult:
    cookies: list
    user_agent: str
    status: str
    raw: dict = field(default_factory=dict)


class FlareClient:
    """Talk to `base_url` with the stdlib. `base_url` is the server root, not a secret."""

    def __init__(self, base_url: str) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")

    def available(self) -> bool:
        """True when `GET /health` returns a 2xx response."""
        try:
            status, _body = _request(self._url("/health"), method="GET")
        except Exception:
            return False
        return 200 <= status < 300

    def solve(self, url: str) -> FlareResult:
        """POST `request.get` to `/v1`. Unreachable hosts become `status="error"`."""
        payload = json.dumps(
            {"cmd": "request.get", "url": url, "maxTimeout": _TIMEOUT_S * 1000}
        ).encode("utf-8")
        try:
            status, body = _request(
                self._url("/v1"),
                method="POST",
                data=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        except Exception as exc:
            return _error(exc)
        parsed = _parse_json(body)
        if not isinstance(parsed, dict):
            return FlareResult(
                cookies=[],
                user_agent="",
                status="error",
                raw={"error": "response was not a JSON object", "http_status": status},
            )
        if str(parsed.get("status") or "").lower() != "ok":
            return FlareResult(cookies=[], user_agent="", status="error", raw=parsed)
        solution = parsed.get("solution")
        if not isinstance(solution, dict):
            solution = {}
        cookies = solution.get("cookies")
        if not isinstance(cookies, list):
            cookies = []
        user_agent = solution.get("userAgent") or solution.get("user_agent") or ""
        return FlareResult(
            cookies=list(cookies),
            user_agent=str(user_agent),
            status="ok",
            raw=parsed,
        )

    def _url(self, path: str) -> str:
        if not self.base_url:
            raise URLError("flaresolverr url is empty")
        return self.base_url + path


def _request(url: str, *, method: str, data: bytes | None = None, headers: dict | None = None):
    request = Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urlopen(request, timeout=_TIMEOUT_S) as response:
            return response.status, response.read()
    except HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        # A JSON error body is still a result the caller can inspect. Transport
        # failures raise out of here and `solve` turns them into an error result.
        if body:
            parsed = _parse_json(body)
            if isinstance(parsed, dict):
                raise _FlareHTTP(parsed) from None
        raise


class _FlareHTTP(Exception):
    def __init__(self, payload: dict) -> None:
        super().__init__("flaresolverr http error")
        self.payload = payload


def _parse_json(body: bytes):
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except (UnicodeError, json.JSONDecodeError, AttributeError):
        return None


def _error(exc: Exception) -> FlareResult:
    if isinstance(exc, _FlareHTTP):
        return FlareResult(cookies=[], user_agent="", status="error", raw=exc.payload)
    return FlareResult(
        cookies=[],
        user_agent="",
        status="error",
        raw={"error": f"{type(exc).__name__}: {exc}"},
    )
