"""Pick a proxy URL from a user-supplied country pool. Nothing is bundled."""

from __future__ import annotations

import json
from pathlib import Path


class GeoProxyUnavailable(RuntimeError):
    """The pool has no proxy for the requested country."""


def load_pool(path: str | Path) -> list[dict]:
    """Load `[{country, url}, ...]` from a JSON or simple YAML file."""
    file = Path(path)
    if not file.is_file():
        raise GeoProxyUnavailable(f"proxy pool not found: {file}")
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise GeoProxyUnavailable(f"could not read proxy pool {file}: {exc}") from None
    stripped = text.lstrip("\ufeff").lstrip()
    if not stripped:
        raise GeoProxyUnavailable(f"proxy pool is empty: {file}")
    if stripped[0] in "[{":
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise GeoProxyUnavailable(f"proxy pool is not valid JSON: {file}: {exc}") from None
    else:
        data = _parse_simple_yaml(text, str(file))
    return _normalize(data, str(file))


def pick(pool, country: str) -> str:
    """Return the proxy URL for `country`.

    Raises GeoProxyUnavailable and names the countries that are actually in the pool.
    """
    if not isinstance(country, str) or not country.strip():
        raise GeoProxyUnavailable(
            f"no proxy for country {country!r}. available countries: {_listing(pool)}"
        )
    wanted = country.strip().upper()
    match: str | None = None
    for item in _entries(pool):
        code = _code(item)
        url = _url(item)
        if code == wanted and url and match is None:
            match = url
    if match is not None:
        return match
    raise GeoProxyUnavailable(
        f"no proxy for country {wanted}. available countries: {_listing(pool)}"
    )


def _listing(pool) -> str:
    codes = []
    for item in _entries(pool):
        code = _code(item)
        if code and code not in codes:
            codes.append(code)
    if not codes:
        return "(none)"
    return ", ".join(sorted(codes))


def _entries(pool) -> list:
    if pool is None:
        return []
    if isinstance(pool, dict):
        return [pool]
    try:
        return list(pool)
    except TypeError:
        return []


def _code(item) -> str:
    if not isinstance(item, dict):
        return ""
    raw = _field(item, "country")
    return raw.upper() if raw else ""


def _url(item) -> str:
    if not isinstance(item, dict):
        return ""
    return _field(item, "url") or ""


def _field(item: dict, name: str) -> str | None:
    for key, value in item.items():
        if str(key).lower() != name:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None
    return None


def _normalize(data, origin: str) -> list[dict]:
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise GeoProxyUnavailable(f"proxy pool must be a list of {{country, url}}: {origin}")
    pool = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise GeoProxyUnavailable(f"proxy pool entry {index} is not a mapping: {origin}")
        country = _field(item, "country")
        url = _field(item, "url")
        if not country:
            raise GeoProxyUnavailable(f"proxy pool entry {index} is missing country: {origin}")
        if not url:
            raise GeoProxyUnavailable(f"proxy pool entry {index} is missing url: {origin}")
        pool.append({"country": country, "url": url})
    return pool


def _parse_simple_yaml(text: str, origin: str) -> list[dict]:
    """Parse a list of flat mappings. Enough for `{country, url}` pools. Not a YAML library."""
    entries: list[dict] = []
    current: dict | None = None
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw).rstrip()
        if not line.strip() or line.strip() in ("---", "..."):
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current:
                entries.append(current)
            current = {}
            rest = stripped[2:].strip()
            if rest:
                _store_kv(current, rest, origin, line_no)
            continue
        if current is not None and ":" in stripped:
            _store_kv(current, stripped, origin, line_no)
            continue
        raise GeoProxyUnavailable(f"{origin}:{line_no}: unsupported proxy pool syntax")
    if current:
        entries.append(current)
    if not entries:
        raise GeoProxyUnavailable(f"proxy pool has no entries: {origin}")
    return entries


def _store_kv(current: dict, text: str, origin: str, line_no: int) -> None:
    if ":" not in text:
        raise GeoProxyUnavailable(f"{origin}:{line_no}: expected key: value")
    key, value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise GeoProxyUnavailable(f"{origin}:{line_no}: expected key: value")
    current[key] = _scalar(value)


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:index]
    return line
