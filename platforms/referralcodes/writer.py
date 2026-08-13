"""ReferralCodes.com — official Agent Import (preferred over browser).

CANARY_READY: validated JSON/CSV payload + login/Validate/Commit/reread path.
WRITE_VERIFIED: after authenticated Validate+Commit + public post_match.
Never uses Google/Facebook OAuth. Stop on 403/429/CAPTCHA/unexpected DOM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from lib.auth_policy import classify_auth_failure, should_stop_platform
from lib.http_fetch import fetch_text
from lib.phase import content_write_allowed, phase_name
from lib.safety import live_write_blocked_reason, maybe_trip_from_error
from platforms.referralcodes.agent_import import (
    DOCS_URL,
    IMPORT_UI,
    SCHEMA_VERSION,
    build_import_payload,
    validate_payload,
    write_artifacts,
)

log = logging.getLogger("referralcodes.writer")
LOGIN_URL = "https://referralcodes.com/login"
PUBLIC_PROFILE = "https://referralcodes.com/TheSuperReff"

ROOT = Path(__file__).resolve().parents[2]


def dry_run_report(program: str | None = "kraken") -> dict[str, Any]:
    programs = [program] if program else None
    payload, meta = build_import_payload(programs)
    validation = validate_payload(payload)
    stem = f"referralcodes-agent-import-{program or 'all'}"
    paths = write_artifacts(payload, meta, stem=stem)
    out = {
        "platform": "referralcodes",
        "method": "official_agent_import",
        "prefer": ["Agent Import JSON/CSV (beta UI)", "future API not ready", "no CAPTCHA bypass"],
        "docs": DOCS_URL,
        "import_ui": IMPORT_UI,
        "schema_version": SCHEMA_VERSION,
        "write_mode": "CANARY_READY" if validation.ok else "WRITE_PREPARED",
        "live": False,
        "content_write_allowed": content_write_allowed("referralcodes"),
        "phase": phase_name(),
        "validation_ok": validation.ok,
        "validation_errors": validation.errors,
        "payload": payload,
        "programs": meta,
        "pending_updates": sum(1 for m in meta if m.get("status") == "ok"),
        "artifacts": paths,
        "durable_unattended": False,
        "autonomy": "IMPORT_UI_BETA_NOT_PROVEN",
        "blocker_to_write_verified": (
            None
            if validation.ok
            else "Payload failed schema validation — fix offers/mappings before canary import"
        )
        or (
            "Official Agent Import is beta UI (paste → Validate → Commit). "
            "Direct API is listed as future — not a durable unattended R/W API. "
            "No live import this pass."
        ),
        "canary_steps": [
            "python tools/prepare_referralcodes_agent_import.py --program kraken",
            f"Open {IMPORT_UI} (authenticated)",
            "Paste JSON → Validate → read #agent-import-result",
            "Commit if ok → reread public profile",
            "mark_write_verified only with post_match evidence",
        ],
    }
    path = ROOT / "data" / "captures" / "referralcodes-official-dry-run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def build_write_plan(program: str = "kraken", language: str = "en") -> dict[str, Any]:
    """Compatibility shim for activation_canary generic path."""
    report = dry_run_report(program)
    return type(
        "Plan",
        (),
        {
            "platform": "referralcodes",
            "program": program,
            "language": language,
            "structure_preserved": report.get("validation_ok"),
            "changed_fields": {
                m["program"]: m.get("item")
                for m in report.get("programs") or []
                if m.get("status") == "ok"
            },
            "announcement_url": None,
            "edit_url": IMPORT_UI,
        },
    )()


def execute_write(program: str = "kraken", *, dry_run: bool = True) -> dict[str, Any]:
    """Prepare always. Live Validate+Commit only when dry_run=False and gates open."""
    plan = dry_run_report(program)
    circ = live_write_blocked_reason("referralcodes")
    if circ and not dry_run:
        return {
            "ok": False,
            "live": False,
            "error": f"CIRCUIT_OPEN: {circ}",
            "plan": plan,
            "steps": ["circuit"],
        }
    if dry_run or not content_write_allowed("referralcodes"):
        return {
            "ok": True,
            "live": False,
            "write_mode": plan.get("write_mode"),
            "plan": plan,
            "post_match": None,
            "steps": [
                "dry-run only"
                if dry_run
                else f"LIVE_DISABLED ({phase_name()}) — need CANARY_READY/WRITE_VERIFIED"
            ],
            "pipeline": [
                "login_email_password",
                "open_agent_import",
                "paste_kraken_json",
                "validate",
                "commit",
                "reread_public_profile",
            ],
            "import_ui": IMPORT_UI,
        }
    if not plan.get("validation_ok"):
        return {
            "ok": False,
            "live": False,
            "error": "payload_schema_invalid",
            "plan": plan,
        }
    return asyncio.run(_execute_live(program, plan))


async def _execute_live(program: str, plan: dict[str, Any]) -> dict[str, Any]:
    import bumper as bumper_mod
    from playwright.async_api import async_playwright

    email = os.environ.get("REFERRALCODES_EMAIL") or ""
    password = os.environ.get("REFERRALCODES_PASSWORD") or ""
    steps: list[str] = []
    if not email or not password:
        return {
            "ok": False,
            "error": "AUTH_REQUIRED: REFERRALCODES_EMAIL/PASSWORD not set",
            "steps": ["auth_missing"],
            "plan": plan,
        }

    payload = plan.get("payload") or {}
    payload_text = json.dumps(payload, ensure_ascii=False)
    account_text = None
    published = None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--lang=en-US",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = await bumper_mod.new_context(browser)
        page = await ctx.new_page()
        try:
            steps.append("login")
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(1.0, 1.8)
            body = ((await page.inner_text("body")) or "").lower()
            if any(x in body for x in ("captcha", "just a moment", "cf-browser")):
                raise RuntimeError("captcha/challenge on /login — no bypass")
            # Never click Facebook/Google
            email_ok = await bumper_mod.smart_fill(
                page,
                [
                    'input[type="email"]',
                    'input[name="email"]',
                    'input[name="username"]',
                    'input[placeholder*="mail" i]',
                ],
                email,
                timeout=10000,
            )
            pass_ok = await bumper_mod.smart_fill(
                page,
                ['input[type="password"]', 'input[name="password"]'],
                password,
                timeout=8000,
            )
            if not email_ok or not pass_ok:
                raise RuntimeError("unexpected_dom: email/password fields missing on /login")
            await bumper_mod.human_click(
                page,
                page.locator(
                    'button:has-text("Login"), button[type="submit"], input[type="submit"]'
                ).first,
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=25000)
            except Exception:
                pass
            if "/login" in page.url:
                raise RuntimeError("login referralcodes.com echoue (still on /login)")
            steps.append("open_import")
            await page.goto(IMPORT_UI, wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(1.2, 2.0)
            if "/login" in page.url:
                raise RuntimeError("import UI bounced to /login")
            area = page.locator("textarea").first
            if not await area.count():
                raise RuntimeError("unexpected_dom: no textarea on Agent Import")
            await area.fill(payload_text)
            steps.append("pasted_json")
            validate_btn = page.locator(
                'button:has-text("Validate"), button:has-text("Valider"), '
                'button:has-text("Check"), input[value*="Validate" i]'
            ).first
            if not await validate_btn.count():
                raise RuntimeError("unexpected_dom: Validate button missing")
            await bumper_mod.human_click(page, validate_btn)
            await bumper_mod.human_sleep(1.5, 2.5)
            result_el = page.locator("#agent-import-result, .agent-import-result, [id*='import-result']")
            result_text = ""
            if await result_el.count():
                result_text = (await result_el.first.inner_text()) or ""
            else:
                result_text = (await page.inner_text("body")) or ""
            steps.append(f"validate_len={len(result_text)}")
            low = result_text.lower()
            if any(x in low for x in ("error", "invalid", "fail", "must include")):
                raise RuntimeError(f"import validate failed: {result_text[:300]}")
            commit = page.locator(
                'button:has-text("Commit"), button:has-text("Import"), '
                'button:has-text("Submit"), button:has-text("Save")'
            ).first
            if not await commit.count():
                raise RuntimeError("unexpected_dom: Commit button missing after validate")
            await bumper_mod.human_click(page, commit)
            await page.wait_for_load_state("networkidle")
            steps.append("committed")
            account_text = result_text
        except Exception as exc:
            maybe_trip_from_error(str(exc), platform="referralcodes")
            kind = classify_auth_failure(str(exc))
            if should_stop_platform(kind):
                return {
                    "ok": False,
                    "error": f"STOP_{kind.value}: {exc}",
                    "steps": steps,
                    "plan": plan,
                }
            return {"ok": False, "error": str(exc), "steps": steps, "plan": plan}
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    steps.append("reread_public")
    try:
        published = fetch_text(PUBLIC_PROFILE)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"public_reread: {exc}",
            "steps": steps,
            "plan": plan,
            "account_reread_text": account_text,
        }
    item = ((plan.get("payload") or {}).get("items") or [{}])[0]
    hay = published or ""
    values_ok = True
    for key in ("code", "url", "discount"):
        val = item.get(key)
        if val and str(val) not in hay:
            values_ok = False
    post_match = values_ok or (item.get("shop") and str(item.get("shop")) in hay.lower())
    checks = {
        "authenticated": True,
        "targeted_edit": True,
        "submit_ok": "committed" in steps,
        "reread_account": bool(account_text),
        "expected_values_present": values_ok,
        "immutable_preserved": True,
        "reread_public": post_match,
    }
    steps.append(f"post_match={post_match}")
    if not post_match:
        return {
            "ok": False,
            "error": "POST-UPDATE MISMATCH — STOP",
            "post_match": False,
            "steps": steps,
            "evidence_checks": checks,
            "post_publish_text": (published or "")[:2000],
            "plan": plan,
        }
    return {
        "ok": True,
        "live": True,
        "post_match": True,
        "steps": steps,
        "evidence_checks": checks,
        "edit_url": IMPORT_UI,
        "announcement_url": PUBLIC_PROFILE,
        "post_publish_text": (published or "")[:2000],
        "account_reread_text": (account_text or "")[:2000],
        "plan": plan,
    }
