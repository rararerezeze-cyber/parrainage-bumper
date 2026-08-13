#!/usr/bin/env python3
"""READ-ONLY compare: live Kraken announcement vs OPERATOR_VALIDATED values.

No save, no boost, no last_super_run. One session per platform.
Stop on 403/429/CAPTCHA/auth/unexpected DOM.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.operator_overrides import resolve_effective_value  # noqa: E402
from lib.paths import DATA_DIR  # noqa: E402

OUT = DATA_DIR / "captures" / "kraken-live-compare.json"
FORBIDDEN = ("4hpz4gdy", "proinvite.kraken.com", "20 € en Bitcoin", "20€ en Bitcoin")
EXPECTED_FIELDS = ("personal_code", "personal_link", "referee_reward")


def _expected() -> dict[str, str]:
    out = {}
    for f in EXPECTED_FIELDS:
        eff = resolve_effective_value("kraken", f, platform=None)
        out[f] = str(eff.value or "")
    return out


def _classify(observed: dict[str, str | None], expected: dict[str, str], *, error: str | None) -> str:
    if error:
        low = error.lower()
        if any(x in low for x in ("captcha", "403", "429", "challenge", "cloudflare")):
            return "AUTH_BLOCKED" if "login" in low or "auth" in low else "DOM_BLOCKED"
        if any(x in low for x in ("auth", "login", "password", "credential")):
            return "AUTH_BLOCKED"
        if "unexpected_dom" in low or "introuvable" in low or "not found" in low:
            return "DOM_BLOCKED"
        return "DOM_BLOCKED"
    blob = " ".join(str(v or "") for v in observed.values())
    if any(bad in blob for bad in FORBIDDEN):
        return "REAL_SAFE_DIFF"
    diffs = []
    for f, exp in expected.items():
        got = (observed.get(f) or "").strip()
        if exp and got and exp not in got and got not in exp:
            diffs.append(f)
        elif exp and not got:
            diffs.append(f)
    return "REAL_SAFE_DIFF" if diffs else "NO_SAFE_DIFF"


def _hay_to_fields(hay: str, expected: dict[str, str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for f, exp in expected.items():
        out[f] = exp if exp and exp in (hay or "") else None
        if f == "personal_code":
            for cand in ("cpbrgddy", "4hpz4gdy"):
                if cand in (hay or ""):
                    out[f] = cand
                    break
        if f == "personal_link":
            for cand in (
                "https://invite.kraken.com/JDNW/s5qudqe4",
                "https://proinvite.kraken.com/9f1e/lqbuov8u",
            ):
                if cand in (hay or ""):
                    out[f] = cand
                    break
        if f == "referee_reward":
            if "200 € en cryptomonnaies" in (hay or "") or "200 €" in (hay or ""):
                if "20 € en Bitcoin" not in (hay or "") or "200" in (hay or ""):
                    out[f] = "200 € en cryptomonnaies" if "200 € en cryptomonnaies" in (hay or "") else "200 €"
            elif "20 € en Bitcoin" in (hay or ""):
                out[f] = "20 € en Bitcoin"
    return out


async def _compare_browser(platform: str, expected: dict[str, str]) -> dict:
    from lib.auth_policy import classify_auth_failure, should_stop_platform

    try:
        if platform == "parrainage-co":
            from platforms.parrainage_co.writer import (
                _cfg as _pco_cfg,
            )
            from platforms.parrainage_co import writer as w

            # parrainage_co uses bumper CONFIG
            import bumper as bumper_mod
            from playwright.async_api import async_playwright

            plan = w.build_write_plan("parrainage-co", "kraken", "fr")
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR"],
                )
                ctx = await bumper_mod.new_context(browser)
                page = await ctx.new_page()
                try:
                    await w._login(page, ctx, bumper_mod.CONFIG["parrainage"])
                    edit = await w._resolve_edit_url(page, plan)
                    await page.goto(edit, wait_until="domcontentloaded", timeout=60000)
                    await bumper_mod.human_sleep(1.0, 1.6)
                    hay = await w._reread_account_fields(page)
                    pub = ""
                    if plan.announcement_url:
                        from lib.http_fetch import fetch_text

                        pub = fetch_text(plan.announcement_url)
                    observed = _hay_to_fields(hay + "\n" + pub, expected)
                    return {
                        "platform": platform,
                        "ok": True,
                        "edit_url": edit,
                        "observed": observed,
                        "class": _classify(observed, expected, error=None),
                    }
                except Exception as exc:
                    kind = classify_auth_failure(str(exc))
                    if should_stop_platform(kind):
                        return {
                            "platform": platform,
                            "ok": False,
                            "error": f"STOP_{kind.value}: {exc}",
                            "class": _classify({}, expected, error=str(exc)),
                        }
                    return {
                        "platform": platform,
                        "ok": False,
                        "error": str(exc),
                        "class": _classify({}, expected, error=str(exc)),
                    }
                finally:
                    await page.close()
                    await ctx.close()
                    await browser.close()

        if platform == "code-parrainage":
            import bumper as bumper_mod
            from playwright.async_api import async_playwright
            from platforms.code_parrainage import writer as w

            plan = w.build_write_plan("code-parrainage", "kraken", "fr")
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR"],
                )
                ctx = await bumper_mod.new_context(browser)
                page = await ctx.new_page()
                try:
                    await w._login(page, bumper_mod.CONFIG["code"])
                    edit = await w._resolve_edit_url(page, plan, bumper_mod.CONFIG["code"]["url"])
                    await page.goto(edit, wait_until="domcontentloaded", timeout=60000)
                    await bumper_mod.human_sleep(1.0, 1.6)
                    hay = await w._reread_account_fields(page)
                    observed = _hay_to_fields(hay, expected)
                    return {
                        "platform": platform,
                        "ok": True,
                        "edit_url": edit,
                        "observed": observed,
                        "class": _classify(observed, expected, error=None),
                    }
                except Exception as exc:
                    return {
                        "platform": platform,
                        "ok": False,
                        "error": str(exc),
                        "class": _classify({}, expected, error=str(exc)),
                    }
                finally:
                    await page.close()
                    await ctx.close()
                    await browser.close()

        if platform == "1parrainage":
            import bumper as bumper_mod
            from playwright.async_api import async_playwright
            from platforms.oneparrainage import writer as w

            plan = w.build_write_plan("1parrainage", "kraken", "fr")
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR"],
                )
                ctx = await bumper_mod.new_context(browser)
                page = await ctx.new_page()
                try:
                    await w._login(page, w._cfg())
                    edit = await w._resolve_edit_url(page, plan)
                    await page.goto(edit, wait_until="domcontentloaded", timeout=60000)
                    await bumper_mod.human_sleep(1.0, 1.6)
                    hay = await w._reread_account_fields(page)
                    from lib.http_fetch import fetch_text

                    pub = fetch_text(
                        (plan.announcement_url or w.PUBLIC_LIST).split("#")[0]
                    )
                    observed = _hay_to_fields(hay + "\n" + pub, expected)
                    return {
                        "platform": platform,
                        "ok": True,
                        "edit_url": edit,
                        "observed": observed,
                        "class": _classify(observed, expected, error=None),
                    }
                except Exception as exc:
                    return {
                        "platform": platform,
                        "ok": False,
                        "error": str(exc),
                        "class": _classify({}, expected, error=str(exc)),
                    }
                finally:
                    await page.close()
                    await ctx.close()
                    await browser.close()

        if platform == "referralcodes":
            from lib.http_fetch import fetch_text
            from platforms.referralcodes.writer import PUBLIC_PROFILE

            hay = fetch_text(PUBLIC_PROFILE)
            observed = _hay_to_fields(hay, expected)
            # Public profile compare only — no login unless missing
            cls = _classify(observed, expected, error=None)
            return {
                "platform": platform,
                "ok": True,
                "method": "public_profile",
                "url": PUBLIC_PROFILE,
                "observed": observed,
                "class": cls,
            }
    except Exception as exc:
        return {
            "platform": platform,
            "ok": False,
            "error": str(exc),
            "class": _classify({}, expected, error=str(exc)),
        }
    return {"platform": platform, "ok": False, "error": "unknown_platform", "class": "DOM_BLOCKED"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--platforms",
        default="parrainage-co,code-parrainage,1parrainage,referralcodes",
    )
    args = p.parse_args()
    expected = _expected()
    plats = [x.strip() for x in args.platforms.split(",") if x.strip()]
    rows = []
    for plat in plats:
        print(f"=== compare {plat} (read-only) ===")
        row = asyncio.run(_compare_browser(plat, expected))
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2))
        if str(row.get("error") or "").startswith("STOP_"):
            print("STOP remaining platforms")
            break
    report = {
        "at": datetime.now(timezone.utc).isoformat(),
        "live": False,
        "expected": expected,
        "forbidden_must_not_publish": list(FORBIDDEN),
        "platforms": rows,
        "first_real_safe_diff": next(
            (r["platform"] for r in rows if r.get("class") == "REAL_SAFE_DIFF"),
            None,
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report={OUT}")
    print(f"FIRST_REAL_SAFE_DIFF={report['first_real_safe_diff']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
