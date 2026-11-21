"""Profile directories and the non-secret state used to reopen a session."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_STATE_KEYS = ("profile", "url", "headless", "ignore_https_errors")


class ProfileError(RuntimeError):
    """A profile could not be created, listed, or deleted."""


class SessionError(RuntimeError):
    """Session state or cookie import/export failed."""


def agentbrowse_home() -> Path:
    raw = os.environ.get("AGENTBROWSE_HOME")
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path
    return Path.home() / ".agentbrowse"


def profiles_root() -> Path:
    return agentbrowse_home() / "profiles"


def state_dir() -> Path:
    return agentbrowse_home() / "state"


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not _PROFILE_NAME.fullmatch(name):
        raise ProfileError(
            f"invalid profile name {name!r}; "
            "use 1-64 characters: letters, digits, '.', '_' or '-'"
        )
    return name


def profile_path(name: str) -> Path:
    return profiles_root() / _check_name(name)


def profile_state_path(name: str) -> Path:
    return state_dir() / f"{_check_name(name)}.json"


def _current_pointer() -> Path:
    return state_dir() / "current"


def ensure_profile(name: str) -> Path:
    """Create `~/.agentbrowse/profiles/<name>` (or `$AGENTBROWSE_HOME`) and return it."""
    path = profile_path(name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_profiles() -> list[str]:
    root = profiles_root()
    if not root.is_dir():
        return []
    names = [entry.name for entry in root.iterdir() if entry.is_dir() and not entry.is_symlink()]
    return sorted(names)


def delete_profile(name: str, force: bool = False) -> None:
    """Delete a profile. Refuses unless `force` is true (the CLI `--force` flag)."""
    path = profile_path(name)
    if path.is_symlink():
        raise ProfileError(f"profile {name!r} is a symlink; refusing to delete it")
    if not path.exists():
        raise ProfileError(f"profile {name!r} does not exist")
    if not path.is_dir():
        raise ProfileError(f"profile {name!r} is not a directory")
    if not force:
        raise ProfileError(f"refusing to delete profile {name!r} without --force")

    root = profiles_root().resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ProfileError(f"profile {name!r} escapes the profiles directory")
    try:
        shutil.rmtree(resolved)
    except OSError as exc:
        raise ProfileError(f"could not delete profile {name!r}: {exc}") from None

    state_file = profile_state_path(name)
    state_file.unlink(missing_ok=True)
    pointer = _current_pointer()
    if pointer.is_file() and pointer.read_text(encoding="utf-8").strip() == name:
        pointer.unlink(missing_ok=True)


def redact_url(url: str) -> str:
    """Drop userinfo so a saved URL cannot carry credentials."""
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def write_state(*, profile: str, url: str, headless: bool, ignore_https_errors: bool) -> Path:
    """Persist the non-secret bits needed for `snap` to reopen this profile.

    Proxy specs, cookies, and URL userinfo are never written.
    """
    _check_name(profile)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise SessionError(f"refusing to store a non-http url: {url!r}")
    if not isinstance(headless, bool) or not isinstance(ignore_https_errors, bool):
        raise SessionError("headless and ignore_https_errors must be booleans")

    payload = {
        "profile": profile,
        "url": redact_url(url),
        "headless": headless,
        "ignore_https_errors": ignore_https_errors,
    }
    path = profile_state_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(payload, indent=2) + "\n")
    _atomic_write(_current_pointer(), profile + "\n")
    return path


def read_state(name: str | None = None) -> dict:
    """Load one profile's state. With no name, load the profile `open` last used."""
    if name is None:
        pointer = _current_pointer()
        if not pointer.is_file():
            raise SessionError("no session yet; run: agentbrowse open URL")
        name = pointer.read_text(encoding="utf-8").strip()
        if not name:
            raise SessionError("no session yet; run: agentbrowse open URL")
    name = _check_name(name)
    path = profile_state_path(name)
    if not path.is_file():
        raise SessionError(f"no session for profile {name!r}; run: agentbrowse open URL --profile {name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise SessionError(f"session file is corrupt: {path}") from None
    if not isinstance(data, dict) or any(key not in data for key in _STATE_KEYS):
        raise SessionError(f"session file is missing fields: {path}")
    if data["profile"] != name:
        raise SessionError(f"session file profile {data['profile']!r} does not match {name!r}")
    url = data["url"]
    parts = urlsplit(url) if isinstance(url, str) else None
    if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
        raise SessionError(f"session file has a non-http url: {url!r}")
    if not isinstance(data["headless"], bool) or not isinstance(data["ignore_https_errors"], bool):
        raise SessionError(f"session file has invalid flags: {path}")
    if parts.username or parts.password:
        raise SessionError(f"session file contains URL credentials: {path}")
    return {key: data[key] for key in _STATE_KEYS}


def export_cookies(context, dest: str | Path) -> Path:
    """Write Playwright storage state (cookies + origins) to a JSON file."""
    path = Path(dest)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(path))
    except OSError as exc:
        raise SessionError(f"cookie export failed: {exc}") from None
    except Exception as exc:
        raise SessionError(f"cookie export failed: {exc}") from None
    if not path.is_file():
        raise SessionError(f"cookie export did not create {path}")
    return path


def import_cookies(context, src: str | Path) -> int:
    """Load cookies from a Playwright storage-state JSON file into `context`.

    Returns the number of cookies added. Origin localStorage is left in the
    file; Playwright only applies that when a context is created.
    """
    data = _load_storage_state(src)
    cookies = data.get("cookies", [])
    if not cookies:
        return 0
    try:
        context.add_cookies(cookies)
    except Exception as exc:
        raise SessionError(f"cookie import failed: {exc}") from None
    return len(cookies)


def _load_storage_state(src: str | Path) -> dict:
    path = Path(src)
    if not path.is_file():
        raise SessionError(f"cookie file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise SessionError(f"cookie file is not valid JSON: {path}") from None
    if not isinstance(data, dict):
        raise SessionError(f"cookie file is not a playwright storage state: {path}")
    cookies = data.get("cookies", [])
    if not isinstance(cookies, list) or not all(isinstance(item, dict) for item in cookies):
        raise SessionError(f"cookie file has an invalid cookies list: {path}")
    return data


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
