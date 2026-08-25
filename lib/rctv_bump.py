"""ReferralCode.tv isolated bump runtime rules.

ReferralCode.tv is deliberately isolated from every other bumper: its only
scheduled workflow is ``.github/workflows/bump_referralcode_tv.yml`` and it
never shares a run, a browser session, or a concurrency group with
Code-Parrainage / Parrainage.co.

The goal is the best *legitimate* behaviour, never a bypass:

* try the normal login exactly like a human client would;
* if the account is reachable, boost the existing ``#cliccami`` listing at
  most once per run (the site allows a few per day, we spend one);
* if GitHub-hosted Chromium is served the standalone Cloudflare Turnstile
  interstitial, stop immediately — no solve, no click, no retry loop — and
  classify the cycle as an EXPECTED external blocker rather than a product
  regression.

An expected external blocker must not turn the isolated workflow red on every
schedule: a recurring, already-documented, unfixable-from-GitHub challenge is
noise, not a signal. A real failure (network, DOM drift on an authenticated
page, bad credentials) still fails loudly.
"""
from __future__ import annotations

import re
from typing import Any

# Runtime classes for one isolated ReferralCode.tv cycle.
RCTV_BOOSTED = "BOOSTED"
RCTV_QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
RCTV_CONTROL_ABSENT = "BOOST_CONTROL_ABSENT"
# A click that raised no exception proves nothing. Without a counter moving,
# the boost is NOT verified and must never be reported as a success.
RCTV_BOOST_NOT_VERIFIED = "BOOST_NOT_VERIFIED"
RCTV_AUTH_BLOCKED_CHALLENGE = "AUTH_BLOCKED_CHALLENGE"
RCTV_FAILED = "FAILED"

# Cycle outcomes that are expected, externally imposed, and already documented.
# They are reported, deduplicated, and never retried or bypassed.
EXPECTED_EXTERNAL_BLOCKER = "EXPECTED_EXTERNAL_BLOCKER"

# Exact non-retryable reasons raised by the runtime for a proven external gate.
EXPECTED_BLOCKER_REASONS = frozenset(
    {
        "cloudflare_turnstile_challenge",
    }
)

BOOST_CONTROL_SELECTOR = "button#cliccami"
LISTINGS_PATH = "/my-account/?tab=listings"

# "You can click 0 more times today", "can click 3 more", ... The site wording
# has moved before; keep the parse tolerant but never optimistic: an
# unparseable page is *not* treated as "quota available".
_QUOTA_RE = re.compile(r"can\s+click\s+(\d+)", re.IGNORECASE)
# "You've boosted 0 times today" — the counter that must go UP after a real boost.
_BOOSTED_RE = re.compile(r"boosted\s+(\d+)\s+time", re.IGNORECASE)


def is_expected_external_blocker(reason: Any) -> bool:
    """True only for a proven, documented, external gate (never a DOM bug)."""
    text = str(reason or "").strip().lower()
    if not text:
        return False
    return any(known in text for known in EXPECTED_BLOCKER_REASONS)


