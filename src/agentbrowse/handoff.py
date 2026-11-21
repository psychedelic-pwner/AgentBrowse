"""Tell the operator a challenge is present. This module never solves one."""

from __future__ import annotations

import time

from agentbrowse.challenge import Challenge

HEADLESS_HANDOFF = "run again with --headed --pause-for-human"
DEFAULT_HANDOFF_TIMEOUT_S = 120.0


def plan_handoff(challenge: Challenge, headless: bool) -> str:
    """Operator instruction. Headless sessions must be re-run visibly."""
    kind = _kind(challenge)
    if headless:
        return f"{kind}: {HEADLESS_HANDOFF}"
    return (
        f"{kind} challenge is on screen. Clear it yourself in the browser window. "
        "This tool does not solve challenges. "
        f"{HEADLESS_HANDOFF}"
    )


def wait_for_clear(poll_fn, timeout_s: float, interval_s: float = 2.0) -> bool:
    """Call `poll_fn` until it is true or `timeout_s` elapses.

    Returns True when the poll says the page is clear. Returns False on timeout.
    `poll_fn` is supplied by the caller (the CLI reads the live page). This
    function does not touch a browser and does not solve anything.
    """
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    pause = max(0.0, float(interval_s))
    while True:
        if poll_fn():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(pause, remaining))


def _kind(challenge) -> str:
    if isinstance(challenge, Challenge):
        return challenge.kind or "challenge"
    if isinstance(challenge, dict):
        return str(challenge.get("kind") or "challenge")
    return "challenge"
