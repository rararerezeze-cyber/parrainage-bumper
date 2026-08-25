"""ReferralCode.tv isolated bump: legitimate behaviour, no bypass, no noise.

Covers the six required runtime situations (normal login, Turnstile challenge,
quota available, quota exhausted, control absent, network failure) plus the
non-interference guarantee with the two healthy bumpers.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import bumper
from lib.rctv_bump import (
    EXPECTED_EXTERNAL_BLOCKER,
    RCTV_AUTH_BLOCKED_CHALLENGE,
    RCTV_BOOST_NOT_VERIFIED,
    RCTV_BOOSTED,
    RCTV_CONTROL_ABSENT,
    RCTV_FAILED,
    RCTV_QUOTA_EXHAUSTED,
    classify_cycle,
    is_expected_external_blocker,
    parse_boost_quota,
    parse_boosted_today,
    quota_exhausted,
    verify_boost,
)

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


# -- quota parsing -------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("You can click 0 more times today", 0),
        ("you can click 3 more times", 3),
        ("CAN CLICK 5", 5),
        ("nothing relevant here", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_boost_quota(text, expected):
    assert parse_boost_quota(text) == expected


def test_unparseable_page_is_never_optimistically_exhausted():
    """Unknown quota must not be reported as exhausted (nor as available)."""
    assert quota_exhausted("some redesigned page") is False
    assert parse_boost_quota("some redesigned page") is None


# -- cycle classification ------------------------------------------------------
def test_turnstile_challenge_is_expected_external_and_never_blocking():
    cycle = classify_cycle(challenge_detected=True)
    assert cycle["outcome"] == RCTV_AUTH_BLOCKED_CHALLENGE
    assert cycle["classification"] == EXPECTED_EXTERNAL_BLOCKER
    assert cycle["blocking"] is False
    assert cycle["retry"] is False
    assert cycle["bypass_attempted"] is False
    assert cycle["human_required"] is True


def test_quota_available_and_verified_boost_is_success():
    cycle = classify_cycle(
        login_ok=True, control_visible=True, remaining_quota=4,
        click_performed=True, post_verify=True,
    )
    assert cycle["outcome"] == RCTV_BOOSTED
    assert cycle["boosts_this_run"] == 1
    assert cycle["blocking"] is False
    assert cycle["post_verify"] is True


def test_quota_exhausted_is_not_a_failure():
    cycle = classify_cycle(login_ok=True, control_visible=False, remaining_quota=0)
    assert cycle["outcome"] == RCTV_QUOTA_EXHAUSTED
    assert cycle["blocking"] is False
    assert cycle["boosts_this_run"] == 0


def test_missing_boost_control_is_observed_not_failed():
    cycle = classify_cycle(login_ok=True, control_visible=False, remaining_quota=None)
    assert cycle["outcome"] == RCTV_CONTROL_ABSENT
    assert cycle["blocking"] is False
    assert cycle["block_reason"] == "boost_control_not_visible"


def test_network_failure_is_a_real_blocking_failure():
    cycle = classify_cycle(error="net::ERR_CONNECTION_RESET")
    assert cycle["outcome"] == RCTV_FAILED
    assert cycle["blocking"] is True
    assert cycle["human_required"] is False


def test_login_not_confirmed_is_blocking():
    assert classify_cycle(login_ok=False)["blocking"] is True


def test_only_the_proven_gate_counts_as_expected():
    assert is_expected_external_blocker("cloudflare_turnstile_challenge") is True
    assert is_expected_external_blocker("Champ email introuvable") is False
    assert is_expected_external_blocker("") is False
    assert is_expected_external_blocker(None) is False


# -- main() outcome contract ---------------------------------------------------
def _run_main_with(monkeypatch, runner_exc, site="referralcode"):
    async def fake_sleep(*_a, **_k):
        return None

    class _Browser:
        async def close(self):
            return None

    class _PW:
        class chromium:
            @staticmethod
            async def launch(**_k):
                return _Browser()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    async def runner(_browser):
        if runner_exc is not None:
            raise runner_exc

    monkeypatch.setattr(bumper, "TARGET_SITES", [site])
    monkeypatch.setattr(bumper, "human_sleep", fake_sleep)
    monkeypatch.setattr(bumper, "RUNNERS", {site: runner})
    monkeypatch.setitem(bumper.CONFIG, site, {"url": "https://x", "email": "e", "password": "p"})
    monkeypatch.setattr(bumper, "async_playwright", lambda: _PW())
    return asyncio.run(bumper.main())


def test_expected_external_blocker_does_not_fail_the_isolated_cycle(monkeypatch):
    """A proven Turnstile gate every 5 h must not paint the workflow red."""
    emitted = []
    monkeypatch.setattr(bumper, "_notify", lambda *a, **k: emitted.append((a, k)))
    _run_main_with(monkeypatch, bumper.ExpectedExternalBlocker("cloudflare_turnstile_challenge"))
    assert emitted, "an expected blocker must still be reported once"
    level, event = emitted[0][0]
    assert level == "HUMAN_REQUIRED"
    assert event == "external_blocker"
    assert emitted[0][1]["platform"] == "referralcode-tv"


def test_real_failure_still_fails_the_cycle(monkeypatch):
    monkeypatch.setattr(bumper, "_notify", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="Echec de site"):
        _run_main_with(monkeypatch, RuntimeError("Champ email introuvable"))


def test_expected_blocker_is_a_non_retryable_error():
    assert issubclass(bumper.ExpectedExternalBlocker, bumper.NonRetryableError)


def test_expected_blocker_is_never_retried(monkeypatch):
    calls = {"n": 0}

    async def blocked():
        calls["n"] += 1
        raise bumper.ExpectedExternalBlocker("cloudflare_turnstile_challenge")

    async def no_sleep(*_a, **_k):
        raise AssertionError("an expected external blocker must never be retried")

    monkeypatch.setattr(bumper.asyncio, "sleep", no_sleep)
    with pytest.raises(bumper.ExpectedExternalBlocker):
        asyncio.run(bumper.retry(blocked, retries=3, label="referralcode"))
    assert calls["n"] == 1


# -- isolation from the two healthy bumpers ------------------------------------
def test_rctv_workflow_is_isolated_from_the_other_bumpers():
    rctv = (WORKFLOWS / "bump_referralcode_tv.yml").read_text(encoding="utf-8")
    autres = (WORKFLOWS / "bump_autres.yml").read_text(encoding="utf-8")

    # Distinct concurrency groups: one can never cancel or queue behind the other.
    assert "group: parrainage-bumper-referralcode-tv" in rctv
    assert "group: parrainage-bumper-autres" in autres

    # Distinct targets: no shared browser session, no shared run.
    assert 'TARGET_SITES: "referralcode"' in rctv
    assert 'TARGET_SITES:              "code,parrainage"' in autres
    assert "code,parrainage" not in rctv
    assert "REFERRALCODE_EMAIL" not in autres

    # Only the ReferralCode.tv credentials reach the isolated job.
    for forbidden in (
        "CODE_PARRAINAGE_EMAIL",
        "PARRAINAGE_CO_EMAIL",
        "SUPER_PARRAIN_EMAIL",
    ):
        assert forbidden not in rctv


def test_rctv_workflow_never_gets_a_captcha_solver_key():
    """No 2captcha key in the isolated job: the challenge is never bypassed."""
    rctv = (WORKFLOWS / "bump_referralcode_tv.yml").read_text(encoding="utf-8")
    assert "TWOCAPTCHA" not in rctv


def test_rctv_workflow_cannot_write_repository_state():
    rctv = (WORKFLOWS / "bump_referralcode_tv.yml").read_text(encoding="utf-8")
    assert "contents: read" in rctv
    assert "contents: write" not in rctv
    assert "git push" not in rctv


# -- run_referralcode() end-to-end simulation ---------------------------------
class _Btn:
    def __init__(self, selector: str, visible: bool, clicks: list):
        self.selector = selector
        self._visible = visible
        self._clicks = clicks

    @property
    def first(self):
        return self

    async def wait_for(self, **_k):
        if not self._visible:
            raise TimeoutError("control not visible")

    async def scroll_into_view_if_needed(self):
        return None


class _SimPage:
    def __init__(self, *, body: str, control_visible: bool, clicks: list,
                 body_after: str | None = None):
        self.url = "about:blank"
        self.body = body
        # What the server reports once the boost has actually landed.
        self.body_after = body_after
        self.control_visible = control_visible
        self.clicks = clicks

    async def reload(self, **_k):
        if self.clicks and self.body_after is not None:
            self.body = self.body_after

    async def goto(self, url, **_k):
        self.url = url

    async def screenshot(self, **_k):
        return None

    def locator(self, selector):
        if "cliccami" in selector:
            return _Btn(selector, self.control_visible, self.clicks)
        return _Btn(selector, True, self.clicks)

    async def wait_for_url(self, *_a, **_k):
        return None

    async def wait_for_load_state(self, *_a, **_k):
        return None

    async def inner_text(self, _sel):
        return self.body

    async def close(self):
        return None


def _sim(monkeypatch, *, body, control_visible, challenge=False, body_after=None):
    clicks: list = []
    page = _SimPage(
        body=body, control_visible=control_visible, clicks=clicks, body_after=body_after
    )

    class _Ctx:
        async def new_page(self):
            return page

        async def close(self):
            return None

    async def fake_new_context(_b):
        return _Ctx()

    async def fake_sleep(*_a, **_k):
        return None

    async def fake_fill(*_a, **_k):
        return True

    async def fake_click(pg, locator):
        if "cliccami" in locator.selector:
            clicks.append("boost")
        else:
            pg.url = "https://www.referralcode.tv/my-account/"

    async def fake_challenge(_pg):
        return challenge

    async def fake_consent(*_a, **_k):
        return {"cookie_consent_handled": False, "reason": "none"}

    monkeypatch.setattr(bumper, "new_context", fake_new_context)
    monkeypatch.setattr(bumper, "human_sleep", fake_sleep)
    monkeypatch.setattr(bumper, "smart_fill", fake_fill)
    monkeypatch.setattr(bumper, "human_click", fake_click)
    monkeypatch.setattr("lib.auth_policy.detect_cloudflare_challenge", fake_challenge)
    monkeypatch.setattr("lib.cookie_consent.handle_cookie_consent", fake_consent)
    bumper.run_referralcode.last_cycle = None
    return clicks


def test_normal_login_with_quota_boosts_exactly_once(monkeypatch):
    clicks = _sim(
        monkeypatch,
        body="You've boosted 0 times today. You can click 5 times",
        body_after="You've boosted 1 times today. You can click 4 times",
        control_visible=True,
    )
    asyncio.run(bumper.run_referralcode(object()))
    assert clicks == ["boost"], "at most one boost per run"
    cycle = bumper.run_referralcode.last_cycle
    assert cycle["outcome"] == RCTV_BOOSTED
    assert cycle["report"] == {
        "boosted_before": 0, "boosted_after": 1,
        "remaining_before": 5, "remaining_after": 4,
        "click_performed": True, "POST_VERIFY": True, "OUTCOME": RCTV_BOOSTED,
    }


def test_unverified_boost_fails_the_run_and_never_clicks_twice(monkeypatch):
    """Counters that do not move: the run must fail, having clicked exactly once.

    retry() must not re-enter and spend more of the daily quota.
    """
    clicks = _sim(
        monkeypatch,
        body="You've boosted 0 times today. You can click 5 times",
        body_after=None,  # server reports no change
        control_visible=True,
    )
    with pytest.raises(bumper.NonRetryableError, match="BOOST_NOT_VERIFIED"):
        asyncio.run(bumper.run_referralcode(object()))
    assert clicks == ["boost"], f"exactly one click allowed, got {len(clicks)}"
    assert bumper.run_referralcode.last_cycle["outcome"] == RCTV_BOOST_NOT_VERIFIED


def test_exhausted_quota_never_clicks(monkeypatch):
    clicks = _sim(
        monkeypatch,
        body="You've boosted 5 times today. You can click 0 times",
        control_visible=True,
    )
    asyncio.run(bumper.run_referralcode(object()))
    assert clicks == []
    assert bumper.run_referralcode.last_cycle["outcome"] == RCTV_QUOTA_EXHAUSTED


def test_absent_control_never_clicks_and_never_raises(monkeypatch):
    clicks = _sim(monkeypatch, body="listings", control_visible=False)
    asyncio.run(bumper.run_referralcode(object()))
    assert clicks == []
    assert bumper.run_referralcode.last_cycle["outcome"] == RCTV_CONTROL_ABSENT


def test_standalone_turnstile_stops_before_any_credential_is_typed(monkeypatch):
    clicks = _sim(monkeypatch, body="Un instant…", control_visible=True, challenge=True)

    filled: list = []

    async def spy_fill(*a, **k):
        filled.append(a)
        return True

    monkeypatch.setattr(bumper, "smart_fill", spy_fill)
    with pytest.raises(bumper.ExpectedExternalBlocker, match="cloudflare_turnstile_challenge"):
        asyncio.run(bumper.run_referralcode(object()))
    assert filled == [], "no credential may be submitted into a challenge page"
    assert clicks == []


# -- POST_VERIFY : un clic sans preuve n'est jamais un succès -------------------
@pytest.mark.parametrize(
    "text,boosted,remaining",
    [
        ("You've boosted 0 times today. You can click 5 times", 0, 5),
        ("You've boosted 3 times today", 3, None),
        ("You have boosted 1 time today. You can click 4 times", 1, 4),
        ("page redesigned, no counters", None, None),
    ],
)
def test_parse_both_counters_from_the_real_page_wording(text, boosted, remaining):
    assert parse_boosted_today(text) == boosted
    assert parse_boost_quota(text) == remaining


def test_boost_is_verified_when_boosted_today_increments():
    proof = verify_boost(
        boosted_before=0, boosted_after=1, remaining_before=5, remaining_after=5
    )
    assert proof["post_verify"] is True
    assert proof["boosted_today_incremented"] is True


def test_boost_is_verified_when_remaining_decrements():
    proof = verify_boost(
        boosted_before=None, boosted_after=None, remaining_before=5, remaining_after=4
    )
    assert proof["post_verify"] is True
    assert proof["clicks_remaining_decremented"] is True


def test_real_user_state_0_of_5_then_one_boost_is_verified():
    """Exactly the state observed on the account: boosted 0, can click 5."""
    proof = verify_boost(
        boosted_before=0, boosted_after=1, remaining_before=5, remaining_after=4
    )
    assert proof["post_verify"] is True


@pytest.mark.parametrize(
    "b_before,b_after,r_before,r_after",
    [
        (0, 0, 5, 5),      # rien n'a bougé
        (None, None, None, None),  # page illisible
        (0, 5, 5, 0),      # saut incohérent
    ],
)
def test_unmoved_or_incoherent_counters_are_never_a_verified_boost(
    b_before, b_after, r_before, r_after
):
    proof = verify_boost(
        boosted_before=b_before, boosted_after=b_after,
        remaining_before=r_before, remaining_after=r_after,
    )
    assert proof["post_verify"] is False


def test_click_without_proof_is_boost_not_verified_and_blocking():
    """The exact defect this fixes: click() not raising is not a success."""
    cycle = classify_cycle(
        login_ok=True, control_visible=True, remaining_quota=5,
        click_performed=True, post_verify=False,
    )
    assert cycle["outcome"] == RCTV_BOOST_NOT_VERIFIED
    assert cycle["blocking"] is True, "an unconfirmed boost must be observable, not green"
    assert cycle["boosts_this_run"] == 0
    assert cycle["post_verify"] is False


def test_no_code_path_reports_boosted_without_post_verify():
    """Guards the invariant across every combination, not just the happy path."""
    for control in (True, False):
        for remaining in (None, 0, 5):
            cycle = classify_cycle(
                login_ok=True, control_visible=control,
                remaining_quota=remaining, click_performed=True, post_verify=False,
            )
            assert cycle["outcome"] != RCTV_BOOSTED, (control, remaining)
