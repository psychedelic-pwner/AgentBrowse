import json

import pytest

from agentbrowse.cli import build_browser_options, build_parser, main
from agentbrowse.engine import EngineError
from agentbrowse.session import ensure_profile


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBROWSE_HOME", str(tmp_path))
    return tmp_path


def test_insecure_flag_is_passed_through_on_the_options_object():
    parser = build_parser()
    args = parser.parse_args(["open", "https://target.test/", "--insecure", "--profile", "burp"])
    options = build_browser_options(args, profile_dir="/tmp/agentbrowse-burp")
    assert options.ignore_https_errors is True
    assert options.headless is True
    assert options.profile_dir == "/tmp/agentbrowse-burp"
    assert options.context_kwargs()["ignore_https_errors"] is True
    assert options.persistent_kwargs()["ignore_https_errors"] is True
    assert "ignore_https_errors" not in options.launch_kwargs()

    headed = parser.parse_args(
        ["open", "http://127.0.0.1/", "--headed", "--proxy", "127.0.0.1:8080", "--insecure"]
    )
    headed_options = build_browser_options(headed, profile_dir=None)
    assert headed_options.headless is False
    assert headed_options.ignore_https_errors is True
    assert headed_options.proxy == {"server": "http://127.0.0.1:8080"}


class _FakeEngine:
    def __init__(self):
        self.calls = []
        self.closed = False

    def start(self, headless, proxy, profile_dir, ignore_https_errors):
        self.calls.append(("start", headless, proxy, profile_dir, ignore_https_errors))

    def goto(self, url):
        self.calls.append(("goto", url))

    def snapshot(self):
        url = next(item[1] for item in self.calls if item[0] == "goto")
        return {
            "url": url,
            "title": "Fake",
            "challenge": None,
            "text": "hello",
            "elements": [{"role": "button", "name": "Go", "selector": "#go"}],
            "console_errors": [],
        }

    def close(self):
        self.closed = True


