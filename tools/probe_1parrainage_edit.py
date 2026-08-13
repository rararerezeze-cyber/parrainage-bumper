#!/usr/bin/env python3
"""Discover 1Parrainage login + edit pages BEFORE canary day.

Modes:
  --public   map public list + login URL (no credentials)
  --auth     login with ONEPARRAINAGE_* and inventory edit URLs (READ-ONLY, no save)

Never bypasses CAPTCHA/anti-bot. Stops on 403/429/challenge.
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

from lib.http_fetch import fetch_result  # noqa: E402
from lib.inventory import list_mapping_refs  # noqa: E402
from lib.renderer import MappingRepository  # noqa: E402

OUT = ROOT / "data" / "captures" / "1parrainage-edit-map.json"
PUBLIC_LIST = "https://www.1parrainage.com/listeannonces_98906_Adrien89.php"
LOGIN = "https://www.1parrainage.com/login"


def public_discovery() -> dict:
    login = fetch_result(LOGIN, timeout=25)
    lst = fetch_result(PUBLIC_LIST, timeout=25)
    (ROOT / "data" / "captures" / "1parrainage-login.html").write_text(
        login.body[:80000] if login.body else "", encoding="utf-8"
    )
    offer_ids = sorted(set(re.findall(r"id=(\d+)", lst.body or "")))
    repo = MappingRepository()
    mapped = []
    for ref in list_mapping_refs():
        if ref.platform != "1parrainage":
            continue
        m = repo.load(ref.platform, ref.program, ref.language)
        mapped.append(
            {
                "program": ref.program,
                "announcement_url": m.announcement_url,
                "edit_url": m.edit_url,
            }
        )
    has_email = bool(re.search(r'type=["\']email["\']|name=["\'][^"\']*mail', login.body or "", re.I))
    has_pass = bool(re.search(r'type=["\']password["\']', login.body or "", re.I))
    return {
        "mode": "public",
        "login_url": LOGIN,
        "login_http": login.status,
        "login_has_email_field": has_email,
        "login_has_password_field": has_pass,
        "public_list": PUBLIC_LIST,
        "list_http": lst.status,
        "list_offer_id_count": len(offer_ids),
        "mappings": mapped,
        "auth_probe_required": True,
        "secrets": ["ONEPARRAINAGE_EMAIL", "ONEPARRAINAGE_PASSWORD"],
        "edit_discovery_hypothesis": [
            "Public /login is the real login (connexion.php is 404)",
            "Public list exposes offer ids via parrain_definit.php?id_par=98906&id=N",
            "Authenticated edit lives in member area after /login (espace parrain)",
            "Never click Boost / Remonter — content edit only",
        ],
        "stop_on": ["403", "429", "CAPTCHA", "auth", "unexpected_dom"],
    }


async def auth_discovery() -> dict:
    from lib.auth_policy import classify_auth_failure, should_stop_platform
    from platforms.oneparrainage.writer import (
        MEMBER_CANDIDATES,
        _cfg,
        _detect_challenge,
        _login,
    )

    cfg = _cfg()
    if not cfg.get("email") or not cfg.get("password"):
        return {
            "mode": "auth",
            "ok": False,
            "error": "ONEPARRAINAGE_EMAIL/PASSWORD missing",
        }

    import bumper as bumper_mod
    from playwright.async_api import async_playwright

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
                "--lang=fr-FR",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = await bumper_mod.new_context(browser)
        page = await ctx.new_page()
        try:
            await _login(page, cfg)
            report["login"] = "ok"
            report["login_landed"] = page.url
            for url in MEMBER_CANDIDATES:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await bumper_mod.human_sleep(1.0, 1.6)
                    await _detect_challenge(page)
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
                                || label.includes('modif') || href.includes('modif')
                                || href.includes('editer')) {
                              out.push({href, label: label.slice(0,100)});
                            }
                          }
                          return out;
                        }
                        """
                    )
                    report["pages"].append(
                        {
                            "url": url,
                            "final_url": page.url,
                            "body_len": len(body or ""),
                            "edit_candidates": edits or [],
                            "snippet": (body or "")[:400],
                        }
                    )
                    for e in edits or []:
                        if e.get("href") and e["href"] not in report["edit_urls"]:
                            report["edit_urls"].append(e["href"])
                except Exception as exc:  # noqa: BLE001
                    kind = classify_auth_failure(str(exc))
                    report["errors"].append({"url": url, "error": str(exc), "kind": kind.value})
                    if should_stop_platform(kind):
                        report["stopped"] = kind.value
                        break
            known_edit = "https://www.1parrainage.com/espace_parrain/parrainages/edit/2541207/"
            try:
                await page.goto(known_edit, wait_until="domcontentloaded", timeout=60000)
                await bumper_mod.human_sleep(1.2, 1.8)
                await _detect_challenge(page)
                ck = await page.evaluate(
                    """
                    () => {
                      const id = 'edit_parrainage_presentation';
                      const inst = window.CKEDITOR && CKEDITOR.instances && CKEDITOR.instances[id];
                      const data = inst ? (inst.getData() || '') : '';
                      const ta = document.getElementById(id);
                      const tv = ta ? (ta.value || '') : '';
                      const blob = data || tv;
                      return {
                        url: location.href,
                        bounced_login: (location.href || '').includes('/login'),
                        has_ckeditor: !!inst,
                        has_textarea: !!ta,
                        len: blob.length,
                        has_s5qudqe4: blob.includes('s5qudqe4'),
                        has_4jdp7sea: blob.includes('4jdp7sea'),
                        has_cpbrgddy: blob.includes('cpbrgddy'),
                        form_edit: !!document.querySelector('form[action*="parrainages/edit"]'),
                      };
                    }
                    """
                )
                report["known_edit_probe"] = ck
                report["save_clicked"] = False
                if ck.get("url") and ck["url"] not in report["edit_urls"]:
                    report["edit_urls"].append(ck["url"])
            except Exception as exc:  # noqa: BLE001
                report["known_edit_probe"] = {"error": str(exc)}
            report["ok"] = report["login"] == "ok"
            report["conclusion"] = (
                "EDIT_URLS_FOUND" if report["edit_urls"] else "LOGIN_OK_NO_EDIT_URLS_YET"
            )
        except Exception as exc:  # noqa: BLE001
            kind = classify_auth_failure(str(exc))
            report["error"] = str(exc)
            report["login"] = "failed"
            report["kind"] = kind.value
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

    out: dict = {"platform": "1parrainage"}
    if not args.no_public:
        out["public"] = public_discovery()
        pub = out["public"]
        print(
            f"login_http={pub['login_http']} fields={pub['login_has_email_field']}/"
            f"{pub['login_has_password_field']} list_ids={pub['list_offer_id_count']}"
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
