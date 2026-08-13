#!/usr/bin/env python3
"""ONE headed Chrome session: you log in, I learn RCTV listing EIDs. NO SAVE.

  python -u tools/local_headed_rctv.py

After /my-account, do not click Boost / Add / Submit. I only read edit forms.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOGIN = "https://www.referralcode.tv/login/"
ACCOUNT = "https://www.referralcode.tv/my-account/?tab=listings"
OUT = ROOT / "data" / "captures" / "rctv-headed-eid.json"
LOCAL = ROOT / ".local-auth"
LOGIN_WAIT_S = 900
WANTED = ("kraken", "paypal", "robinhood", "whatnot", "wise", "okx", "stake")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wipe() -> None:
    if LOCAL.exists():
        shutil.rmtree(LOCAL, ignore_errors=True)


def _classify(title: str, content: str, link: str) -> str | None:
    blob = f"{title} {content} {link}".lower()
    for prog in WANTED + (
        "gemini",
        "bybit",
        "swissborg",
        "airbnb",
        "joko",
        "ledger",
        "revolut",
        "widilo",
        "unibet",
        "vinted",
        "boursobank",
        "ebuyclub",
    ):
        if prog in blob:
            return prog
    if "transferwise" in blob:
        return "wise"
    return None


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
    ctx = await browser.new_context(locale="en-US", timezone_id="Europe/Paris")
    page = await ctx.new_page()
    return pw, browser, ctx, page


async def _wait_account(page) -> bool:
    print()
    print("=" * 64)
    print("LOGIN MANUEL — une seule fois, ne ferme pas Chrome")
    print(f"     {LOGIN}")
    print("     Ensuite ouvre My Account / My Referral Codes si besoin.")
    print("=" * 64)
    print()
    deadline = asyncio.get_event_loop().time() + LOGIN_WAIT_S
    last = 0.0
    while asyncio.get_event_loop().time() < deadline:
        url = (page.url or "").lower()
        if "my-account" in url and "login" not in url:
            print(f"my-account détecté: {page.url}")
            return True
        if "referralcode.tv" in url and "login" not in url and "sign" not in url:
            print(f"login OK ({page.url}) → listings même session")
            await page.goto(ACCOUNT, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1.0)
            if "my-account" in (page.url or "").lower():
                return True
        now = asyncio.get_event_loop().time()
        if now - last > 15:
            print(f"  en attente de login… url={page.url}")
            last = now
        await asyncio.sleep(2)
    print("TIMEOUT: /my-account non atteint")
    return False


async def _collect_eids(page) -> list[str]:
    for url in (
        ACCOUNT,
        "https://www.referralcode.tv/my-account/",
        "https://referralcode.tv/my-account/?tab=listings",
    ):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1.2)
        except Exception:
            continue
        hrefs = await page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href]'))
              .map(a => a.href || '')
              .filter(h => /eid=\\d+/.test(h) && h.includes('add-referral-code'))
            """
        )
        eids = []
        for h in hrefs or []:
            m = re.search(r"[?&]eid=(\d+)", h)
            if m and m.group(1) not in eids:
                eids.append(m.group(1))
        if eids:
            print(f"EIDs trouvés ({len(eids)}) sur {page.url}")
            return eids
    return []


async def _read_edit(page, eid: str) -> dict:
    url = f"https://referralcode.tv/add-referral-code/?eid={eid}"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(0.8)
    rec = await page.evaluate(
        """
        () => {
          const val = (sel) => {
            const el = document.querySelector(sel);
            return el ? (el.value || el.innerText || '').trim() : '';
          };
          return {
            url: location.href,
            title: val('input[name="form[post_title]"], #form_post_title, input.form_post_title'),
            code: val('input[name="custom[code]"], input.field-code'),
            link: val('input[name="custom[buy_link]"], input.field-buy_link'),
            content: val('textarea[name="form[post_content]"]'),
          };
        }
        """
    )
    rec["eid"] = eid
    rec["program"] = _classify(rec.get("title") or "", rec.get("content") or "", rec.get("link") or "")
    rec["save_clicked"] = False
    print(f"  eid={eid} program={rec.get('program')} title={(rec.get('title') or '')[:70]!r}")
    return rec


async def run() -> int:
    report = {
        "at": _now(),
        "READ_ONLY": True,
        "save_clicked": False,
        "listings": [],
        "wanted": {},
    }
    pw = browser = ctx = page = None
    try:
        pw, browser, ctx, page = await _launch()
        await page.goto(LOGIN, wait_until="domcontentloaded", timeout=60000)
        if not await _wait_account(page):
            report["error"] = "account_not_reached"
            _write(OUT, report)
            return 3
        eids = await _collect_eids(page)
        report["eid_count"] = len(eids)
        report["eids"] = eids
        if not eids:
            report["error"] = "no_eid_links — stay on My Referral Codes, no save"
            _write(OUT, report)
            print("STOP: aucun lien eid= — ouvre l'onglet My Referral Codes puis relance si besoin")
            return 4
        print("=== READ-ONLY — ouverture des formulaires d'édition, AUCUN SAVE ===")
        for eid in eids:
            rec = await _read_edit(page, eid)
            report["listings"].append(
                {
                    "eid": eid,
                    "program": rec.get("program"),
                    "title": rec.get("title"),
                    "code": rec.get("code"),
                    "link": rec.get("link"),
                    "content_head": (rec.get("content") or "")[:240],
                    "edit_url": rec.get("url"),
                }
            )
            prog = rec.get("program")
            if prog in WANTED and prog not in report["wanted"]:
                report["wanted"][prog] = {
                    "eid": eid,
                    "edit_url": rec.get("url"),
                    "title": rec.get("title"),
                    "code": rec.get("code"),
                    "link": rec.get("link"),
                }
        report["kraken_eid"] = (report["wanted"].get("kraken") or {}).get("eid")
        report["ok"] = True
        _write(OUT, report)
        print(f"kraken_eid={report['kraken_eid']}")
        print(f"wanted={list(report['wanted'])}")
        print(f"report={OUT}")
        return 0 if report.get("kraken_eid") else 1
    except Exception as exc:
        report["error"] = str(exc)
        _write(OUT, report)
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
        _wipe()
        print("local auth/state wiped")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
