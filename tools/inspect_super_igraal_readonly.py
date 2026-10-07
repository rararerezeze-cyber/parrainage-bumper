"""Narrow authenticated discovery of iGraal's announcement editor; no save."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.renderer import MappingRepository
from lib.super_parrain_resource import assert_announcement_edit_url, assert_not_codes_promo_form
from platforms.super_parrain.writer import _find_program_edit_url, _login_super, _page_blocked_reason


async def inspect() -> dict:
    import bumper
    from playwright.async_api import async_playwright

    cfg = bumper.CONFIG["super"]
    mapping = MappingRepository().load("super-parrain", "igraal", "fr")
    report = {"mode": "READ_ONLY", "save_clicked": False, "at": datetime.now(timezone.utc).isoformat(),
              "public_listing": mapping.announcement_url, "ok": False}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await bumper.new_context(browser)
        page = await ctx.new_page()
        try:
            await _login_super(page, cfg)
            url = await _find_program_edit_url(page, cfg["url"], "igraal", mapping.announcement_url)
            assert_announcement_edit_url(url)
            await page.goto(url, wait_until="domcontentloaded")
            blocked = _page_blocked_reason(await page.inner_text("body"), page.url)
            if blocked:
                raise RuntimeError(f"blocked:{blocked}")
            # Only announcement textareas. Never dump hidden inputs/auth fields.
            fields = await page.locator("textarea").evaluate_all(
                "els => els.map(t => ({name:t.name, id:t.id, value:t.value}))"
            )
            assert_not_codes_promo_form(url=page.url, form_names=[f["name"] for f in fields])
            report.update(ok=True, edit_url=page.url, body_fields=fields)
        except Exception:
            # No arbitrary exception/URL dump: credentials are never evidence.
            report["error"] = "announcement_editor_discovery_failed_or_blocked"
        finally:
            await browser.close()
    return report


if __name__ == "__main__":
    report = asyncio.run(inspect())
    path = ROOT / "data/captures/super-igraal-readonly.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"igraal_readonly_ok={report['ok']} save_clicked=false")
    raise SystemExit(0 if report["ok"] else 1)
