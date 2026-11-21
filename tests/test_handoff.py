import time

from agentbrowse.challenge import Challenge
from agentbrowse.handoff import plan_handoff, wait_for_clear


def _challenge() -> Challenge:
    return Challenge(
        kind="captcha",
        confidence="high",
        evidence="html:g-recaptcha",
        suggested_action="run again with --headed --pause-for-human",
    )


def test_plan_mentions_headed_pause_for_human():
    text = plan_handoff(_challenge(), headless=True)
    assert "--headed --pause-for-human" in text
    assert text.endswith("run again with --headed --pause-for-human")


def test_headed_plan_also_names_the_flag_and_does_not_offer_a_solver():
    text = plan_handoff(_challenge(), headless=False)
    assert "--headed --pause-for-human" in text
    assert "does not solve" in text


def test_wait_for_clear_times_out():
    started = time.monotonic()
    cleared = wait_for_clear(lambda: False, timeout_s=0.2, interval_s=0.05)
    elapsed = time.monotonic() - started
    assert cleared is False
    assert elapsed < 1.0


def test_wait_for_clear_returns_when_the_poll_succeeds():
    calls = {"n": 0}

    def poll() -> bool:
        calls["n"] += 1
        return calls["n"] >= 2

    assert wait_for_clear(poll, timeout_s=1.0, interval_s=0.01) is True
    assert calls["n"] == 2
