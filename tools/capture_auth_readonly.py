#!/usr/bin/env python3
"""Capture authentifiee READ-ONLY des annonces (parrainage.co, code-parrainage, referralcode.tv).

Reutilise la config env de bumper.py. Aucun bump/boost/enregistrer.
Ne loggue jamais credentials/cookies/tokens.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.offers import OffersRepository
from lib.template_builder import build_from_text, detect_platform_values, write_build_result

# Import utilitaires bumper sans executer main
sys.path.insert(0, str(ROOT))
import bumper as bumper_mod  # noqa: E402

REPORT_DIR = ROOT / "data" / "captures"


def _has_creds(site: str) -> bool:
    if site == "parrainage":
        return bool(
            os.environ.get("PARRAINAGE_CO_RM_COOKIE")
            or (os.environ.get("PARRAINAGE_CO_EMAIL") and os.environ.get("PARRAINAGE_CO_PASSWORD"))
        )
    if site == "code":
        return bool(os.environ.get("CODE_PARRAINAGE_EMAIL") and os.environ.get("CODE_PARRAINAGE_PASSWORD"))
    if site == "referralcode":
        return bool(os.environ.get("REFERRALCODE_EMAIL") and os.environ.get("REFERRALCODE_PASSWORD"))
    return False


def _slug_from_text(name: str, offers: OffersRepository) -> str | None:
    key = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    for o in offers.load_all():
        on = re.sub(r"[^a-z0-9]+", "", (o.get("name") or "").lower())
        lk = o.get("lk") or ""
        if on and on == key:
            return lk
        if lk and re.sub(r"[^a-z0-9]+", "", lk) == key:
            return lk
    aliases = {
        "traderepublic": "traderepublic",
        "traderepublique": "traderepublic",
        "boursobank": "boursobank",
        "boursorama": "boursobank",
    }
    return aliases.get(key)


def _save_result(platform: str, program: str, language: str, text: str, url: str | None, offer) -> dict:
    result = build_from_text(
        platform=platform,
        program=program,
        language=language,
        golden_text=text,
        offer=offer,
        announcement_url=url,
    )
    paths = write_build_result(result)
    return {
        "program": program,
        "status": "ok",
        "mutable": result.mutable_fields,
        "sync_mode": result.sync_mode,
        "paths": {k: str(v.relative_to(ROOT)).replace("\\", "/") for k, v in paths.items()},
    }


async def capture_parrainage_co(browser, offers: OffersRepository) -> dict:
    """READ-ONLY: /account/offers — pas de boost-all."""
    cfg = bumper_mod.CONFIG["parrainage"]
    platform = "parrainage-co"
    report = {"platform": platform, "items": [], "errors": []}
    ctx = await bumper_mod.new_context(browser)
    page = await ctx.new_page()
    try:
        rm_cookie = cfg.get("rm_cookie", "")
        email = cfg.get("email", "")
        password = cfg.get("password", "")
        if rm_cookie:
            await ctx.add_cookies(
                [
                    {
                        "name": "parrainageco_rm",
                        "value": rm_cookie.strip(),
                        "domain": "parrainage.co",
                        "path": "/",
                    }
                ]
            )
        await page.goto(f"{cfg['url']}/account/offers", wait_until="domcontentloaded", timeout=60000)
        await bumper_mod.human_sleep(2, 3)
        if "/login" in page.url:
            if not email or not password:
                raise RuntimeError("session requise (cookie/login manquant)")
            await page.goto(f"{cfg['url']}/account/login", wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(1, 2)
            ok = await bumper_mod.smart_login_parrainage(page, email, password)
            if not ok:
                raise RuntimeError("login echoue")
            await page.goto(f"{cfg['url']}/account/offers", wait_until="domcontentloaded", timeout=60000)
            await bumper_mod.human_sleep(2, 3)

        # Extraire liens edit / cartes annonces
        cards = await page.evaluate(
            """
            () => {
              const out = [];
              const anchors = Array.from(document.querySelectorAll('a[href*="edit"], a[href*="offer"], a[href*="annonce"]'));
              for (const a of anchors) {
                const href = a.href || '';
                if (!href || href.includes('boost')) continue;
                const text = (a.innerText || a.textContent || '').trim();
                const block = a.closest('tr, li, article, .card, .offer, .row') || a.parentElement;
                const body = (block ? block.innerText : text) || '';
                out.push({href, text: text.slice(0,200), body: body.slice(0,4000)});
              }
              // textareas / descriptions
              document.querySelectorAll('textarea, [contenteditable="true"]').forEach(el => {
                const v = (el.value || el.innerText || '').trim();
                if (v.length > 40) out.push({href: location.href, text: 'field', body: v.slice(0,8000)});
              });
              return out;
            }
            """
        )
        # Also dump page text segments for matching
        body_text = await page.inner_text("body")
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "parrainage-co-raw.txt").write_text(body_text[:50000], encoding="utf-8")

        seen = set()
        for card in cards:
            body = (card.get("body") or "").strip()
            if len(body) < 30:
                continue
            # Guess program from first line / title
            title = (card.get("text") or body.split("\n", 1)[0]).strip()
            slug = _slug_from_text(title, offers)
            if not slug:
                # try any offer name contained in body
                for o in offers.load_all():
                    n = o.get("name") or ""
                    if n and n.lower() in body.lower():
                        slug = o.get("lk")
                        break
            if not slug or slug in seen:
                continue
            seen.add(slug)
            try:
                offer = offers.get_by_slug(slug)
            except KeyError:
                offer = None
            try:
                item = _save_result(platform, slug, "fr", body, card.get("href"), offer)
                report["items"].append(item)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append({"program": slug, "error": str(exc)})

        if not report["items"]:
            report["errors"].append(
                {
                    "error": "aucune annonce structuree extraite — DOM a cartographier",
                    "hint": "raw dump: data/captures/parrainage-co-raw.txt",
                }
            )
    except Exception as exc:  # noqa: BLE001
        report["errors"].append({"error": str(exc)})
    finally:
        await page.close()
        await ctx.close()
    return report


async def capture_code_parrainage(browser, offers: OffersRepository) -> dict:
    """READ-ONLY: /moncompte — pas de clic Actualiser."""
    cfg = bumper_mod.CONFIG["code"]
    platform = "code-parrainage"
    report = {"platform": platform, "items": [], "errors": []}
    ctx = await bumper_mod.new_context(browser)
    page = await ctx.new_page()
    try:
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle", timeout=60000)
        await bumper_mod.human_sleep(1, 2)
        await bumper_mod.robust_fill(page, 'input[type="email"]', cfg["email"])
        await bumper_mod.robust_fill(page, 'input[type="password"]', cfg["password"])
        if not await bumper_mod.solve_slider(page):
            raise RuntimeError("slider captcha non resolu (pas de contournement)")
        await asyncio.sleep(random.uniform(0.8, 1.5))
        await bumper_mod.human_click(
            page, page.locator('button:has-text("Se connecter"), button[type="submit"]').first
        )
        try:
            await page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
        except Exception:
            pass
        await page.wait_for_load_state("networkidle")
        if not await bumper_mod.verify_login(page, "/login", platform):
            raise RuntimeError("login echoue")

        await page.goto(f"{cfg['url']}/moncompte", wait_until="networkidle", timeout=60000)
        await bumper_mod.human_sleep(2, 3)
        body_text = await page.inner_text("body")
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "code-parrainage-raw.txt").write_text(body_text[:50000], encoding="utf-8")

        cards = await page.evaluate(
            """
            () => {
              const out = [];
              // blocs carte typiques
              const nodes = document.querySelectorAll(
                '.card, .annonce, .offer, article, .list-group-item, tr, .row'
              );
              nodes.forEach(n => {
                const t = (n.innerText || '').trim();
                if (t.length < 40 || t.length > 6000) return;
                if (!/code|lien|parrain|bonus|€|http/i.test(t)) return;
                const a = n.querySelector('a[href]');
                out.push({href: a ? a.href : location.href, body: t});
              });
              return out;
            }
            """
        )
        seen = set()
        for card in cards:
            body = card["body"]
            slug = None
            for o in offers.load_all():
                n = o.get("name") or ""
                if n and n.lower() in body.lower():
                    slug = o.get("lk")
                    break
            if not slug or slug in seen:
                continue
            seen.add(slug)
            try:
                offer = offers.get_by_slug(slug)
            except KeyError:
                offer = None
            try:
                item = _save_result(platform, slug, "fr", body, card.get("href"), offer)
                report["items"].append(item)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append({"program": slug, "error": str(exc)})

        if not report["items"]:
            report["errors"].append(
                {
                    "error": "aucune annonce structuree extraite — DOM a cartographier",
                    "hint": "raw dump: data/captures/code-parrainage-raw.txt",
                }
            )
    except Exception as exc:  # noqa: BLE001
        report["errors"].append({"error": str(exc)})
    finally:
        await page.close()
        await ctx.close()
    return report


async def capture_referralcode_tv(browser, offers: OffersRepository) -> dict:
    """READ-ONLY: my-account listings — pas de #cliccami."""
    cfg = bumper_mod.CONFIG["referralcode"]
    platform = "referralcode-tv"
    report = {"platform": platform, "items": [], "errors": []}
    ctx = await bumper_mod.new_context(browser)
    page = await ctx.new_page()
    try:
        await page.goto(f"{cfg['url']}/login/", wait_until="networkidle", timeout=60000)
        await bumper_mod.human_sleep(1, 2)
        EMAIL_SEL = [
            'input[type="email"]',
            'input[name="email"]',
            'input[placeholder*="mail" i]',
            'input[name="username"]',
        ]
        ok_email = await bumper_mod.smart_fill(page, EMAIL_SEL, cfg["email"], timeout=15000)
        if not ok_email:
            raise RuntimeError("champ email introuvable")
        await bumper_mod.smart_fill(
            page, ['input[type="password"]', 'input[name="password"]'], cfg["password"], timeout=10000
        )
        await bumper_mod.human_click(
            page,
            page.locator(
                'button:has-text("SIGN IN"), button[type="submit"], input[type="submit"]'
            ).first,
        )
        try:
            await page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        except Exception:
            pass
        await page.wait_for_load_state("networkidle")
        if "/login" in page.url:
            raise RuntimeError("login echoue")

        await page.goto(
            f"{cfg['url']}/my-account/?tab=listings", wait_until="networkidle", timeout=45000
        )
        await bumper_mod.human_sleep(2, 3)
        body_text = await page.inner_text("body")
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "referralcode-tv-raw.txt").write_text(body_text[:50000], encoding="utf-8")

        cards = await page.evaluate(
            """
            () => {
              const out = [];
              document.querySelectorAll(
                '.listing, .card, article, tr, .job_listing, li, .row'
              ).forEach(n => {
                const t = (n.innerText || '').trim();
                if (t.length < 30 || t.length > 8000) return;
                if (!/code|link|http|referral|bonus|€|\\$/i.test(t)) return;
                // ignore pure boost UI
                if (/^boost|cliccami/i.test(t) && t.length < 80) return;
                const a = n.querySelector('a[href]');
                out.push({href: a ? a.href : location.href, body: t});
              });
              return out;
            }
            """
        )
        seen = set()
        for card in cards:
            body = card["body"]
            slug = None
            for o in offers.load_all():
                n = o.get("name") or ""
                if n and n.lower() in body.lower():
                    slug = o.get("lk")
                    break
            if not slug or slug in seen:
                continue
            seen.add(slug)
            try:
                offer = offers.get_by_slug(slug)
            except KeyError:
                offer = None
            try:
                item = _save_result(platform, slug, "en", body, card.get("href"), offer)
                report["items"].append(item)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append({"program": slug, "error": str(exc)})

        if not report["items"]:
            report["errors"].append(
                {
                    "error": "aucune annonce structuree extraite — DOM a cartographier",
                    "hint": "raw dump: data/captures/referralcode-tv-raw.txt",
                }
            )
    except Exception as exc:  # noqa: BLE001
        report["errors"].append({"error": str(exc)})
    finally:
        await page.close()
        await ctx.close()
    return report


