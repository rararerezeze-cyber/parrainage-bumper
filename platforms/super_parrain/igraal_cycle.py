"""iGraal announcement update inside an already-eligible historical bump cycle.

The read-only probe 37626222482 proved that form[message] belongs to the
public announcement's /edit resource, not codes-promo. One message save here
is distinct from the unchanged historical codes-promo bump save.
"""
from __future__ import annotations

from types import SimpleNamespace

from lib.content_plan import content_allowed, load_plan
from lib.phase import live_writes_enabled
from lib.super_parrain_policy import should_prefill_content
from lib.super_parrain_schedule import is_eligible, is_historical_bumper_authorized
from lib.super_parrain_resource import assert_announcement_edit_url, assert_not_codes_promo_form
from lib.super_parrain_post_verify import verify_public_program
from platforms.super_parrain.writer import _click_save_not_boost, _page_blocked_reason, build_write_plan


def _norm(text):
    return " ".join((text or "").split())


def _record_failure(result, exc):
    # Never copy browser exception text, which can contain form values.
    known = {
        "announcement_challenge_or_cooldown", "announcement_24h_cooldown",
        "announcement_body_not_unique", "live_announcement_structure_drift",
        "announcement_prefill_mismatch", "announcement_account_post_verify_failed",
        "announcement_public_post_verify_failed",
    }
    reason = str(exc) if str(exc) in known else f"announcement_error_{type(exc).__name__}"
    result["reason"] = reason
    result["content_post_verify"] = {"program": "igraal", "post_match": False,
                                     "ok": False, "error": reason}


async def update_announcement(page, code_edit_url):
    """Only called from the normal cycle, with its pre-check and canary gates."""
    result = {"program": "igraal", "edit_url": code_edit_url, "fields_filled": [],
              "changed_fields": {}, "needs_update": False, "skipped": True,
              "reason": "content_not_authorized", "writes_performed": 0}
    allowed, reason = content_allowed("igraal", load_plan())
    policy, policy_reason = should_prefill_content("igraal")
    if not live_writes_enabled("super-parrain") or not allowed or not policy:
        result["reason"] = reason if not allowed else policy_reason
        return result
    if not is_eligible()[0] or not is_historical_bumper_authorized():
        result["reason"] = "normal_cycle_not_eligible_or_authorized"
        return result
    try:
        plan = build_write_plan("super-parrain", "igraal", "fr", only_fields=["referee_reward"])
    except Exception as exc:
        _record_failure(result, exc)
        return result
    if not plan.structure_preserved or set(plan.changed_fields) - {"referee_reward"}:
        result["reason"] = "immutable_or_unconfirmed_field_drift"
        result["content_post_verify"] = {"program": "igraal", "post_match": False, "error": result["reason"]}
        return result
    if not plan.changed_fields:
        result["reason"] = "in_sync"
        return result
    result.update(needs_update=True, changed_fields=plan.changed_fields)
    # The probe proved this exact route and form. No fuzzy resource discovery
    # or fallback to a codes-promo textarea during a production mutation.
    url = (plan.announcement_url or "").rstrip("/") + "/edit"
    content_page = None
    try:
        assert_announcement_edit_url(url)
        content_page = await page.context.new_page()
        await content_page.goto(url, wait_until="domcontentloaded")
        assert_announcement_edit_url(content_page.url)
        names = await content_page.locator("input, textarea").evaluate_all("els => els.map(el => el.name)")
        assert_not_codes_promo_form(url=content_page.url, form_names=names)
        page_text = await content_page.inner_text("body")
        blocked = _page_blocked_reason(page_text, content_page.url)
        if blocked:
            raise RuntimeError("announcement_challenge_or_cooldown")
        low = page_text.lower()
        if "moins de 24" in low or ("24h" in low and ("réessayer" in low or "reessayer" in low)):
            raise RuntimeError("announcement_24h_cooldown")
        body = content_page.locator('textarea[name="form[message]"]')
        if await body.count() != 1:
            raise RuntimeError("announcement_body_not_unique")
        current = await body.input_value()
        if _norm(current) not in {_norm(plan.historical), _norm(plan.rendered)}:
            raise RuntimeError("live_announcement_structure_drift")
        already_current = _norm(current) == _norm(plan.rendered)
        if not already_current:
            await body.fill(plan.rendered)
            if await body.input_value() != plan.rendered:
                raise RuntimeError("announcement_prefill_mismatch")
            # Exactly one official UI submission; no replay on timeout.
            result["save_attempted"] = True
            result["writes_performed"] = None  # unknown until submission returns
            await _click_save_not_boost(content_page)
            result["writes_performed"] = 1
        await content_page.goto(url, wait_until="domcontentloaded")
        assert_announcement_edit_url(content_page.url)
        if _norm(await body.input_value()) != _norm(plan.rendered):
            raise RuntimeError("announcement_account_post_verify_failed")
        public = verify_public_program("igraal", filled_fields=["body"], expected_body=plan.rendered)
        result["content_post_verify"] = public.to_dict()
        if not public.post_match:
            raise RuntimeError("announcement_public_post_verify_failed")
        from tools.run_verified_writers import _persist_verified_baseline

        _persist_verified_baseline(plan, SimpleNamespace(
            writes_performed=result["writes_performed"], edit_url=url,
        ))
        result.update(skipped=False, fields_filled=["body"] if not already_current else [],
                      reason="announcement_verified" if not already_current else "reconciled_without_save",
                      content_resource=url)
    except Exception as exc:
        _record_failure(result, exc)
    finally:
        if content_page is not None:
            await content_page.close()
    return result
