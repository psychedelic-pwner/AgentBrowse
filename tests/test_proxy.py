import pytest

from agentbrowse.proxy import ProxyError, parse_proxy


def test_parse_http_url():
    assert parse_proxy("http://127.0.0.1:8080") == {"server": "http://127.0.0.1:8080"}


def test_parse_host_port_defaults_to_http():
    assert parse_proxy("127.0.0.1:8080") == {"server": "http://127.0.0.1:8080"}


def test_parse_socks5_userinfo_is_split_out():
    assert parse_proxy("socks5://user:pass@host:1080") == {
        "server": "socks5://host:1080",
        "username": "user",
        "password": "pass",
    }


def test_parse_proxy_rejects_bad_input():
    for spec in ("", "   ", "nope", "http://127.0.0.1", "ftp://127.0.0.1:21", "http://:8080"):
        with pytest.raises(ProxyError, match="invalid proxy"):
            parse_proxy(spec)
