"""Normalise proxy specs into Playwright's proxy dictionary."""

from __future__ import annotations

from urllib.parse import unquote, urlparse

_SCHEMES = {"http", "https", "socks4", "socks5"}


class ProxyError(ValueError):
    """A proxy spec could not be parsed."""


def parse_proxy(spec: str) -> dict:
    """Return Playwright's proxy dict for `spec`.

    Accepts `http://127.0.0.1:8080`, `127.0.0.1:8080`, and
    `socks5://user:pass@host:1080` (userinfo is split out of the server URL).
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ProxyError(
            "invalid proxy: empty value. "
            "expected 'http://host:port', 'host:port', or 'socks5://user:pass@host:port'"
        )
    original = spec.strip()
    candidate = original if "://" in original else f"http://{original}"
    try:
        parsed = urlparse(candidate)
    except ValueError as exc:
        raise ProxyError(_invalid(original, str(exc))) from None

    scheme = (parsed.scheme or "").lower()
    if scheme not in _SCHEMES:
        raise ProxyError(_invalid(original, f"unsupported scheme {parsed.scheme!r}"))
    if parsed.path not in ("", "/"):
        raise ProxyError(_invalid(original, "proxy spec must not include a path"))
    if parsed.query or parsed.fragment:
        raise ProxyError(_invalid(original, "proxy spec must not include a query or fragment"))

    try:
        port = parsed.port
    except ValueError as exc:
        raise ProxyError(_invalid(original, str(exc))) from None
    host = parsed.hostname
    if not host or port is None:
        raise ProxyError(_invalid(original, "host and port are required"))
    if not 1 <= port <= 65535:
        raise ProxyError(_invalid(original, f"port {port} is out of range"))

    host_fmt = f"[{host}]" if ":" in host else host
    proxy: dict[str, str] = {"server": f"{scheme}://{host_fmt}:{port}"}
    if parsed.username is not None:
        proxy["username"] = unquote(parsed.username)
    if parsed.password is not None:
        proxy["password"] = unquote(parsed.password)
    return proxy


def _invalid(original: str, reason: str) -> str:
    return (
        f"invalid proxy {original!r}: {reason}. "
        "expected 'http://host:port', 'host:port', or 'socks5://user:pass@host:port'"
    )
