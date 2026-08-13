#!/usr/bin/env python3
"""ReferralCodes Agent Import — VALIDATE ONLY. Never Commit.

Uses official /profile/import/agent. Kraken native EN $200 only.
  python -u tools/validate_referralcodes_agent_import.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.paths import mapping_path  # noqa: E402
from platforms.referralcodes.agent_import import (  # noqa: E402
    IMPORT_UI,
    SCHEMA_VERSION,
    validate_payload,
)

OUT = ROOT / "data" / "captures" / "referralcodes-agent-validate-only.json"
LOGIN = "https://referralcodes.com/login"
NATIVE_DISCOUNT = "⭐️ $200 in Crypto ⭐️"
CODE = "cpbrgddy"
LINK = "https://invite.kraken.com/JDNW/s5qudqe4"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _kraken_item() -> dict:
    discount = NATIVE_DISCOUNT
    try:
        raw = json.loads(mapping_path("referralcodes", "kraken", "en").read_text(encoding="utf-8"))
        pv = raw.get("platform_values") or {}
        if pv.get("referee_reward"):
            discount = str(pv["referee_reward"])
    except Exception:
        pass
    if "200 €" in discount:
        discount = NATIVE_DISCOUNT
    return {
        "shop": "kraken",
        "discount": discount,
        "url": LINK,
        "code": CODE,
        "description": "VALIDATE ONLY — existing TheSuperReff listing, no commit",
    }


def _classify_result(text: str) -> dict:
    low = (text or "").lower()
    update = any(
        x in low
        for x in ("update", "updated", "already exists", "match", "matched", "overwrite", "replace")
    )
    duplicate = any(
        x in low for x in ("duplicate", "duplicat", "already listed", "conflict", "two listings")
    )
    create = any(x in low for x in ("create", "new item", "will add", "insert"))
    error = any(x in low for x in ("error", "invalid", "fail", "must include"))
    if error:
        kind = "VALIDATE_ERROR"
    elif update and not duplicate:
        kind = "UPDATE_OR_MATCH"
    elif duplicate:
        kind = "DUPLICATE_RISK"
    elif create:
        kind = "WOULD_CREATE"
    else:
        kind = "UNKNOWN"
    return {
        "existing_detected": update or duplicate,
        "update_or_duplicate": kind,
        "signals": {"update": update, "duplicate": duplicate, "create": create, "error": error},
    }


async def main() -> int:
    email = os.environ.get("REFERRALCODES_EMAIL") or ""
    password = os.environ.get("REFERRALCODES_PASSWORD") or ""
    item = _kraken_item()
    payload = {"version": SCHEMA_VERSION, "items": [item]}
    schema = validate_payload(payload)
    report: dict = {
        "platform": "referralcodes",
        "phase": "VALIDATE_ONLY",
        "commit": False,
        "live_write": False,
        "docs": "https://referralcodes.com/agents",
        "import_ui": IMPORT_UI,
        "payload": payload,
        "schema_ok": schema.ok,
        "schema_errors": schema.errors,
        "at": _now(),
    }
    if not schema.ok:
        report["ok"] = False
        report["error"] = "payload_schema_invalid"
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2)[:2000])
        return 1
    if not email or not password:
        report["ok"] = False
        report["error"] = "AUTH_REQUIRED: REFERRALCODES_EMAIL/PASSWORD"
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("missing REFERRALCODES_*")
        return 1

    import bumper as bumper_mod
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=en-US"],
        )
        ctx = await bumper_mod.new_context(browser)
        page = await ctx.new_page()
        steps: list[str] = []
        try:
            steps.append("login")
            await page.goto(LOGIN, wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(1.0, 1.8)
            body = ((await page.inner_text("body")) or "").lower()
            if any(x in body for x in ("captcha", "just a moment", "cf-browser")):
                raise RuntimeError("captcha/challenge on /login — no bypass")
            email_ok = await bumper_mod.smart_fill(
                page,
                ['input[type="email"]', 'input[name="email"]', 'input[name="username"]'],
                email,
                timeout=10000,
            )
            pass_ok = await bumper_mod.smart_fill(
                page, ['input[type="password"]', 'input[name="password"]'], password, timeout=8000
            )
            if not email_ok or not pass_ok:
                raise RuntimeError("unexpected_dom: login fields missing")
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
                raise RuntimeError("login failed — still on /login")
            steps.append("open_import")
            await page.goto(IMPORT_UI, wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(1.2, 2.0)
            if "/login" in page.url:
                raise RuntimeError("import UI bounced to /login")
            area = page.locator("textarea").first
            if not await area.count():
                raise RuntimeError("unexpected_dom: no textarea on Agent Import")
            await area.fill(json.dumps(payload, ensure_ascii=False))
            steps.append("pasted_json")
            validate_btn = page.locator(
                'button:has-text("Validate"), button:has-text("Valider"), '
                'button:has-text("Check"), input[value*="Validate" i]'
            ).first
            if not await validate_btn.count():
                raise RuntimeError("unexpected_dom: Validate button missing")
            await bumper_mod.human_click(page, validate_btn)
            await bumper_mod.human_sleep(1.5, 2.5)
            result_text = ""
            script_json = None
            loc = page.locator("#agent-import-result, script#agent-import-result")
            if await loc.count():
                raw = (await loc.first.inner_text()) or ""
                result_text = raw
                try:
                    script_json = json.loads(raw)
                except Exception:
                    script_json = None
            if not result_text:
                result_text = (await page.inner_text("body")) or ""
            steps.append("validated")
            report["agent_import_result_text"] = result_text[:8000]
            report["agent_import_result_json"] = script_json
            report["classify"] = _classify_result(
                json.dumps(script_json, ensure_ascii=False) if script_json else result_text
            )
            report["commit_clicked"] = False
            report["ok"] = True
            report["steps"] = steps
        except Exception as exc:
            report["ok"] = False
            report["error"] = str(exc)
            report["steps"] = steps
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cls = (report.get("classify") or {}).get("update_or_duplicate")
    print(
        f"ok={report.get('ok')} classify={cls} commit=False "
        f"error={report.get('error')}"
    )
    return 0 if report.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
