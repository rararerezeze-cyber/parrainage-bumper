#!/usr/bin/env python3
"""READ-ONLY: prove parrainage.co PC-off login+edit via bumper.solve_turnstile.

Forces password path (no RM cookie). Never clicks Enregistrer/boost.
  python -u tools/probe_parrainage_co_pc_off.py
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

import bumper as bumper_mod  # noqa: E402

OUT = ROOT / "data" / "captures" / "parrainage-co-pc-off-probe.json"
EDIT = "https://parrainage.co/account/offers/edit/113735"
OFFERS = "https://parrainage.co/account/offers"
LOGIN = "https://parrainage.co/account/login"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def main() -> int:
    email = os.environ.get("PARRAINAGE_CO_EMAIL") or ""
    password = os.environ.get("PARRAINAGE_CO_PASSWORD") or ""
    twocaptcha = bool(os.environ.get("TWOCAPTCHA_KEY"))
    report: dict = {
        "platform": "parrainage-co",
        "live": False,
        "save_clicked": False,
        "cookie_used": False,
        "login_path": "bumper.smart_login_parrainage",
        "turnstile": "bumper.solve_turnstile",
        "twocaptcha_present": twocaptcha,
        "at": _now(),
    }
    if not email or not password:
        report["ok"] = False
        report["error"] = "AUTH_REQUIRED: PARRAINAGE_CO_EMAIL/PASSWORD"
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 1

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR"],
        )
        ctx = await bumper_mod.new_context(browser)
        page = await ctx.new_page()
        try:
            await page.goto(LOGIN, wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(1.5, 2.5)
            ok = await bumper_mod.smart_login_parrainage(page, email, password)
            report["login_ok"] = bool(ok)
            report["url_after_login"] = page.url
            if not ok or "/login" in (page.url or ""):
                report["ok"] = False
                report["error"] = "login_failed"
                try:
                    await page.screenshot(path="debug_parrainage_pc_off_login.png")
                except Exception:
                    pass
                OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps({k: report[k] for k in report if k != "fields"}, ensure_ascii=False))
                return 2

            await page.goto(OFFERS, wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(1.0, 1.6)
            report["offers_url"] = page.url
            report["offers_ok"] = "/login" not in (page.url or "") and "/account/offers" in (page.url or "")

            await page.goto(EDIT, wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(1.2, 2.0)
            report["edit_url"] = page.url
            bounced = "/login" in (page.url or "")
            report["edit_bounced_login"] = bounced
            areas = page.locator("textarea")
            n = await areas.count()
            report["textarea_count"] = n
            blob = ""
            if n:
                try:
                    blob = await areas.first.input_value()
                except Exception:
                    blob = ""
            report["edit_len"] = len(blob)
            report["has_cpbrgddy"] = "cpbrgddy" in blob
            report["has_s5qudqe4"] = "s5qudqe4" in blob
            report["save_clicked"] = False
            report["ok"] = bool(
                report.get("login_ok")
                and report.get("offers_ok")
                and not bounced
                and n > 0
            )
            report["gh_login"] = "PROVEN" if report.get("login_ok") else "FAILED"
            report["gh_edit_access"] = "PROVEN" if report["ok"] else "FAILED"
        except Exception as exc:
            report["ok"] = False
            report["error"] = str(exc)
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"ok={report.get('ok')} login={report.get('gh_login')} "
        f"edit={report.get('gh_edit_access')} twocaptcha={twocaptcha}"
    )
    return 0 if report.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