@pytest.fixture
def fake_engine(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr("agentbrowse.cli.create_engine", lambda name="chromium": engine)
    return engine


def test_open_passes_insecure_and_writes_state_without_proxy_secrets(home, fake_engine, capsys):
    code = main(
        [
            "open",
            "http://127.0.0.1:9/form",
            "--insecure",
            "--proxy",
            "socks5://user:pass@127.0.0.1:1080",
            "--json",
        ]
    )
    assert code == 0
    assert fake_engine.closed is True
    kind, headless, proxy, profile_dir, insecure = fake_engine.calls[0]
    assert kind == "start"
    assert headless is True
    assert insecure is True
    assert proxy == {
        "server": "socks5://127.0.0.1:1080",
        "username": "user",
        "password": "pass",
    }
    assert profile_dir == str(home / "profiles" / "default")
    assert fake_engine.calls[1] == ("goto", "http://127.0.0.1:9/form")

    state_file = home / "state" / "default.json"
    raw = state_file.read_text(encoding="utf-8")
    assert "pass" not in raw
    assert "socks5" not in raw
    state = json.loads(raw)
    assert state["ignore_https_errors"] is True
    assert state["url"] == "http://127.0.0.1:9/form"
    printed = json.loads(capsys.readouterr().out)
    assert printed["title"] == "Fake"
    assert printed["challenge"] is None


def test_snap_reopens_saved_profile(home, fake_engine):
    code = main(["open", "http://127.0.0.1:9/", "--headed", "--insecure", "--profile", "burp"])
    assert code == 0
    fake_engine.calls.clear()
    code = main(["snap"])
    assert code == 0
    kind, headless, proxy, profile_dir, insecure = fake_engine.calls[0]
    assert kind == "start"
    assert headless is False
    assert proxy is None
    assert insecure is True
    assert profile_dir == str(home / "profiles" / "burp")
    assert fake_engine.calls[1] == ("goto", "http://127.0.0.1:9/")


def test_open_bad_proxy_and_url_do_not_traceback(home, capsys):
    assert main(["open", "http://127.0.0.1/", "--proxy", "bogus"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "Traceback" not in err

    assert main(["open", "javascript:alert(1)"]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "only http" in err


def test_profiles_cli(home, capsys):
    assert main(["profiles", "list"]) == 0
    assert capsys.readouterr().out == ""
    ensure_profile("box")
    assert main(["profiles", "list"]) == 0
    assert capsys.readouterr().out.strip() == "box"
    assert main(["profiles", "delete", "box"]) == 2
    err = capsys.readouterr().err
    assert "--force" in err
    assert "Traceback" not in err
    assert (home / "profiles" / "box").is_dir()
    assert main(["profiles", "delete", "box", "--force"]) == 0
    assert "deleted profile box" in capsys.readouterr().out
    assert not (home / "profiles" / "box").exists()


def test_snap_without_session(home, capsys):
    assert main(["snap"]) == 2
    err = capsys.readouterr().err
    assert "no session yet" in err
    assert "Traceback" not in err


def test_headless_challenge_exits_4(home, monkeypatch, capsys):
    class _Challenged(_FakeEngine):
        def snapshot(self):
            snap = super().snapshot()
            snap["challenge"] = {
                "kind": "cloudflare",
                "confidence": "high",
                "evidence": "html:cf-chl",
                "suggested_action": "run again with --headed --pause-for-human",
            }
            return snap

    monkeypatch.setattr("agentbrowse.cli.create_engine", lambda name="chromium": _Challenged())
    code = main(["open", "http://127.0.0.1:9/blocked"])
    assert code == 4
    captured = capsys.readouterr()
    assert "run again with --headed --pause-for-human" in captured.err
    assert "Traceback" not in captured.err
    assert "cloudflare" in captured.out


def test_challenge_report_prints_detection_only(home, monkeypatch, capsys):
    class _Challenged(_FakeEngine):
        def snapshot(self):
            snap = super().snapshot()
            snap["challenge"] = {
                "kind": "datadome",
                "confidence": "high",
                "evidence": "html:datadome",
                "suggested_action": "run again with --headed --pause-for-human",
            }
            return snap

    monkeypatch.setattr("agentbrowse.cli.create_engine", lambda name="chromium": _Challenged())
    code = main(["open", "http://127.0.0.1:9/", "--challenge-report"])
    assert code == 4
    out = capsys.readouterr().out
    assert "kind: datadome" in out
    assert "hello" not in out


def test_geo_proxy_is_passed_to_the_engine(home, fake_engine, tmp_path):
    pool = tmp_path / "pool.yaml"
    pool.write_text(
        "- country: US\n  url: http://127.0.0.1:8080\n- country: DE\n  url: http://127.0.0.1:8081\n",
        encoding="utf-8",
    )
    code = main(["open", "http://127.0.0.1:9/", "--geo", "us", "--proxy-pool", str(pool)])
    assert code == 0
    _kind, _headless, proxy, _profile_dir, _insecure = fake_engine.calls[0]
    assert proxy == {"server": "http://127.0.0.1:8080"}


def test_missing_geo_country_is_a_clean_error(home, capsys, tmp_path):
    pool = tmp_path / "pool.json"
    pool.write_text(
        '[{"country": "US", "url": "http://127.0.0.1:8080"}]',
        encoding="utf-8",
    )
    code = main(["open", "http://127.0.0.1/", "--geo", "BR", "--proxy-pool", str(pool)])
    assert code == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "US" in err
    assert err.startswith("error:")


def test_unreachable_flare_does_not_crash(home, fake_engine, capsys):
    code = main(["open", "http://127.0.0.1:9/", "--flare-solverr", "http://127.0.0.1:1"])
    assert code == 0
    assert fake_engine.closed is True
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "flaresolverr" in err


def test_engine_failure_is_a_clean_message(home, monkeypatch, capsys):
    class _Boom:
        def start(self, *args, **kwargs):
            raise EngineError("chromium executable is missing")

        def close(self):
            return None

    monkeypatch.setattr("agentbrowse.cli.create_engine", lambda name="chromium": _Boom())
    assert main(["open", "http://127.0.0.1/"]) == 2
    err = capsys.readouterr().err
    assert "chromium executable is missing" in err
    assert "Traceback" not in err
