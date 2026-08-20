#!/usr/bin/env python3
"""Rollback-enforced GitHub-headless evidence probe for 1Parrainage/Kraken.

This does not promote the platform: 1Parrainage is already WRITE_VERIFIED from
the headed proof.  It may only change ``gh_headless_save`` from NOT_RUN to
PROVEN after a real two-save chain in one browser session:

  baseline account + public guard
  -> append a unique body-only marker -> save once
  -> reread account + public
  -> ALWAYS restore the exact original CKEditor body -> save once
  -> reread account + public

No code/link/reward field is filled separately.  There is no save retry and no
CAPTCHA/anti-bot bypass.  Any missing or ambiguous proof is fail-closed and the
status remains NOT_RUN.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.canary_gate import (  # noqa: E402
    guard_live_evidence_probe,
    record_live_failure,
    record_live_success,
)
from lib.http_fetch import fetch_text  # noqa: E402
from lib.write_status import (  # noqa: E402
    STATUS_WRITE_VERIFIED,
    load_write_status,
    save_write_status,
)
from platforms.oneparrainage.writer import (  # noqa: E402
    EDIT_FORM,
    _bumper,
    _cfg,
    _ck_get,
    _ck_ready,
    _ck_set,
    _detect_challenge,
    _extract_public_block,
    _login,
    _resolve_edit_url,
    build_write_plan,
)

PLATFORM = "1parrainage"
PROGRAM = "kraken"
LANGUAGE = "fr"
EVIDENCE_FIELD = "gh_headless_save"
EXPECTED_CODE = "cpbrgddy"
EXPECTED_LINK = "https://invite.kraken.com/JDNW/s5qudqe4"
EXPECTED_REWARD = "200 € en cryptomonnaies"
CONFIRM_ENV = "AUTOFRESH_1P_HEADLESS_CANARY"

REPORT_PATH = ROOT / "data" / "captures" / "canary-1parrainage-kraken.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plain_html(value: str | None) -> str:
    text = unescape(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _identity_checks(value: str | None) -> dict[str, bool]:
    plain = _plain_html(value)
    return {
        "code_present": EXPECTED_CODE in plain,
        "link_present": EXPECTED_LINK in plain,
        "reward_present": EXPECTED_REWARD in plain,
    }


def _identity_ok(checks: dict[str, bool] | None) -> bool:
    return bool(checks) and all(checks.values())


def _account_evidence(label: str, body_html: str | None, marker: str) -> dict[str, Any]:
    checks = _identity_checks(body_html)
    return {
        "phase": label,
        "side": "account",
        "body_sha256": _sha256(body_html),
        "body_len": len(body_html or ""),
        "marker_present": marker in unescape(body_html or ""),
        "identity": checks,
        "identity_ok": _identity_ok(checks),
    }


async def _public_evidence(label: str, plan, marker: str) -> dict[str, Any]:
    try:
        url = (plan.announcement_url or "").split("#", 1)[0]
        html = await asyncio.to_thread(fetch_text, url)
        extracted = _extract_public_block(html, plan)
        checks = _identity_checks(extracted)
        decoded = unescape(html)
        return {
            "phase": label,
            "side": "public",
            "raw_html_sha256": _sha256(html),
            "extracted_sha256": _sha256(extracted),
            "extracted_len": len(extracted or ""),
            "extracted_preview": (extracted or "")[:1200],
            "marker_present": marker in decoded or marker in unescape(extracted or ""),
            "identity": checks,
            "identity_ok": _identity_ok(checks),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "phase": label,
            "side": "public",
            "error": str(exc),
            "marker_present": None,
            "identity_ok": False,
        }


async def _poll_public(label: str, plan, marker: str, *, expect_marker: bool) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(1, 5):
        last = await _public_evidence(label, plan, marker)
        last["poll_attempt"] = attempt
        if last.get("identity_ok") and last.get("marker_present") is expect_marker:
            return last
        if attempt < 4:
            await asyncio.sleep(3)
    return last


async def _read_account(page, label: str, marker: str) -> tuple[str, dict[str, Any]]:
    if not await _ck_ready(page):
        raise RuntimeError("unexpected_dom: CKEditor not ready")
    body = await _ck_get(page)
    if not body:
        raise RuntimeError("unexpected_dom: empty CKEditor body")
    return body, _account_evidence(label, body, marker)


async def _set_body_without_save(page, target_html: str, marker: str, *, expect_marker: bool) -> dict[str, Any]:
    form = page.locator(EDIT_FORM)
    if await form.count() != 1:
        raise RuntimeError("unexpected_dom: expected exactly one parrainages/edit form")
    if not await _ck_ready(page):
        raise RuntimeError("unexpected_dom: CKEditor not ready before fill")
    result = await _ck_set(page, target_html)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"unexpected_dom: CKEditor setData failed: {result}")
    await page.wait_for_timeout(700)
    prepared = await _ck_get(page)
    evidence = _account_evidence("prepared_before_save", prepared, marker)
    if evidence.get("marker_present") is not expect_marker:
        raise RuntimeError("prepared CKEditor marker state does not match target")
    if not evidence.get("identity_ok"):
        raise RuntimeError("CRITICAL_IDENTITY_GUARD: canonical values changed before save")
    return evidence


async def _click_save_once(page) -> dict[str, Any]:
    """Exactly one scoped Envoyer/Valider click.  No retry loop."""
    bumper = _bumper()
    form = page.locator(EDIT_FORM).first
    candidates = form.locator(
        'button:has-text("Envoyer"), input[value="Envoyer"], '
        'button:has-text("Valider"), input[value="Valider"]'
    )
    visible = []
    for index in range(await candidates.count()):
        item = candidates.nth(index)
        if await item.is_visible() and await item.is_enabled():
            label = ((await item.inner_text()) or (await item.get_attribute("value") or "")).strip()
            visible.append((item, label))
    if len(visible) != 1:
        raise RuntimeError(
            f"unexpected_dom: expected one visible Envoyer/Valider, found {len(visible)}"
        )
    button, label = visible[0]
    await bumper.human_click(page, button)
    try:
        await page.wait_for_load_state("networkidle", timeout=25000)
    except Exception:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    await bumper.human_sleep(1.0, 1.8)
    await _detect_challenge(page)
    return {"clicked": True, "label": label}


def _static_preflight(plan) -> None:
    if not plan.structure_preserved:
        raise RuntimeError("structure_not_preserved")
    if plan.changed_fields:
        raise RuntimeError(
            "REAL_SAFE_DIFF_PRESENT: evidence probe must not mix with a business update"
        )
    expected = {
        "personal_code": EXPECTED_CODE,
        "personal_link": EXPECTED_LINK,
        "referee_reward": EXPECTED_REWARD,
    }
    observed = {key: plan.variables.get(key) for key in expected}
    if observed != expected:
        raise RuntimeError(f"canonical values drifted: expected={expected!r} observed={observed!r}")
    if not plan.edit_url or "parrainages/edit/" not in plan.edit_url:
        raise RuntimeError("proven edit URL missing")
    if not plan.announcement_url or str(plan.platform_offer_id) != "100408":
        raise RuntimeError("proven public offer identity missing")


def _record_status(report: dict[str, Any], success: bool) -> None:
    data = load_write_status()
    meta = data.setdefault("platforms", {}).setdefault(PLATFORM, {})
    if meta.get("status") != STATUS_WRITE_VERIFIED:
        raise RuntimeError("status drifted: 1parrainage is no longer WRITE_VERIFIED")
    attempt = {
        "gh_run_id": report.get("gh_run_id"),
        "at": report.get("finished_at"),
        "report": "data/captures/canary-1parrainage-kraken.json",
        "success": success,
        "canary_ok": report.get("canary_ok"),
        "rollback_ok": report.get("rollback_ok"),
        "save_attempts": report.get("save_attempts"),
        "error": report.get("error"),
    }
    meta["last_headless_canary_attempt"] = attempt
    if success:
        meta["gh_headless_login"] = "PROVEN"
        meta["gh_headless_edit"] = "PROVEN"
        meta[EVIDENCE_FIELD] = "PROVEN"
        meta["last_headless_canary_run"] = attempt
        meta["headless_evidence"] = {
            "program": PROGRAM,
            "checks": report.get("checks"),
            "account_before_sha256": (
                (report.get("phases") or {}).get("before_account") or {}
            ).get("body_sha256"),
            "account_rollback_sha256": (
                (report.get("phases") or {}).get("rollback_account") or {}
            ).get("body_sha256"),
        "source": "github_headless_canary_with_mandatory_rollback",
        }
        suffix = (
            " GH headless save PROVEN by rollback-enforced Kraken body-marker "
            f"canary (run {report.get('gh_run_id')}); exactly two save attempts, "
            "account+public canary verification, exact account rollback hash, "
            "and public marker removal confirmed."
        )
        if "GH headless save PROVEN" not in str(meta.get("notes") or ""):
            meta["notes"] = (str(meta.get("notes") or "").rstrip() + suffix).strip()
    elif meta.get(EVIDENCE_FIELD) != "PROVEN":
        meta[EVIDENCE_FIELD] = "NOT_RUN"
    save_write_status(data)


async def _run_probe(report: dict[str, Any]) -> bool:
    plan = build_write_plan(PLATFORM, PROGRAM, LANGUAGE)
    _static_preflight(plan)
    report["plan"] = {
        "program": PROGRAM,
        "platform_offer_id": plan.platform_offer_id,
        "announcement_url": plan.announcement_url,
        "edit_url": plan.edit_url,
        "changed_fields": plan.changed_fields,
        "structure_preserved": plan.structure_preserved,
    }

    gate = guard_live_evidence_probe(
        PLATFORM,
        evidence_field=EVIDENCE_FIELD,
        expected_value="NOT_RUN",
    )
    report["gate"] = gate
    if not gate.get("ok"):
        raise RuntimeError(f"evidence probe refused: {gate.get('error')}")

    marker = f"AUTOFRESH_1P_HEADLESS_CANARY_{report['gh_run_id']}"
    report["marker"] = marker
    report["phases"] = {}
    report["save_attempts"] = 0
    report["canary_may_have_persisted"] = False
    report["rollback_attempted"] = False
    report["canary_ok"] = False
    report["rollback_ok"] = False

    from playwright.async_api import async_playwright

    pw = None
    browser = None
    ctx = None
    page = None
    original_body: str | None = None
    edit_url: str | None = None
    canary_may_have_persisted = False
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--lang=fr-FR",
                "--window-size=1280,720",
            ],
        )
        ctx = await _bumper().new_context(browser)
        page = await ctx.new_page()
        await page.set_viewport_size({"width": 1280, "height": 720})

        await _login(page, _cfg())
        report["steps"] = ["login"]
        edit_url = await _resolve_edit_url(page, plan)
        if edit_url != plan.edit_url:
            raise RuntimeError(f"edit identity drift: expected={plan.edit_url} got={edit_url}")
        report["steps"].append("edit_resolved")
        await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
        await _detect_challenge(page)
        original_body, before_account = await _read_account(page, "before_account", marker)
        before_public = await _poll_public(
            "before_public", plan, marker, expect_marker=False
        )
        report["phases"]["before_account"] = before_account
        report["phases"]["before_public"] = before_public
        if before_account.get("marker_present") or not before_account.get("identity_ok"):
            raise RuntimeError("baseline account guard failed")
        if before_public.get("marker_present") or not before_public.get("identity_ok"):
            raise RuntimeError("baseline public guard failed")

        marker_html = f'<p data-autofresh-canary="1parrainage-headless">{marker}</p>'
        canary_body = original_body.rstrip() + "\n" + marker_html
        prepared = await _set_body_without_save(
            page, canary_body, marker, expect_marker=True
        )
        report["phases"]["canary_pre_save"] = prepared
        canary_may_have_persisted = True
        report["canary_may_have_persisted"] = True
        report["save_attempts"] += 1
        report["canary_save"] = await _click_save_once(page)

        await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
        canary_body_read, canary_account = await _read_account(
            page, "canary_account", marker
        )
        canary_public = await _poll_public(
            "canary_public", plan, marker, expect_marker=True
        )
        report["phases"]["canary_account"] = canary_account
        report["phases"]["canary_public"] = canary_public
        report["canary_ok"] = bool(
            canary_account.get("marker_present")
            and canary_account.get("identity_ok")
            and canary_public.get("marker_present")
            and canary_public.get("identity_ok")
            and _sha256(canary_body_read) != _sha256(original_body)
        )
        if not report["canary_ok"]:
            raise RuntimeError("canary post-verify incomplete")
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc)
    finally:
        if canary_may_have_persisted and page is not None and edit_url and original_body:
            report["rollback_attempted"] = True
            try:
                await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
                await _detect_challenge(page)
                pre_rollback_body, pre_rollback = await _read_account(
                    page, "pre_rollback_account", marker
                )
                report["phases"]["pre_rollback_account"] = pre_rollback
                report["phases"]["pre_rollback_body_sha256"] = _sha256(
                    pre_rollback_body
                )
                await _set_body_without_save(
                    page, original_body, marker, expect_marker=False
                )
                report["save_attempts"] += 1
                report["rollback_save"] = await _click_save_once(page)

                await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
                rollback_body, rollback_account = await _read_account(
                    page, "rollback_account", marker
                )
                rollback_public = await _poll_public(
                    "rollback_public", plan, marker, expect_marker=False
                )
                report["phases"]["rollback_account"] = rollback_account
                report["phases"]["rollback_public"] = rollback_public
                report["rollback_ok"] = bool(
                    _sha256(rollback_body) == _sha256(original_body)
                    and not rollback_account.get("marker_present")
                    and rollback_account.get("identity_ok")
                    and not rollback_public.get("marker_present")
                    and rollback_public.get("identity_ok")
                )
                if not report["rollback_ok"] and not report.get("error"):
                    report["error"] = "rollback verification incomplete"
            except Exception as rollback_exc:  # noqa: BLE001
                report["rollback_error"] = str(rollback_exc)
                report["error"] = report.get("error") or "rollback failed"

        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if pw is not None:
            try:
                await pw.stop()
            except Exception:
                pass

    report["checks"] = {
        "authenticated": "login" in (report.get("steps") or []),
        "targeted_body_only": True,
        "exactly_two_save_attempts": report.get("save_attempts") == 2,
        "canary_account_verified": bool(
            ((report.get("phases") or {}).get("canary_account") or {}).get(
                "marker_present"
            )
        ),
        "canary_public_verified": bool(
            ((report.get("phases") or {}).get("canary_public") or {}).get(
                "marker_present"
            )
        ),
        "rollback_account_exact": (
            ((report.get("phases") or {}).get("before_account") or {}).get(
                "body_sha256"
            )
            == ((report.get("phases") or {}).get("rollback_account") or {}).get(
                "body_sha256"
            )
            and bool((report.get("phases") or {}).get("rollback_account"))
        ),
        "rollback_public_marker_absent": (
            ((report.get("phases") or {}).get("rollback_public") or {}).get(
                "marker_present"
            )
            is False
        ),
    }
    return bool(
        report.get("canary_ok")
        and report.get("rollback_ok")
        and report.get("save_attempts") == 2
        and all((report.get("checks") or {}).values())
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    report: dict[str, Any] = {
        "schema_version": 1,
        "platform": PLATFORM,
        "program": PROGRAM,
        "started_at": _now(),
        "gh_run_id": os.environ.get("GITHUB_RUN_ID") or "local",
        "headless": True,
        "status_before": "WRITE_VERIFIED",
        "evidence_field_before": "NOT_RUN",
        "live_write_authorized": False,
    }
    success = False
    gate_acquired = False
    try:
        if not args.execute or not args.force:
            raise RuntimeError("--execute --force required")
        if os.environ.get(CONFIRM_ENV) != "1":
            raise RuntimeError(f"{CONFIRM_ENV}=1 required")
        report["live_write_authorized"] = True
        success = asyncio.run(_run_probe(report))
        gate_acquired = bool((report.get("gate") or {}).get("lock"))
    except Exception as exc:  # noqa: BLE001
        report["error"] = report.get("error") or str(exc)
        gate_acquired = bool((report.get("gate") or {}).get("lock"))
    finally:
        report["success"] = success
        report["finished_at"] = _now()
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if gate_acquired:
            if success:
                record_live_success(PLATFORM)
            else:
                record_live_failure(PLATFORM, report.get("error") or "headless_probe_failed")
        if report.get("live_write_authorized") and gate_acquired:
            try:
                _record_status(report, success)
            except Exception as status_exc:  # noqa: BLE001
                report["status_record_error"] = str(status_exc)
                success = False
                report["success"] = False
                REPORT_PATH.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
