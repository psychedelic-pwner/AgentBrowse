"""Detect anti-bot interstitials from URL, title, and HTML. Never solves them."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

# Equal confidence: page-level vendor blocks outrank an embedded widget.
_KIND_PRIORITY = {
    "cloudflare": 0,
    "datadome": 1,
    "aws_waf": 2,
    "captcha": 3,
    "rate_limited": 4,
    "access_denied": 5,
}

_HANDOFF_ACTION = "run again with --headed --pause-for-human"

_CF_HTML = (
    "cf-chl",
    "cf_chl_opt",
    "__cf_chl",
    "challenge-platform",
    'id="challenge-running"',
    "id='challenge-running'",
)
_CAPTCHA = (
    "g-recaptcha",
    "h-captcha",
    "hcaptcha.com",
    "cf-turnstile",
    "recaptcha/api.js",
)
_AWS_HIGH = ("gokuprops", "awswaf")
_STATUS_403 = re.compile(r"\b403\b")
_STATUS_429 = re.compile(r"\b429\b")
_BLOCKED = re.compile(r"\bblocked\b")


@dataclass(frozen=True)
class Challenge:
    """One detected interstitial. `confidence` is `low`, `medium`, or `high`."""

    kind: str
    confidence: str
    evidence: str
    suggested_action: str

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "suggested_action": self.suggested_action,
        }


def detect_html(url, title, html) -> Challenge | None:
    """Return the highest-confidence challenge, or None when the page looks clean.

    String-only. No browser, no network, no solver.
    """
    url_s = "" if url is None else str(url)
    title_s = "" if title is None else str(title)
    html_s = "" if html is None else str(html)
    title_l = title_s.lower()
    html_l = html_s.lower()
    blob_l = f"{url_s.lower()}\n{title_l}\n{html_l}"

    found: list[Challenge] = []
    for detector in (
        _cloudflare,
        _captcha,
        _aws_waf,
        _datadome,
        _access_denied,
        _rate_limited,
    ):
        challenge = detector(title_l, html_l, blob_l)
        if challenge is not None:
            found.append(challenge)
    if not found:
        return None
    found.sort(key=lambda item: (-_CONFIDENCE_RANK[item.confidence], _KIND_PRIORITY[item.kind]))
    return found[0]


def _cloudflare(title_l: str, html_l: str, blob_l: str) -> Challenge | None:
    del blob_l
    markers: list[str] = []
    if "just a moment" in title_l:
        markers.append("title:Just a moment")
    if "attention required" in title_l:
        markers.append("title:Attention Required")
    html_hits = _present(html_l, _CF_HTML)
    # Title alone is suggestive. A challenge token in the HTML is decisive.
    if html_hits:
        confidence = "high"
    elif markers:
        confidence = "medium"
    else:
        return None
    evidence = _join(markers + [f"html:{item}" for item in html_hits])
    return _make("cloudflare", confidence, evidence)


def _captcha(title_l: str, html_l: str, blob_l: str) -> Challenge | None:
    del title_l
    hits = _present(html_l, _CAPTCHA) or _present(blob_l, _CAPTCHA)
    if not hits:
        return None
    return _make("captcha", "high", _join(f"html:{item}" for item in hits))


def _aws_waf(title_l: str, html_l: str, blob_l: str) -> Challenge | None:
    del title_l
    high = _present(html_l, _AWS_HIGH) or _present(blob_l, _AWS_HIGH)
    low = _present(html_l, ("captcha-container",)) or _present(blob_l, ("captcha-container",))
    if high:
        return _make("aws_waf", "high", _join(f"html:{item}" for item in high))
    if low:
        return _make("aws_waf", "low", _join(f"html:{item}" for item in low))
    return None


def _datadome(title_l: str, html_l: str, blob_l: str) -> Challenge | None:
    del title_l
    hits = _present(blob_l, ("datadome", "geo.captcha-delivery.com"))
    if not hits:
        return None
    # Prefer the evidence that was actually in the HTML when both the URL and body match.
    html_hits = _present(html_l, ("datadome", "geo.captcha-delivery.com"))
    shown = html_hits or hits
    return _make("datadome", "high", _join(f"html:{item}" for item in shown))


def _access_denied(title_l: str, html_l: str, blob_l: str) -> Challenge | None:
    del title_l, html_l
    if not _STATUS_403.search(blob_l):
        return None
    denied = "access denied" in blob_l
    blocked = _BLOCKED.search(blob_l) is not None
    if not denied and not blocked:
        return None
    markers = ["403"]
    if denied:
        markers.append("Access Denied")
    if blocked:
        markers.append("blocked")
    confidence = "high" if denied else "medium"
    return _make("access_denied", confidence, _join(markers))


def _rate_limited(title_l: str, html_l: str, blob_l: str) -> Challenge | None:
    del title_l, html_l
    markers: list[str] = []
    if _STATUS_429.search(blob_l):
        markers.append("429")
    if "too many requests" in blob_l:
        markers.append("Too Many Requests")
    if not markers:
        return None
    return _make("rate_limited", "high", _join(markers))


def _present(haystack: str, needles: tuple[str, ...]) -> list[str]:
    found = []
    for needle in needles:
        if needle.lower() in haystack and needle not in found:
            found.append(needle)
    return found


def _join(markers) -> str:
    return ", ".join(str(item) for item in markers)


def _make(kind: str, confidence: str, evidence: str) -> Challenge:
    return Challenge(
        kind=kind,
        confidence=confidence,
        evidence=evidence,
        suggested_action=_HANDOFF_ACTION,
    )
