import pytest

from agentbrowse.engine import (
    BrowserOptions,
    EngineError,
    EngineUnavailable,
    PatchrightEngine,
    PlaywrightEngine,
    create_engine,
    require_http_url,
)
from agentbrowse.snapshot import SnapshotError, build_snapshot


def test_insecure_is_a_context_option_not_a_launch_flag():
    options = BrowserOptions(
        headless=True,
        proxy={"server": "http://127.0.0.1:8080"},
        profile_dir=None,
        ignore_https_errors=True,
    )
    assert options.context_kwargs() == {"ignore_https_errors": True}
    assert "ignore_https_errors" not in options.launch_kwargs()
    assert options.launch_kwargs()["proxy"]["server"] == "http://127.0.0.1:8080"
    persistent = options.persistent_kwargs()
    assert persistent["ignore_https_errors"] is True
    assert persistent["headless"] is True
    assert persistent["proxy"]["server"] == "http://127.0.0.1:8080"


def test_start_passes_insecure_through_without_launching(monkeypatch):
    launched = {}

    def fake_launch(self, options):
        launched["options"] = options

    monkeypatch.setattr(PlaywrightEngine, "_launch", fake_launch)
    engine = PlaywrightEngine()
    engine.start(
        headless=True,
        proxy=None,
        profile_dir=None,
        ignore_https_errors=True,
    )
    assert launched["options"].ignore_https_errors is True
    assert launched["options"].context_kwargs()["ignore_https_errors"] is True
    assert engine.options is launched["options"]


def test_create_engine_names():
    assert isinstance(create_engine(), PlaywrightEngine)
    assert isinstance(create_engine("chromium"), PlaywrightEngine)
    stealth = create_engine("stealth")
    assert isinstance(stealth, PatchrightEngine)
    with pytest.raises(EngineUnavailable, match="stealth"):
        create_engine("camoufox")


def test_stealth_missing_raises_install_hint(monkeypatch):
    monkeypatch.setattr(PatchrightEngine, "available", classmethod(lambda cls: False))
    engine = create_engine("stealth")
    with pytest.raises(EngineUnavailable, match="pip install patchright"):
        engine.start(True, None, None, False)


def test_patchright_import_does_not_crash():
    assert isinstance(PatchrightEngine.available(), bool)


def test_require_http_url():
    assert require_http_url(" http://127.0.0.1/ ") == "http://127.0.0.1/"
    with pytest.raises(EngineError, match="only http"):
        require_http_url("javascript:alert(1)")
    with pytest.raises(EngineError, match="only http"):
        require_http_url("file:///etc/passwd")


def test_actions_fail_clearly_before_start():
    engine = PlaywrightEngine()
    with pytest.raises(EngineError, match="not started"):
        engine.goto("http://127.0.0.1/")
    with pytest.raises(EngineError, match="not started"):
        engine.snapshot()
    with pytest.raises(EngineError, match="not started"):
        engine.cookies()
    with pytest.raises(EngineError, match="not started"):
        engine.press("#q", "Enter")


def test_build_snapshot_truncates_and_keeps_phase1_shape():
    snap = build_snapshot(
        url="http://127.0.0.1/",
        title="Hi",
        text="abcdef",
        elements=[
            {"role": "button", "name": "Go", "selector": "#go"},
            {"role": "", "name": "x", "selector": "#no"},
            "junk",
        ],
        console_errors=[" bad ", ""],
        text_limit=3,
    )
    assert snap == {
        "url": "http://127.0.0.1/",
        "title": "Hi",
        "challenge": None,
        "text": "abc",
        "elements": [{"role": "button", "name": "Go", "selector": "#go"}],
        "console_errors": ["bad"],
    }
    with pytest.raises(SnapshotError):
        build_snapshot(url="", title="", text="", elements=[], console_errors=[], text_limit=-1)
