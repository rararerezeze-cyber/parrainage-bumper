#!/usr/bin/env python3
"""ONE headed Chrome session: login → map offer 100408 → targeted link canary.

One browser, one context, one page. Never relaunch. Never persist cookies.
No stealth. No CAPTCHA solver. No anti-bot bypass. No automatic retry.

  python -u tools/local_headed_1parrainage.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.cookie_consent import ConsentBlocked, handle_cookie_consent  # noqa: E402
from lib.http_fetch import fetch_text  # noqa: E402
from lib.safety import abort_forbidden_publish, snapshot_state  # noqa: E402
from platforms.oneparrainage.writer import (  # noqa: E402
    BASE,
    LOGIN_URL,
    PUBLIC_LIST,
    _detect_challenge,
)

OFFER_ID = "100408"
OLD_LINK = "https://invite.kraken.com/JDNW/4jdp7sea"
NEW_LINK = "https://invite.kraken.com/JDNW/s5qudqe4"
CODE = "cpbrgddy"
REWARD = "200 € en cryptomonnaies"
LOCAL = ROOT / ".local-auth"
OUT = ROOT / "data" / "captures" / "1parrainage-headed-canary.json"
LOGIN_WAIT_S = 900


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wipe_local() -> None:
    if LOCAL.exists():
        shutil.rmtree(LOCAL, ignore_errors=True)


def _write(report: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _hay(page) -> str:
    return await page.evaluate(
        """
        () => {
          const vals = Array.from(document.querySelectorAll('textarea, input'))
            .map(el => el.value || '').join('\\n');
          return vals + '\\n' + (document.body ? document.body.innerText : '');
        }
        """
    )


async def _wait_espace_parrain(page) -> bool:
    print()
    print("=" * 64)
    print("UN SEUL LOGIN MANUEL — ne ferme pas cette fenêtre Chrome")
    print(f"Page : {LOGIN_URL}")
    print("Connecte-toi normalement. J'attends /espace_parrain/")
    print("puis je continue mapping + write dans CETTE session.")
    print("=" * 64)
    print()
    deadline = asyncio.get_event_loop().time() + LOGIN_WAIT_S
    last = 0.0
    while asyncio.get_event_loop().time() < deadline:
        url = (page.url or "").lower()
        try:
            await _detect_challenge(page)
        except Exception as exc:
            print(f"STOP challenge: {exc}")
            return False
        if "espace_parrain" in url and "login" not in url:
            print(f"espace_parrain détecté: {page.url}")
            return True
        if "login" not in url and "1parrainage.com" in url:
            # Logged in but not yet on member home — go there in the SAME page.
            print(f"login OK ({page.url}) → ouverture /espace_parrain/ même session")
            await page.goto(f"{BASE}/espace_parrain/", wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1.0)
            if "espace_parrain" in (page.url or "").lower() and "login" not in (page.url or "").lower():
                print(f"espace_parrain détecté: {page.url}")
                return True
        now = asyncio.get_event_loop().time()
        if now - last > 15:
            print(f"  en attente de login… url={page.url}")
            last = now
        await asyncio.sleep(2)
    print("TIMEOUT: /espace_parrain/ non atteint")
    return False


async def _collect_hrefs(page) -> list[str]:
    return await page.evaluate(
        """
        () => {
          const out = [];
          for (const el of document.querySelectorAll('a[href], form[action], button[formaction], [data-href], [data-url]')) {
            const h = el.href || el.action || el.getAttribute('formaction')
              || el.getAttribute('data-href') || el.getAttribute('data-url') || '';
            if (h && h.includes('1parrainage.com')) out.push(h);
          }
          return [...new Set(out)];
        }
        """
    )


async def _page_values(page) -> dict:
    hay = await _hay(page)
    return {
        "url": page.url,
        "has_offer_id": OFFER_ID in hay or OFFER_ID in (page.url or ""),
        "has_old": OLD_LINK in hay,
        "has_new": NEW_LINK in hay,
        "has_code": CODE in hay,
        "has_reward": REWARD in hay or ("200 €" in hay and "crypto" in hay.lower()),
        "has_kraken": "kraken" in hay.lower(),
    }


async def _locate_link_field(page) -> dict | None:
    loc = page.locator("textarea, input:not([type='password']):not([type='hidden'])")
    n = await loc.count()
    for i in range(n):
        el = loc.nth(i)
        try:
            v = await el.input_value()
        except Exception:
            continue
        if OLD_LINK not in v and NEW_LINK not in v:
            continue
        return {
            "index": i,
            "name": await el.get_attribute("name"),
            "id": await el.get_attribute("id"),
            "type": await el.get_attribute("type"),
            "value": v,
            "has_old": OLD_LINK in v,
            "has_new": NEW_LINK in v,
        }
    return None


async def _find_edit_same_session(page, report: dict) -> str | None:
    """Stay on this browser/page. Never new context."""
    guesses = [
        f"{BASE}/espace_parrain/annonce/edit/{OFFER_ID}/",
        f"{BASE}/espace_parrain/annonces/edit/{OFFER_ID}/",
        f"{BASE}/espace_parrain/annonce/{OFFER_ID}/edit/",
        f"{BASE}/espace_parrain/annonces/{OFFER_ID}/edit/",
        f"{BASE}/espace_parrain/offres/edit/{OFFER_ID}/",
        f"{BASE}/espace_parrain/offer/edit/{OFFER_ID}/",
        f"{BASE}/espace_parrain/{OFFER_ID}/edit/",
        f"{BASE}/espace_parrain/edit/{OFFER_ID}/",
        f"{BASE}/modifier_annonce.php?id={OFFER_ID}",
        f"{BASE}/espace_parrain/annonces/",
        f"{BASE}/espace_parrain/mes-annonces/",
        f"{BASE}/espace_parrain/",
        PUBLIC_LIST,
    ]
    seen: list[str] = []
    crawled: list[str] = []

    async def visit(url: str) -> str | None:
        if url in seen or len(seen) >= 50:
            return None
        seen.append(url)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(0.8)
        if "/login" in (page.url or ""):
            return None
        crawled.append(page.url)
        vals = await _page_values(page)
        field = await _locate_link_field(page)
        print(f"  crawl {page.url} old={vals['has_old']} field={bool(field)} offer={vals['has_offer_id']}")
        if field and (field.get("has_old") or field.get("has_new")):
            return page.url
        if vals["has_old"] or vals["has_new"]:
            # Link on page; maybe need to click Modifier on this same page.
            clicked = await page.evaluate(
                """
                (oid) => {
                  const nodes = Array.from(document.querySelectorAll('a, button'));
                  for (const n of nodes) {
                    const t = ((n.innerText || '') + ' ' + (n.href || '')).toLowerCase();
                    if (/boost|remont|supprim|delete/.test(t)) continue;
                    if ((t.includes('modif') || t.includes('edit')) && (t.includes(oid) || true)) {
                      if (t.includes(oid) || t.includes('modif') || t.includes('edit')) {
                        n.click();
                        return n.href || 'clicked';
                      }
                    }
                  }
                  return null;
                }
                """,
                OFFER_ID,
            )
            if clicked:
                await asyncio.sleep(1.2)
                field2 = await _locate_link_field(page)
                if field2:
                    return page.url
        for h in await _collect_hrefs(page):
            low = h.lower()
            if any(x in low for x in ("boost", "remont", "supprim", "delete", "facebook", "twitter", "logout", "profile/edit")):
                continue
            if any(x in low for x in (OFFER_ID, "modif", "edit", "editer", "annonce", "offre", "kraken")):
                if h not in seen:
                    seen.append(h)
        return None

    # Start from wherever we already are (espace_parrain).
    here = page.url
    if here and here not in guesses:
        guesses.insert(0, here)

    for url in guesses:
        try:
            found = await visit(url)
            if found:
                report["discovery_crawled"] = crawled
                return found
        except Exception as exc:
            print(f"  skip {url}: {exc}")

    extras = [h for h in seen if h not in guesses]
    report["discovery_candidates"] = extras[:50]
    for url in extras:
        try:
            found = await visit(url)
            if found:
                report["discovery_crawled"] = crawled
                return found
        except Exception as exc:
            print(f"  skip extra {url}: {exc}")

    report["discovery_crawled"] = crawled
    return None


async def _set_link_only(page, field: dict) -> None:
    loc = page.locator("textarea, input:not([type='password']):not([type='hidden'])").nth(
        int(field["index"])
    )
    v = await loc.input_value()
    if OLD_LINK not in v or NEW_LINK in v:
        raise RuntimeError("targeted replace failed — STOP no save")
    nxt = v.replace(OLD_LINK, NEW_LINK)
    if nxt == v:
        raise RuntimeError("targeted replace failed — STOP no save")
    await loc.fill(nxt)
    got = await loc.input_value()
    if NEW_LINK not in got or OLD_LINK in got:
        raise RuntimeError("targeted replace failed — STOP no save")


async def _click_save(page) -> str:
    btn = page.locator(
        'button:has-text("Enregistrer"), button:has-text("Sauvegarder"), '
        'button:has-text("Mettre à jour"), button:has-text("Valider"), '
        'input[type="submit"], button[type="submit"]'
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
        raise RuntimeError("unable to launch headed Chrome")
    ctx = await browser.new_context(
        locale="fr-FR",
        timezone_id="Europe/Paris",
        viewport={"width": 1400, "height": 900},
    )
    page = await ctx.new_page()
    return pw, browser, ctx, page


async def run() -> int:
    LOCAL.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "at": _now(),
        "single_session": True,
        "offer_id": OFFER_ID,
        "old_link": OLD_LINK,
        "new_link": NEW_LINK,
        "WRITE_VERIFIED": False,
        "save_submitted": False,
    }
    forbidden = abort_forbidden_publish(NEW_LINK)
    if forbidden:
        report["error"] = forbidden
        _write(report)
        return 2

    pw = browser = ctx = page = None
    try:
        pw, browser, ctx, page = await _launch()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            consent = await handle_cookie_consent(page)
        except ConsentBlocked as exc:
            report["cookie_consent_handled"] = "NO"
            report["error"] = str(exc)
            report["login_form_visible"] = False
            _write(report)
            print(str(exc))
            return 7
        report["cookie_consent_handled"] = consent.get("cookie_consent_handled")
        report["login_form_visible"] = bool(consent.get("login_form_visible"))
        report["consent"] = consent
        print(f"cookie_consent_handled={report['cookie_consent_handled']}")
        print(f"login_form_visible={report['login_form_visible']}")
        if not report["login_form_visible"]:
            report["error"] = "CONSENT_BLOCKED: #_username not visible after consent"
            _write(report)
            return 7
        ok = await _wait_espace_parrain(page)
        report["authenticated"] = ok
        report["authenticated_url"] = page.url if ok else None
        if not ok:
            report["error"] = "espace_parrain_not_reached"
            _write(report)
            return 3

        # Stay in this context. Find offer 100408 edit in the same session.
        edit_url = await _find_edit_same_session(page, report)
        report["offer_100408_found"] = bool(edit_url)
        report["edit_url"] = edit_url
        if not edit_url:
            report["error"] = "edit_url_not_found"
            _write(report)
            print("STOP: URL d'édition 100408 introuvable — aucun save")
            return 4

        if page.url != edit_url:
            await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1.0)
        await _detect_challenge(page)

        field = await _locate_link_field(page)
        hay = await _hay(page)
        report["old_link_verified"] = bool(field and field.get("has_old") and OLD_LINK in (field.get("value") or ""))
        report["preconditions"] = {
            "personal_code": CODE in hay,
            "personal_link_old": OLD_LINK in hay,
            "personal_link_new_already": NEW_LINK in hay,
            "referee_reward": REWARD in hay or ("200 €" in hay and "crypto" in hay.lower()),
        }
        pre = report["preconditions"]
        if not (pre["personal_code"] and pre["personal_link_old"] and pre["referee_reward"]):
            report["error"] = "precondition_mismatch — STOP no save"
            _write(report)
            print("STOP: précondition différente — aucun save")
            print(json.dumps(pre, ensure_ascii=False, indent=2))
            return 5
        if pre["personal_link_new_already"]:
            report["error"] = "NEW already present — STOP no save"
            _write(report)
            return 5
        if not field or not field.get("has_old"):
            report["error"] = "OLD not in editable field — STOP no save"
            _write(report)
            return 5

        current = field.get("value") or ""
        snap = snapshot_state("canary:1parrainage:headed")
        report["snapshot"] = snap.get("id")

        print("=== TARGETED EDIT personal_link only (same session) ===")
        await _set_link_only(page, field)
        after = await _locate_link_field(page)
        if not after or not after.get("has_new") or after.get("has_old"):
            report["error"] = "replace failed — STOP no save"
            _write(report)
            return 6
        if (after.get("value") or "") != current.replace(OLD_LINK, NEW_LINK):
            report["error"] = "non-targeted text change — STOP no save"
            _write(report)
            return 6
        report["targeted_field_only"] = True

        label = await _click_save(page)
        report["save_submitted"] = True
        report["save_label"] = label
        print(f"saved via {label!r}")

        await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.5)
        acc_hay = await _hay(page)
        report["account_reread"] = True
        report["new_link_verified_account"] = NEW_LINK in acc_hay and OLD_LINK not in acc_hay
        report["code_unchanged_account"] = CODE in acc_hay
        report["reward_unchanged_account"] = REWARD in acc_hay or ("200 €" in acc_hay)

        pub_html = fetch_text(PUBLIC_LIST)
        report["public_reread"] = True
        report["new_link_verified_public"] = NEW_LINK in (pub_html or "")
        report["old_absent_public"] = OLD_LINK not in (pub_html or "")
        report["code_unchanged_public"] = CODE in (pub_html or "")
        report["reward_unchanged_public"] = REWARD in (pub_html or "") or (
            "200" in (pub_html or "") and "crypto" in (pub_html or "").lower()
        )

        report["new_link_verified"] = bool(
            report["new_link_verified_account"] and report["new_link_verified_public"]
        )
        report["code_unchanged"] = bool(
            report["code_unchanged_account"] and report["code_unchanged_public"]
        )
        report["reward_unchanged"] = bool(
            report["reward_unchanged_account"] and report["reward_unchanged_public"]
        )
        report["immutable_preserved"] = bool(
            report.get("targeted_field_only") and report["code_unchanged"] and report["reward_unchanged"]
        )
        report["post_match"] = bool(
            report["new_link_verified"]
            and report["old_absent_public"]
            and report["immutable_preserved"]
        )

        if report["post_match"]:
            from lib.write_status import mark_write_verified

            promo = mark_write_verified(
                "1parrainage",
                program="kraken",
                evidence={
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
                },
            )
            report["WRITE_VERIFIED"] = bool(promo.get("ok"))
            report["promotion"] = promo
            print("WRITE_VERIFIED 1parrainage")
        else:
            report["WRITE_VERIFIED"] = False
            print("post_match incomplete — not WRITE_VERIFIED")

        _write(report)
        print(f"report={OUT}")
        return 0 if report.get("WRITE_VERIFIED") else 1
    except Exception as exc:
        report["error"] = str(exc)
        _write(report)
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
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