def parse_boost_quota(page_text: str | None) -> int | None:
    """Remaining boosts announced by the listings page, or None if unknown."""
    if not page_text:
        return None
    match = _QUOTA_RE.search(page_text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def parse_boosted_today(page_text: str | None) -> int | None:
    """How many boosts the listings page says were already spent today."""
    if not page_text:
        return None
    match = _BOOSTED_RE.search(page_text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def verify_boost(
    *,
    boosted_before: int | None,
    boosted_after: int | None,
    remaining_before: int | None,
    remaining_after: int | None,
) -> dict[str, Any]:
    """Did the boost actually land? Requires a counter to have moved.

    Either signal alone is sufficient proof, but at least one is mandatory:
      * boosted_today went up by exactly one, or
      * clicks_remaining went down by exactly one.

    Anything else -- unchanged counters, unreadable page, a jump of more than
    one -- is NOT proof. A click that merely did not raise is never a success.
    """
    boosted_up = (
        boosted_before is not None
        and boosted_after is not None
        and boosted_after == boosted_before + 1
    )
    remaining_down = (
        remaining_before is not None
        and remaining_after is not None
        and remaining_after == remaining_before - 1
    )
    readable = any(
        v is not None
        for v in (boosted_before, boosted_after, remaining_before, remaining_after)
    )
    return {
        "boosted_today_incremented": boosted_up,
        "clicks_remaining_decremented": remaining_down,
        "counters_readable": readable,
        "post_verify": bool(boosted_up or remaining_down),
    }


def quota_exhausted(page_text: str | None) -> bool:
    """True only when the page explicitly announces zero remaining boosts."""
    return parse_boost_quota(page_text) == 0


def classify_cycle(
    *,
    challenge_detected: bool = False,
    login_ok: bool = False,
    control_visible: bool = False,
    remaining_quota: int | None = None,
    click_performed: bool = False,
    post_verify: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    """Classify one isolated ReferralCode.tv cycle.

    ``blocking`` is True only for a real failure the repository could fix.
    An expected external blocker is reported but never fails the workflow.
    """
    if challenge_detected:
        return {
            "outcome": RCTV_AUTH_BLOCKED_CHALLENGE,
            "classification": EXPECTED_EXTERNAL_BLOCKER,
            "blocking": False,
            "retry": False,
            "bypass_attempted": False,
            "block_reason": "cloudflare_turnstile_challenge",
            "human_required": True,
        }
    if error:
        return {
            "outcome": RCTV_FAILED,
            "classification": "RUNTIME_FAILURE",
            "blocking": True,
            "retry": False,
            "bypass_attempted": False,
            "block_reason": str(error)[:300],
            "human_required": False,
        }
    if not login_ok:
        return {
            "outcome": RCTV_FAILED,
            "classification": "RUNTIME_FAILURE",
            "blocking": True,
            "retry": False,
            "bypass_attempted": False,
            "block_reason": "login_not_confirmed",
            "human_required": False,
        }
    if click_performed:
        # POST_VERIFY is mandatory. `post_verify` must come from verify_boost()
        # against counters re-read AFTER the click -- never from the click call
        # returning without raising.
        if post_verify:
            return {
                "outcome": RCTV_BOOSTED,
                "classification": "OK",
                "blocking": False,
                "retry": False,
                "bypass_attempted": False,
                "block_reason": None,
                "human_required": False,
                "boosts_this_run": 1,
                "post_verify": True,
            }
        return {
            "outcome": RCTV_BOOST_NOT_VERIFIED,
            "classification": "UNVERIFIED_ACTION",
            # Observable, not silently green: a click we cannot confirm means the
            # listing may not have moved at all, which is the whole point of the
            # scheduled run.
            "blocking": True,
            "retry": False,
            "bypass_attempted": False,
            "block_reason": "boost_click_not_confirmed_by_any_counter",
            "human_required": False,
            "boosts_this_run": 0,
            "post_verify": False,
        }
    if remaining_quota == 0:
        return {
            "outcome": RCTV_QUOTA_EXHAUSTED,
            "classification": "OK",
            "blocking": False,
            "retry": False,
            "bypass_attempted": False,
            "block_reason": "daily_boost_quota_exhausted",
            "human_required": False,
            "boosts_this_run": 0,
        }
    if not control_visible:
        # The site also hides the control once the daily quota is spent, so a
        # missing button is ambiguous: surface it, never fail the cycle on it,
        # and never broaden the selector to "find something to click".
        return {
            "outcome": RCTV_CONTROL_ABSENT,
            "classification": "OBSERVED_NO_ACTION",
            "blocking": False,
            "retry": False,
            "bypass_attempted": False,
            "block_reason": "boost_control_not_visible",
            "human_required": False,
            "boosts_this_run": 0,
        }
    return {
        "outcome": RCTV_CONTROL_ABSENT,
        "classification": "OBSERVED_NO_ACTION",
        "blocking": False,
        "retry": False,
        "bypass_attempted": False,
        "block_reason": "boost_not_performed",
        "human_required": False,
        "boosts_this_run": 0,
    }
