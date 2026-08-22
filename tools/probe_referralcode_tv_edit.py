#!/usr/bin/env python3
"""Discover ReferralCode.tv authenticated edit pages BEFORE canary day.

Modes:
  --public     map author listings / brand pages (no login)
  --auth       login with REFERRALCODE_* and inventory edit URLs (READ-ONLY, no save)

Writes:
  data/captures/referralcode-tv-edit-map.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text  # noqa: E402
from lib.offers import OffersRepository  # noqa: E402
from lib.renderer import MappingRepository  # noqa: E402

OUT = ROOT / "data" / "captures" / "referralcode-tv-edit-map.json"
AUTHOR = "https://www.referralcode.tv/author/thesuperreff/"


def public_discovery() -> dict:
    offers = OffersRepository()
    html = fetch_text(AUTHOR)
    (ROOT / "data" / "captures" / "referralcode-tv-author.html").write_text(
        html, encoding="utf-8"
    )
    listings = sorted(
        set(re.findall(r'href="(https://www\.referralcode\.tv/referral-code/[^"]+)"', html, re.I))
    )
    brands = sorted(
        set(re.findall(r'href="(https://www\.referralcode\.tv/brand/[^"]+)"', html, re.I))
    )
    sid_map: dict[str, list[str]] = {}
    for u in listings + brands:
        m = re.search(r"[?&]__sid=(\d+)", u)
        if m:
            sid_map.setdefault(m.group(1), []).append(u)

    # Match known mappings announcement URLs
    repo = MappingRepository()
    from lib.inventory import list_mapping_refs

    mapped = []
    for ref in list_mapping_refs():
        if ref.platform != "referralcode-tv":
            continue
        m = repo.load(ref.platform, ref.program, ref.language)
        mapped.append(
            {
                "program": ref.program,
                "announcement_url": m.announcement_url,
                "edit_url": m.edit_url,
                "sid": (
                    re.search(r"[?&]__sid=(\d+)", m.announcement_url or "")
                    and re.search(r"[?&]__sid=(\d+)", m.announcement_url or "").group(1)
                ),
            }
        )

    return {
        "mode": "public",
        "author": AUTHOR,
        "listing_count": len(listings),
        "brand_count": len(brands),
        "listings_sample": listings[:30],
        "brands_sample": brands[:30],
        "sid_map_count": len(sid_map),
        "mappings": mapped,
        "edit_discovery_hypothesis": [
            "Public author page exposes listing URLs + __sid, NOT edit forms",
            "Authenticated edit lives under my-account listings (Listeo-style)",
            "After login: /my-account/?tab=listings → links containing edit / modifier",
            "Fallback: listing dashboard card actions (Edit Listing)",
            "add-referral-code/ is CREATE only — never use for update canary",
        ],
        "auth_probe_required": True,
        "secrets": ["REFERRALCODE_EMAIL", "REFERRALCODE_PASSWORD"],
        "known_from_bumper": {
            "login": "https://www.referralcode.tv/login/",
            "listings": "https://www.referralcode.tv/my-account/?tab=listings",
            "boost_button": "#cliccami (bump only — not content edit)",
        },
    }


async def auth_discovery() -> dict:
    """READ-ONLY login + collect edit URLs. No save/boost."""
    import bumper as bumper_mod
    from playwright.async_api import async_playwright

    cfg = bumper_mod.CONFIG["referralcode"]
    if not cfg.get("email") or not cfg.get("password"):
        return {
            "mode": "auth",
            "ok": False,
            "error": "REFERRALCODE_EMAIL/PASSWORD missing",
        }

    report: dict = {
        "mode": "auth",
        "ok": False,
        "login": "pending",
        "pages": [],
        "edit_urls": [],
        "errors": [],
    }
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
            await page.goto(
                f"{cfg['url']}/login/", wait_until="domcontentloaded", timeout=60000
            )
            await bumper_mod.human_sleep(1, 2)
            ok_email = await bumper_mod.smart_fill(
                page,
                [
                    'input[type="email"]',
                    'input[name="email"]',
                    'input[placeholder*="mail" i]',
                    'input[name="username"]',
                ],
                cfg["email"],
                timeout=15000,
            )
            if not ok_email:
                raise RuntimeError("email field not found")
            await bumper_mod.smart_fill(
                page,
                ['input[type="password"]', 'input[name="password"]'],
                cfg["password"],
                timeout=10000,
            )
            await bumper_mod.human_click(
                page,
                page.locator(
                    'button:has-text("SIGN IN"), button[type="submit"], input[type="submit"]'
                ).first,
            )
            try:
                await page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
            except Exception:
                pass
            try:
                # Same fragile "networkidle" wait as bumper.py::run_referralcode
                # (see comment there) -- confirmed to intermittently hang the
                # full 30s default timeout on this site (docs/HANDOFF_CODEX.md
                # blocker #2, GH run 31648937632: "Timeout 30000ms exceeded"
                # on login). domcontentloaded with a bounded timeout is
                # enough to safely reach the point where page.url can be
                # checked below.
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            if "/login" in page.url:
                raise RuntimeError("login failed")
            report["login"] = "ok"
            report["login_landed"] = page.url

            candidates = [
                f"{cfg['url']}/my-account/?tab=listings",
                f"{cfg['url']}/my-account/",
                f"{cfg['url']}/my-account/?tab=dashboard",
                "https://www.referralcode.tv/my-account/?dashboard=listings",
            ]
            for url in candidates:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    await bumper_mod.human_sleep(1.5, 2.5)
                    body = await page.inner_text("body")
                    edits = await page.evaluate(
                        """
                        () => {
                          const out = [];
                          for (const a of document.querySelectorAll('a[href]')) {
                            const href = a.href || '';
                            const label = ((a.innerText||'')+' '+href).toLowerCase();
                            if (!href.startsWith('http')) continue;
                            if (label.includes('edit') || href.includes('edit')
                                || href.includes('action=edit') || label.includes('modifier')
                                || href.includes('listing_id') || href.includes('job_listing')) {
                              out.push({href, label: label.slice(0,100)});
                            }
                          }
                          return out;
                        }
                        """
                    )
                    page_info = {
                        "url": url,
                        "final_url": page.url,
                        "body_len": len(body or ""),
                        "edit_candidates": edits or [],
                        "has_cliccami": bool(await page.locator("#cliccami").count()),
                        "snippet": (body or "")[:500],
                    }
                    report["pages"].append(page_info)
                    for e in edits or []:
                        if e.get("href") and e["href"] not in report["edit_urls"]:
                            report["edit_urls"].append(e["href"])
                    # screenshot per page
                    safe = re.sub(r"[^a-z0-9]+", "-", url.lower())[:60]
                    try:
                        await page.screenshot(
                            path=str(ROOT / "data" / "captures" / f"rctv-auth-{safe}.png"),
                            full_page=True,
                        )
                    except Exception:
                        pass
                except Exception as exc:  # noqa: BLE001
                    report["errors"].append({"url": url, "error": str(exc)})

            # Open edit candidates READ-ONLY and detect fields (no save).
            field_probes = []
            edit_only = [h for h in report["edit_urls"] if "eid=" in (h or "")]
            for href in (edit_only or report["edit_urls"])[:30]:
                try:
                    await page.goto(href, wait_until="domcontentloaded", timeout=45000)
                    await bumper_mod.human_sleep(1.0, 1.8)
                    fields = await page.evaluate(
                        """
                        () => {
                          const val = (sel) => {
                            const el = document.querySelector(sel);
                            return el ? (el.value || el.innerText || '').trim() : '';
                          };
                          const title = val('input[name="form[post_title]"], #form_post_title, input.form_post_title');
                          const code = val('input[name="custom[code]"], input.field-code, #field-code');
                          const link = val('input[name="custom[buy_link]"], input.field-buy_link, #field-buy_link');
                          const content = val('textarea[name="form[post_content]"], textarea#form_post_content');
                          const blob = (title + ' ' + content + ' ' + link).toLowerCase();
                          let program = null;
                          const keys = [
                            ['kraken','kraken'], ['okx','okx'], ['paypal','paypal'],
                            ['robinhood','robinhood'], ['whatnot','whatnot'], ['wise','wise'],
                            ['stake','stake'], ['gemini','gemini'], ['bybit','bybit'],
                            ['swissborg','swissborg'], ['airbnb','airbnb'], ['joko','joko'],
                          ];
                          for (const [prog, key] of keys) {
                            if (blob.includes(key)) { program = prog; break; }
                          }
                          const eidM = (location.search || '').match(/[?&]eid=(\\d+)/);
                          return {
                            url: location.href,
                            eid: eidM ? eidM[1] : null,
                            program,
                            post_title: title.slice(0, 180),
                            code: code.slice(0, 80),
                            buy_link: link.slice(0, 200),
                            content_len: content.length,
                            content_head: content.slice(0, 240),
                            page_title: document.title,
                          };
                        }
                        """
                    )
                    field_probes.append(fields)
                except Exception as exc:  # noqa: BLE001
                    field_probes.append({"url": href, "error": str(exc)})
            report["field_probes"] = field_probes
            report["ok"] = bool(report["edit_urls"]) or bool(report["pages"])
            report["conclusion"] = (
                "EDIT_URLS_FOUND"
                if report["edit_urls"]
                else "NO_EDIT_URLS_YET — inspect pages[] snippets / screenshots"
            )
        except Exception as exc:  # noqa: BLE001
            report["error"] = str(exc)
            report["login"] = "failed"
        finally:
            await page.close()
            await ctx.close()
            await browser.close()
    return report


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--public", action="store_true", default=True)
    p.add_argument("--auth", action="store_true")
    p.add_argument("--no-public", action="store_true")
    args = p.parse_args()

    out: dict = {"platform": "referralcode-tv"}
    if not args.no_public:
        out["public"] = public_discovery()
        print(
            f"public listings={out['public']['listing_count']} "
            f"mappings={len(out['public']['mappings'])}"
        )
    if args.auth:
        out["auth"] = asyncio.run(auth_discovery())
        print(
            f"auth ok={out['auth'].get('ok')} "
            f"edit_urls={len(out['auth'].get('edit_urls') or [])} "
            f"conclusion={out['auth'].get('conclusion') or out['auth'].get('error')}"
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
