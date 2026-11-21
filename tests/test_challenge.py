import pytest

from agentbrowse.challenge import detect_html
from agentbrowse.snapshot import build_snapshot

_FIXTURES = [
    (
        "cloudflare",
        "high",
        "https://example.com/",
        "Just a moment...",
        "<html><head><title>Just a moment...</title></head>"
        "<body><div id=\"cf-chl\"></div><script>window._cf_chl_opt = {}</script></body></html>",
    ),
    (
        "captcha",
        "high",
        "https://example.com/login",
        "Sign in",
        "<html><body><div class=\"g-recaptcha\"></div>"
        "<script src=\"https://www.google.com/recaptcha/api.js\"></script></body></html>",
    ),
    (
        "aws_waf",
        "high",
        "https://example.com/",
        "Human check",
        "<html><body><script>window.gokuProps = {key: \"abc\"};</script></body></html>",
    ),
    (
        "datadome",
        "high",
        "https://example.com/",
        "Please wait",
        "<html><body><script src=\"https://geo.captcha-delivery.com/captcha/?t=1\"></script>"
        "<p>datadome</p></body></html>",
    ),
    (
        "access_denied",
        "high",
        "https://example.com/secret",
        "403 Forbidden",
        "<html><body><h1>403 Forbidden</h1><p>Access Denied</p></body></html>",
    ),
    (
        "rate_limited",
        "high",
        "https://example.com/api",
        "429 Too Many Requests",
        "<html><body><h1>429 Too Many Requests</h1></body></html>",
    ),
]


@pytest.mark.parametrize(("kind", "confidence", "url", "title", "html"), _FIXTURES)
def test_detect_fixture(kind, confidence, url, title, html):
    found = detect_html(url, title, html)
    assert found is not None
    assert found.kind == kind
    assert found.confidence == confidence
    assert found.confidence in {"low", "medium", "high"}
    assert found.evidence
    assert "--headed --pause-for-human" in found.suggested_action


def test_clean_page_is_none():
    html = "<html><head><title>Example Domain</title></head><body><p>Hello</p></body></html>"
    assert detect_html("https://example.com/", "Example Domain", html) is None


def test_title_only_cloudflare_is_medium():
    found = detect_html("https://example.com/", "Just a moment...", "<html><body>please wait</body></html>")
    assert found is not None
    assert found.kind == "cloudflare"
    assert found.confidence == "medium"


def test_generic_container_loses_to_a_captcha_widget():
    html = '<div id="captcha-container"></div><div class="h-captcha"></div>'
    found = detect_html("https://example.com/", "Check", html)
    assert found is not None
    assert found.kind == "captcha"
    assert found.confidence == "high"


def test_snapshot_records_the_challenge():
    snap = build_snapshot(
        url="https://cdn.example/",
        title="Denied",
        text="nope",
        elements=[],
        console_errors=[],
        html="<h1>Access Denied</h1>",
        status=403,
    )
    assert snap["challenge"]["kind"] == "access_denied"
    assert snap["challenge"]["confidence"] == "high"


def test_snapshot_without_html_stays_clean():
    snap = build_snapshot(
        url="http://127.0.0.1/",
        title="Hi",
        text="abc",
        elements=[],
        console_errors=[],
    )
    assert snap["challenge"] is None
