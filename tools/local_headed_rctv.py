#!/usr/bin/env python3
"""Headed RCTV: View ads one by one. No batch edit. No save.

  python -u tools/local_headed_rctv.py

After login, click View on each ad. I only read. Never Boost / Add / Submit.
If the ad is already OK I say CONFORME — go to the next View.
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
AUTHOR = "https://www.referralcode.tv/author/thesuperreff/"
OUT = ROOT / "data" / "captures" / "rctv-headed-eid.json"
LOCAL = ROOT / ".local-auth"
LOGIN_WAIT_S = 900
WATCH_S = 900
OLD_LINK = "4jdp7sea"
NEW_LINK = "s5qudqe4"
CODE = "cpbrgddy"

_BRANDS = (
    ("traderepublic", ("trade republic", "traderepublic", "trade.re")),
    ("boursobank", ("boursobank", "boursorama", "bour.so")),
    ("swissborg", ("swissborg",)),
    ("robinhood", ("robinhood",)),
    ("ebuyclub", ("ebuyclub", "ebuy club")),
    ("igraal", ("igraal",)),
    ("poulpeo", ("poulpeo",)),
    ("whatnot", ("whatnot",)),
    ("widilo", ("widilo",)),
    ("revolut", ("revolut",)),
    ("vinted", ("vinted",)),
    ("paypal", ("paypal", "py.pl/")),
    ("kraken", ("kraken",)),
    ("gemini", ("gemini",)),
    ("ledger", ("ledger",)),
    ("unibet", ("unibet",)),
    ("airbnb", ("airbnb",)),
    ("bybit", ("bybit",)),
    ("stake", ("stake.com",)),
    ("wise", ("transferwise", "wise.com", "wise.")),
    ("okx", ("okx",)),
    ("joko", ("joko",)),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wipe() -> None:
    if LOCAL.exists():
        shutil.rmtree(LOCAL, ignore_errors=True)


def _classify(blob: str) -> str | None:
    low = (blob or "").lower()
    for prog, keys in _BRANDS:
        if any(k in low for k in keys):
            return prog
    return None


def _verdict(program: str | None, blob: str) -> str:
    if program != "kraken":
        return "VIEW_OK_SKIP"  # no operator lock on this program this cycle
    if NEW_LINK in blob and CODE in blob and OLD_LINK not in blob:
        return "CONFORME"
    if OLD_LINK in blob or (CODE in blob and NEW_LINK not in blob):
        return "DIFF_LINK"
    if "kraken" in blob.lower():
        return "KRAKEN_SEEN_CHECK_FIELDS"
    return "UNKNOWN"


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
    print("=" * 64)
    print()
    deadline = asyncio.get_event_loop().time() + LOGIN_WAIT_S
    last = 0.0
    while asyncio.get_event_loop().time() < deadline:
        url = (page.url or "").lower()
        if "my-account" in url and "login" not in url:
            print(f"my-account détecté: {page.url}")
            return True
        if "referralcode.tv" in url and "login" not in url:
            await page.goto(ACCOUNT, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1.0)
            if "my-account" in (page.url or "").lower():
                return True
        now = asyncio.get_event_loop().time()
        if now - last > 15:
            print(f"  en attente de login… url={page.url}")
            last = now
        await asyncio.sleep(2)
    return False


async def _cards(page) -> list[dict]:
    return await page.evaluate(
        """
        () => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a[href]')) {
            const href = a.href || '';
            const text = ((a.innerText || '') + ' ' + (a.getAttribute('title') || '')).replace(/\\s+/g, ' ').trim();
            const low = (href + ' ' + text).toLowerCase();
            if (low.includes('boost') || low.includes('cliccami') || low.includes('add-referral-code/?eid=') === false
                && !href.includes('/referral-code/') && !href.includes('/brand/')) continue;
            if (href.includes('add-referral-code/') && !href.includes('eid=')) continue;
            if (!href.startsWith('http')) continue;
            const key = href.split('#')[0];
            if (seen.has(key)) continue;
            seen.add(key);
            const eidM = href.match(/[?&]eid=(\\d+)/);
            const sidM = href.match(/[?&]__sid=(\\d+)/);
            out.push({
              href,
              text: text.slice(0, 140),
              eid: eidM ? eidM[1] : null,
              sid: sidM ? sidM[1] : null,
              kind: href.includes('eid=') ? 'edit' : (href.includes('/referral-code/') ? 'view' : 'other'),
            });
          }
          return out;
        }
        """
    )


async def _read_view(page) -> dict:
    url = page.url or ""
    title = ""
    try:
        title = await page.title()
    except Exception:
        pass
    body = ""
    try:
        body = await page.inner_text("body")
    except Exception:
        pass
    blob = f"{title}\n{body}"
    eid_m = re.search(r"[?&]eid=(\d+)", url)
    sid_m = re.search(r"[?&]__sid=(\d+)", url)
    program = _classify(blob + " " + url)
    verdict = _verdict(program, blob)
    recaptcha = False
    try:
        recaptcha = bool(
            await page.locator(
                "iframe[src*='recaptcha'], textarea[name='g-recaptcha-response'], .g-recaptcha"
            ).count()
        )
    except Exception:
        pass
    kind = "edit" if "add-referral-code" in url and "eid=" in url else "view"
    return {
        "url": url,
        "kind": kind,
        "title": title[:180],
        "program": program,
        "eid": eid_m.group(1) if eid_m else None,
        "sid": sid_m.group(1) if sid_m else None,
        "has_s5qudqe4": NEW_LINK in blob,
        "has_4jdp7sea": OLD_LINK in blob,
        "has_cpbrgddy": CODE in blob,
        "recaptcha": recaptcha,
        "verdict": verdict,
        "head": re.sub(r"\s+", " ", body)[:280],
    }


async def run() -> int:
    report = {
        "at": _now(),
        "mode": "view_one_by_one",
        "READ_ONLY": True,
        "save_clicked": False,
        "inventory": [],
        "reviewed": [],
    }
    pw = browser = ctx = page = None
    try:
        pw, browser, ctx, page = await _launch()
        await page.goto(LOGIN, wait_until="domcontentloaded", timeout=60000)
        if not await _wait_account(page):
            report["error"] = "account_not_reached"
            _write(OUT, report)
            return 3

        await page.goto(ACCOUNT, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2.5)
        inv = await _cards(page)
        if len(inv) < 5:
            await page.goto("https://www.referralcode.tv/my-account/?tab=listings", wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2.0)
            inv = await _cards(page)
        report["inventory"] = inv
        print(f"cartes dashboard: {len(inv)}")
        for c in inv:
            print(f"  [{c.get('kind')}] eid={c.get('eid')} sid={c.get('sid')} {(c.get('text') or '')[:70]!r}")

        print()
        print("=" * 64)
        print("VIEW ADS — une annonce à la fois")
        print("  Clique View (œil / titre / View) sur une carte.")
        print("  PAS Boost, PAS Add, PAS Submit.")
        print("  Si CONFORME → View sur l'annonce suivante.")
        print("  Priorité : trouve Kraken (⭐️ Kraken Referral Bonus).")
        print("=" * 64)
        print()

        seen: set[str] = set()
        deadline = asyncio.get_event_loop().time() + WATCH_S
        last_url = page.url
        while asyncio.get_event_loop().time() < deadline:
            url = page.url or ""
            interesting = (
                "/referral-code/" in url
                or ("eid=" in url and "add-referral-code" in url)
            )
            if interesting and url not in seen and url != last_url:
                await asyncio.sleep(0.8)
                rec = await _read_view(page)
                seen.add(url)
                report["reviewed"].append(rec)
                print(
                    f"{rec['kind'].upper()} program={rec['program']} verdict={rec['verdict']} "
                    f"eid={rec['eid']} sid={rec['sid']} recaptcha={rec['recaptcha']}"
                )
                print(f"  {rec['head'][:160]}")
                if rec.get("recaptcha"):
                    print("  CAPTCHA de validation vu — je ne le résous pas, je ne clique pas Submit.")
                if rec["program"] == "kraken":
                    report["kraken"] = rec
                    print(f"=== KRAKEN {rec['verdict']} sid={rec['sid']} eid={rec['eid']} ===")
                    if rec["verdict"] == "CONFORME":
                        print("Rien à changer. View l'annonce suivante.")
                    else:
                        print("DIFF vu — PAS de save (captcha). View suivante ou reste ici.")
                else:
                    print("  → pas Kraken : rien à changer maintenant. View l'annonce suivante.")
                _write(OUT, report)
            last_url = url
            await asyncio.sleep(1.0)

        report["kraken_eid"] = (report.get("kraken") or {}).get("eid") or (report.get("kraken") or {}).get("sid")
        report["ok"] = True
        _write(OUT, report)
        print(f"reviewed={len(report['reviewed'])} kraken={report.get('kraken')}")
        print(f"report={OUT}")
        return 0 if report.get("kraken") else 1
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
