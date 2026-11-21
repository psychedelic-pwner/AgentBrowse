import json

import pytest

from agentbrowse.session import (
    ProfileError,
    SessionError,
    delete_profile,
    ensure_profile,
    export_cookies,
    import_cookies,
    list_profiles,
    read_state,
    write_state,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBROWSE_HOME", str(tmp_path))
    return tmp_path


def test_profile_create_list_delete(home):
    assert list_profiles() == []
    created = ensure_profile("burp")
    assert created == home / "profiles" / "burp"
    assert created.is_dir()
    assert ensure_profile("burp") == created
    assert list_profiles() == ["burp"]

    with pytest.raises(ProfileError, match="--force"):
        delete_profile("burp")
    assert created.is_dir()

    delete_profile("burp", force=True)
    assert not created.exists()
    assert list_profiles() == []


def test_profile_name_is_checked(home):
    for name in ("", "../etc", "a/b", "has space", ".", ".."):
        with pytest.raises(ProfileError, match="invalid profile name"):
            ensure_profile(name)


def test_delete_missing_profile(home):
    with pytest.raises(ProfileError, match="does not exist"):
        delete_profile("missing", force=True)


def test_state_is_per_profile_and_stores_no_secrets(home):
    write_state(
        profile="default",
        url="https://user:secret@example.test/path?q=1",
        headless=True,
        ignore_https_errors=False,
    )
    write_state(
        profile="burp",
        url="http://127.0.0.1:9/form",
        headless=False,
        ignore_https_errors=True,
    )
    raw_default = (home / "state" / "default.json").read_text(encoding="utf-8")
    assert "secret" not in raw_default
    assert "password" not in raw_default
    assert json.loads(raw_default)["url"] == "https://example.test/path?q=1"

    current = read_state()
    assert current == {
        "profile": "burp",
        "url": "http://127.0.0.1:9/form",
        "headless": False,
        "ignore_https_errors": True,
    }
    assert (home / "state" / "current").read_text(encoding="utf-8").strip() == "burp"
    assert read_state("default")["profile"] == "default"


def test_read_state_without_session(home):
    with pytest.raises(SessionError, match="no session yet"):
        read_state()


def test_delete_profile_drops_its_state(home):
    ensure_profile("box")
    write_state(
        profile="box",
        url="http://127.0.0.1/",
        headless=True,
        ignore_https_errors=False,
    )
    delete_profile("box", force=True)
    with pytest.raises(SessionError, match="no session"):
        read_state()
    assert not (home / "state" / "box.json").exists()


class _Context:
    def __init__(self):
        self.added = None

    def storage_state(self, path: str):
        from pathlib import Path

        Path(path).write_text(
            json.dumps(
                {
                    "cookies": [
                        {"name": "sid", "value": "1", "domain": "127.0.0.1", "path": "/"},
                    ],
                    "origins": [],
                }
            ),
            encoding="utf-8",
        )

    def add_cookies(self, cookies):
        self.added = cookies


def test_cookie_export_import_roundtrip(tmp_path):
    dest = tmp_path / "cookies.json"
    export_cookies(_Context(), dest)
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert "cookies" in loaded
    target = _Context()
    assert import_cookies(target, dest) == 1
    assert target.added[0]["name"] == "sid"


def test_cookie_import_rejects_bad_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(SessionError, match="not valid JSON"):
        import_cookies(object(), path)