async def amain(sites: list[str]) -> dict:
    offers = OffersRepository()
    summary = {"sites": {}, "missing_credentials": []}
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--lang=fr-FR",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            if "parrainage" in sites:
                if _has_creds("parrainage"):
                    summary["sites"]["parrainage-co"] = await capture_parrainage_co(browser, offers)
                else:
                    summary["missing_credentials"].append("parrainage-co")
            if "code" in sites:
                if _has_creds("code"):
                    summary["sites"]["code-parrainage"] = await capture_code_parrainage(browser, offers)
                else:
                    summary["missing_credentials"].append("code-parrainage")
            if "referralcode" in sites:
                if _has_creds("referralcode"):
                    summary["sites"]["referralcode-tv"] = await capture_referralcode_tv(browser, offers)
                else:
                    summary["missing_credentials"].append("referralcode-tv")
        finally:
            await browser.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sites",
        default="parrainage,code,referralcode",
        help="Liste separee par virgules: parrainage,code,referralcode",
    )
    args = parser.parse_args()
    sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    print("READ-ONLY capture — no boost/save/actualiser")
    print("sites:", ",".join(sites))
    # Never print secret values — only presence
    for label, ok in [
        ("PARRAINAGE_CO", _has_creds("parrainage")),
        ("CODE_PARRAINAGE", _has_creds("code")),
        ("REFERRALCODE", _has_creds("referralcode")),
    ]:
        print(f"  creds {label}: {'yes' if ok else 'no'}")

    summary = asyncio.run(amain(sites))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "auth-readonly-report.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for pid, rep in summary.get("sites", {}).items():
        n = len(rep.get("items") or [])
        e = len(rep.get("errors") or [])
        print(f"  {pid}: items={n} errors={e}")
    if summary.get("missing_credentials"):
        print("missing_credentials:", ",".join(summary["missing_credentials"]))
    print("report:", out)
    # exit 0 even if partial — CI will upload dumps
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
