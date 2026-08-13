#!/usr/bin/env python3
"""LOCAL headed one-shot for 1Parrainage Kraken personal_link canary.

Visible Playwright window. No stealth. No CAPTCHA solver. No anti-bot bypass.
Manual login allowed. Session stays in this process only — never committed.

  python tools/local_headed_1parrainage.py --diagnose
  python tools/local_headed_1parrainage.py --execute

--execute runs diagnose first, then one targeted save only if OLD is exact.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text  # noqa: E402
from lib.safety import abort_forbidden_publish, snapshot_state  # noqa: E402
from platforms.oneparrainage.writer import (  # noqa: E402
    BASE,
    LOGIN_URL,
    PUBLIC_LIST,
    _detect_challenge,
    build_write_plan,
)

OFFER_ID = "100408"
OLD_LINK = "https://invite.kraken.com/JDNW/4jdp7sea"
NEW_LINK = "https://invite.kraken.com/JDNW/s5qudqe4"
CODE = "cpbrgddy"
REWARD = "200 € en cryptomonnaies"
LOCAL = ROOT / ".local-auth"
OUT = ROOT / "data" / "captures" / "1parrainage-headed-canary.json"
DIAG = ROOT / "data" / "captures" / "1parrainage-headed-diag.json"
LOGIN_WAIT_S = 600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wipe_local() -> None:
    if LOCAL.exists():
        shutil.rmtree(LOCAL, ignore_errors=True)


async def _dump_dom(page) -> dict:
    return await page.evaluate(
        """
        () => {
          const inputs = Array.from(document.querySelectorAll('input, textarea, select'))
            .map(el => ({
              tag: el.tagName.toLowerCase(),
              type: el.type || null,
              name: el.name || null,
              id: el.id || null,
              value: (el.value || '').slice(0, 400),
              placeholder: el.placeholder || null,
              visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
            }))
            .filter(x => x.type !== 'password' && x.type !== 'hidden');
          const buttons = Array.from(document.querySelectorAll(
            'button, input[type="submit"], a, [role="button"]'
          )).map(el => ({
            tag: el.tagName.toLowerCase(),
            type: el.type || null,
            name: el.name || null,
            id: el.id || null,
            href: el.href || null,
            text: ((el.innerText || el.value || '') + '').trim().slice(0, 120),
          })).filter(b => (b.text || b.href || '').length > 0).slice(0, 80);
          const hrefs = Array.from(document.querySelectorAll('a[href]'))
            .map(a => a.href)
            .filter(h => /100408|modif|edit|editer|annonce/i.test(h));
          return {
            url: location.href,
            title: document.title,
            body_len: (document.body && document.body.innerText || '').length,
            has_logout: /deconnexion|logout|se d.connecter/i.test(document.body.innerText || ''),
            inputs,
            buttons,
            editish_hrefs: hrefs.slice(0, 40),
          };
        }
        """
    )


async def _wait_manual_login(page) -> bool:
    print()
    print("=" * 64)
    print("LOGIN MANUEL 1Parrainage")
    print(f"Fenêtre ouverte : {LOGIN_URL}")
    print("Connecte-toi normalement dans le navigateur visible.")
    print("Pas de solveur, pas de bypass. Ensuite reste sur le site.")
    print(f"J'attends jusqu'à {LOGIN_WAIT_S}s que /login disparaisse.")
    print("=" * 64)
    print()
    deadline = asyncio.get_event_loop().time() + LOGIN_WAIT_S
    last = 0.0
    while asyncio.get_event_loop().time() < deadline:
        url = page.url or ""
        try:
            await _detect_challenge(page)
        except Exception as exc:
            print(f"STOP challenge: {exc}")
            return False
        logged = "/login" not in url.lower()
        if logged:
            try:
                body = ((await page.inner_text("body")) or "").lower()
                if any(x in body for x in ("deconnexion", "logout", "se déconnecter", "mon compte", "mes annonces")):
                    print(f"Login détecté: {url}")
                    return True
            except Exception:
                pass
            if "1parrainage.com" in url and "login" not in url.lower() and "inscription" not in url.lower():
                print(f"Login probable (plus sur /login): {url}")
                return True
        now = asyncio.get_event_loop().time()
        if now - last > 15:
            print(f"  encore sur {url} — en attente de login…")
            last = now
        await asyncio.sleep(2)
    print("TIMEOUT: login manuel non terminé")
    return False


async def _page_has_link_field(page) -> bool:
    values = await page.evaluate(
        """
        () => Array.from(document.querySelectorAll('textarea, input'))
          .map(el => el.value || '')
        """
    )
    blob = "\n".join(values or [])
    return OLD_LINK in blob or NEW_LINK in blob


async def _collect_hrefs(page) -> list[str]:
    return await page.evaluate(
        """
        () => Array.from(document.querySelectorAll('a[href], form[action], button[formaction]'))
          .map(el => el.href || el.action || el.getAttribute('formaction') || '')
          .filter(h => h.startsWith('http') && h.includes('1parrainage.com'))
        """
    )


async def _find_edit(page, report: dict) -> str | None:
    seeds = [
        f"{BASE}/espace_parrain/",
        f"{BASE}/espace_parrain/annonces/",
        f"{BASE}/espace_parrain/annonce/",
        f"{BASE}/espace_parrain/offres/",
        f"{BASE}/espace_parrain/mes-annonces/",
        f"{BASE}/espace_parrain/mes_annonces/",
        f"{BASE}/espace_parrain/annonce/edit/{OFFER_ID}/",
        f"{BASE}/espace_parrain/annonces/edit/{OFFER_ID}/",
        f"{BASE}/espace_parrain/offres/edit/{OFFER_ID}/",
        f"{BASE}/espace_parrain/offer/edit/{OFFER_ID}/",
        f"{BASE}/espace_parrain/coupon/edit/{OFFER_ID}/",
        f"{BASE}/espace_parrain/parrainage/edit/{OFFER_ID}/",
        f"{BASE}/espace_parrain/{OFFER_ID}/edit/",
        f"{BASE}/espace_parrain/edit/{OFFER_ID}/",
        f"{BASE}/espace_parrain/edit/annonce/{OFFER_ID}/",
        f"{BASE}/modifier_annonce.php?id={OFFER_ID}",
        f"{BASE}/edit_annonce.php?id={OFFER_ID}",
        f"{BASE}/editer_annonce.php?id={OFFER_ID}",
        PUBLIC_LIST,
    ]
    seen: list[str] = []
    crawled: list[str] = []
    for url in seeds:
        if url in seen:
            continue
        seen.append(url)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(0.8)
            if "/login" in (page.url or ""):
                continue
            crawled.append(page.url)
            if await _page_has_link_field(page):
                print(f"edit field found on {page.url}")
                report["discovery_crawled"] = crawled
                return page.url
            for h in await _collect_hrefs(page):
                low = h.lower()
                if any(x in low for x in ("boost", "remont", "supprim", "delete", "facebook", "twitter", "logout")):
                    continue
                interesting = any(
                    x in low
                    for x in (
                        OFFER_ID,
                        "modif",
                        "edit",
                        "editer",
                        "annonce",
                        "offre",
                        "kraken",
                        "espace_parrain",
                    )
                )
                if interesting and h not in seen and len(seen) < 40:
                    seen.append(h)
        except Exception as exc:
            print(f"  skip {url}: {exc}")
            continue

    # Second pass: remaining interesting hrefs discovered on member pages
    extra = [h for h in seen if h not in seeds]
    report["discovery_candidates"] = extra[:40]
    for url in extra:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(0.7)
            if "/login" in (page.url or ""):
                continue
            crawled.append(page.url)
            if await _page_has_link_field(page):
                print(f"edit field found on {page.url}")
                report["discovery_crawled"] = crawled
                return page.url
        except Exception as exc:
            print(f"  skip extra {url}: {exc}")
            continue
    report["discovery_crawled"] = crawled
    return None


async def _locate_link_field(page) -> dict | None:
    return await page.evaluate(
        """
        (oldLink, newLink) => {
          const els = Array.from(document.querySelectorAll('textarea, input'));
          for (let i = 0; i < els.length; i++) {
            const el = els[i];
            const v = el.value || '';
            if (v.includes(oldLink) || v.includes(newLink)) {
              return {
                index: i,
                tag: el.tagName.toLowerCase(),
                type: el.type || null,
                name: el.name || null,
                id: el.id || null,
                value: v.slice(0, 800),
                has_old: v.includes(oldLink),
                has_new: v.includes(newLink),
                value_is_exactly_old: v.trim() === oldLink,
                value_is_exactly_new: v.trim() === newLink,
              };
            }
          }
          return null;
        }
        """,
        OLD_LINK,
        NEW_LINK,
    )


async def _set_link_only(page, field: dict) -> None:
    # Replace only the OLD URL inside the same field. Never rewrite other text.
    ok = await page.evaluate(
        """
        (info, oldLink, newLink) => {
          const els = Array.from(document.querySelectorAll('textarea, input'));
          const el = els[info.index];
          if (!el) return false;
          const v = el.value || '';
          if (!v.includes(oldLink)) return false;
          if (v.includes(newLink)) return false;
          const next = v.split(oldLink).join(newLink);
          if (next === v) return false;
          el.focus();
          el.value = next;
          el.dispatchEvent(new Event('input', {bubbles: true}));
          el.dispatchEvent(new Event('change', {bubbles: true}));
          return el.value.includes(newLink) && !el.value.includes(oldLink);
        }
        """,
        field,
        OLD_LINK,
        NEW_LINK,
    )
    if not ok:
        raise RuntimeError("targeted replace failed — STOP no save")


async def _click_save(page) -> str:
    btn = page.locator(
        'button:has-text("Enregistrer"), button:has-text("Sauvegarder"), '
        'button:has-text("Mettre à jour"), button:has-text("Valider"), '
        'button:has-text("Modifier"), input[type="submit"], button[type="submit"]'
    )
    count = await btn.count()
    for i in range(count):
        b = btn.nth(i)
        label = ((await b.inner_text()) or (await b.get_attribute("value") or "")).lower()
        if any(x in label for x in ("boost", "remont", "supprim", "delete", "actualis")):
            continue
        await b.click()
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2.0)
        return label.strip() or "submit"
    raise RuntimeError("unexpected_dom: bouton Enregistrer introuvable (pas Boost/Remonter)")


async def _launch():
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = None
    for kwargs in (
        {"headless": False, "channel": "chrome", "args": ["--start-maximized"]},
        {"headless": False, "channel": "msedge", "args": ["--start-maximized"]},
        {"headless": False, "args": ["--start-maximized"]},
    ):
        try:
            browser = await pw.chromium.launch(**kwargs)
            print(f"browser launch ok: {kwargs}")
            break
        except Exception as exc:
            print(f"launch skip {kwargs}: {exc}")
    if browser is None:
        await pw.stop()
        raise RuntimeError("unable to launch headed Chromium/Chrome/Edge")
    ctx = await browser.new_context(locale="fr-FR", timezone_id="Europe/Paris")
    page = await ctx.new_page()
    return pw, browser, ctx, page


async def main_async(execute: bool) -> int:
    LOCAL.mkdir(parents=True, exist_ok=True)
    plan = build_write_plan("1parrainage", "kraken", "fr")
    report: dict = {
        "at": _now(),
        "headed": True,
        "stealth": False,
        "captcha_solver": False,
        "offer_id": OFFER_ID,
        "old_link": OLD_LINK,
        "new_link": NEW_LINK,
        "execute": execute,
        "plan_changed_fields": plan.changed_fields,
        "structure_preserved_plan": plan.structure_preserved,
    }
    forbidden = abort_forbidden_publish(NEW_LINK, CODE, REWARD)
    if forbidden:
        report["error"] = forbidden
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 2

    pw = browser = ctx = page = None
    try:
        pw, browser, ctx, page = await _launch()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.screenshot(path=str(LOCAL / "login-before.png"), full_page=True)
        except Exception:
            pass
        login_dom = await _dump_dom(page)
        report["login_page"] = {
            "url": login_dom.get("url"),
            "title": login_dom.get("title"),
            "visible_inputs": [
                {k: x.get(k) for k in ("tag", "type", "name", "id", "placeholder", "visible")}
                for x in (login_dom.get("inputs") or [])
            ],
        }
        authenticated = await _wait_manual_login(page)
        report["headed_login"] = "manual"
        report["authenticated"] = authenticated
        if not authenticated:
            report["error"] = "manual_login_timeout_or_challenge"
            DIAG.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 3

        try:
            await page.screenshot(path=str(LOCAL / "after-login.png"), full_page=True)
        except Exception:
            pass
        await page.goto(f"{BASE}/espace_parrain/", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.0)
        account_dom = await _dump_dom(page)
        nav = await _collect_hrefs(page)
        report["account_page"] = {
            "url": account_dom.get("url"),
            "title": account_dom.get("title"),
            "has_logout": account_dom.get("has_logout"),
            "editish_hrefs": account_dom.get("editish_hrefs"),
            "nav_hrefs": nav[:80],
        }
        print("espace_parrain nav:")
        for h in nav[:80]:
            print(f"  {h}")

        edit_url = await _find_edit(page, report)
        report["edit_url"] = edit_url
        if not edit_url:
            report["error"] = "edit_url_not_found"
            DIAG.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 4

        await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.2)
        await _detect_challenge(page)
        edit_dom = await _dump_dom(page)
        field = await _locate_link_field(page)
        report["edit_dom"] = {
            "url": edit_dom.get("url"),
            "title": edit_dom.get("title"),
            "field": field,
            "save_candidates": [
                b
                for b in (edit_dom.get("buttons") or [])
                if b.get("text")
                and any(
                    x in (b.get("text") or "").lower()
                    for x in ("enregistr", "sauvegard", "valider", "mettre à jour", "modifier")
                )
                and not any(x in (b.get("text") or "").lower() for x in ("boost", "remont", "supprim"))
            ],
        }
        DIAG.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"diag={DIAG}")
        print(f"edit_url={edit_url}")
        print(f"field={json.dumps(field, ensure_ascii=False)}")

        if not execute:
            print("diagnose only — no save")
            return 0

        if not field or not field.get("has_old"):
            report["error"] = "OLD_LINK_NOT_IN_EDIT_FIELD — STOP no save"
            report["old_link_verified"] = False
            OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(report["error"])
            return 5
        if field.get("has_new"):
            report["error"] = "NEW already present — STOP no save"
            report["old_link_verified"] = True
            OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(report["error"])
            return 5

        current = field.get("value") or ""
        if OLD_LINK not in current:
            report["error"] = "precondition OLD mismatch — STOP no save"
            OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 5
        if CODE not in current and not field.get("value_is_exactly_old"):
            # Dedicated URL input may not contain the code — check page hay.
            page_hay = await page.evaluate("() => document.body.innerText || ''")
            if CODE not in page_hay:
                print("note: code not in same field; present on page?", CODE in page_hay)
        report["old_link_verified"] = True
        snap = snapshot_state("canary:1parrainage:headed")
        report["snapshot_id"] = snap.get("id")

        print("=== TARGETED EDIT personal_link only ===")
        await _set_link_only(page, field)
        after = await _locate_link_field(page)
        report["after_edit_before_save"] = after
        if not after or not after.get("has_new") or after.get("has_old"):
            report["error"] = "replace did not yield NEW-only — STOP no save"
            OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 6
        if after.get("value") and after["value"].replace(NEW_LINK, OLD_LINK) != current:
            # Other text changed besides the URL swap
            if current.replace(OLD_LINK, NEW_LINK) != after.get("value"):
                report["error"] = "non-targeted text change — STOP no save"
                OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return 6
        report["targeted_field_only"] = True

        label = await _click_save(page)
        report["save_submitted"] = True
        report["save_label"] = label
        print(f"saved via {label!r}")

        await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.5)
        acc = await _locate_link_field(page)
        acc_hay = await page.evaluate(
            """
            () => {
              const vals = Array.from(document.querySelectorAll('textarea, input'))
                .map(el => el.value || '').join('\\n');
              return vals + '\\n' + (document.body.innerText || '');
            }
            """
        )
        report["account_reread"] = {
            "field": acc,
            "has_new": NEW_LINK in (acc_hay or ""),
            "has_old": OLD_LINK in (acc_hay or ""),
            "has_code": CODE in (acc_hay or ""),
            "has_reward": REWARD in (acc_hay or "") or "200 €" in (acc_hay or ""),
        }
        pub_html = fetch_text(PUBLIC_LIST)
        report["public_reread"] = {
            "url": PUBLIC_LIST,
            "has_new": NEW_LINK in (pub_html or ""),
            "has_old": OLD_LINK in (pub_html or ""),
            "has_code": CODE in (pub_html or ""),
            "has_reward": REWARD in (pub_html or "") or ("200" in (pub_html or "") and "crypto" in (pub_html or "").lower()),
        }
        acc_ok = bool(report["account_reread"]["has_new"]) and not report["account_reread"]["has_old"]
        pub_ok = bool(report["public_reread"]["has_new"]) and not report["public_reread"]["has_old"]
        code_ok = report["account_reread"]["has_code"] or report["public_reread"]["has_code"]
        reward_ok = report["account_reread"]["has_reward"] or report["public_reread"]["has_reward"]
        post_match = acc_ok and pub_ok and code_ok and reward_ok
        report["post_match"] = post_match
        report["immutable_preserved"] = code_ok and reward_ok and report.get("targeted_field_only") is True
        report["ok"] = post_match

        if post_match:
            from lib.write_status import mark_write_verified

            evidence = {
                "post_match": True,
                "announcement_url": PUBLIC_LIST + f"#id={OFFER_ID}",
                "edit_url": edit_url,
                "public_reread": True,
                "immutable_ok": True,
                "source": "local_headed_1parrainage",
                "checks": {
                    "authenticated": True,
                    "targeted_edit": True,
                    "submit_ok": True,
                    "reread_account": True,
                    "expected_values_present": True,
                    "immutable_preserved": True,
                },
            }
            promo = mark_write_verified("1parrainage", program="kraken", evidence=evidence)
            report["WRITE_VERIFIED"] = bool(promo.get("ok"))
            report["promotion"] = promo
            print("WRITE_VERIFIED 1parrainage", promo)
        else:
            report["WRITE_VERIFIED"] = False
            print("post_match failed — not WRITE_VERIFIED")

        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"report={OUT}")
        return 0 if post_match else 1
    except Exception as exc:
        report["error"] = str(exc)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"ERROR: {exc}")
        return 1
    finally:
        try:
            if page:
                await page.close()
            if ctx:
                await ctx.close()
            if browser:
                await browser.close()
            if pw:
                await pw.stop()
        except Exception:
            pass
        _wipe_local()
        print("local auth/state wiped")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--diagnose", action="store_true")
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    execute = bool(args.execute)
    if not args.diagnose and not args.execute:
        execute = False
        args.diagnose = True
    return asyncio.run(main_async(execute=execute))


if __name__ == "__main__":
    raise SystemExit(main())
