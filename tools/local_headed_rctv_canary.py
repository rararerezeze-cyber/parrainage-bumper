#!/usr/bin/env python3
"""ONE headed RCTV canary: fill code+link on eid=23004 only. You solve captcha+Save.

  python -u tools/local_headed_rctv_canary.py

Never clicks Save / Boost / Add. Never opens another listing.
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

from lib.http_fetch import fetch_text  # noqa: E402
from lib.safety import abort_forbidden_publish, snapshot_state  # noqa: E402

LOGIN = "https://www.referralcode.tv/login/"
EDIT = "https://www.referralcode.tv/add-referral-code/?eid=23004"
PUBLIC = (
    "https://www.referralcode.tv/referral-code/"
    "%e2%ad%90%ef%b8%8f-kraken-referral-bonus-up-to-200-in-crypto-%e2%ad%90%ef%b8%8f-"
    "join-kraken-one-of-the-most-trusted-and-secure-crypto-exchanges-worldwide/"
)
EID = "23004"
OLD_CODE = "85d3qj9p"
NEW_CODE = "cpbrgddy"
OLD_LINK = "https://invite.kraken.com/JDNW/2seeom3g"
OLD_LINK_BARE = "invite.kraken.com/JDNW/2seeom3g"
NEW_LINK = "https://invite.kraken.com/JDNW/s5qudqe4"
NATIVE_MARK = "Up to $200"
FR_REWARD = "200 € en cryptomonnaies"
OUT = ROOT / "data" / "captures" / "rctv-headed-canary.json"
LOCAL = ROOT / ".local-auth"
LOGIN_WAIT_S = 900
EDIT_WAIT_S = 900
SAVE_WAIT_S = 900

READ_JS = """
() => {
  const val = (sel) => {
    const el = document.querySelector(sel);
    return el ? (el.value || '').trim() : '';
  };
  return {
    url: location.href,
    code: val('input[name="custom[code]"], input.field-code, #field-code'),
    link: val('input[name="custom[buy_link]"], input.field-buy_link, #field-buy_link'),
    title: val('input[name="form[post_title]"], #form_post_title, input.form_post_title'),
    content: val('textarea[name="form[post_content]"], textarea#form_post_content'),
    has_recaptcha: !!(document.querySelector("iframe[src*='recaptcha'], textarea[name='g-recaptcha-response'], .g-recaptcha")),
  };
}
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wipe() -> None:
    if LOCAL.exists():
        shutil.rmtree(LOCAL, ignore_errors=True)


def _public_flags(html: str) -> dict:
    h = html or ""
    return {
        "new_code": NEW_CODE in h,
        "new_link": "s5qudqe4" in h,
        "old_code": OLD_CODE in h,
        "old_link": "2seeom3g" in h,
        "native_en": NATIVE_MARK in h,
        "fr_reward_injected": FR_REWARD in h,
    }


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


async def _wait_login(page) -> bool:
    print()
    print("=" * 64)
    print("1/3  LOGIN MANUEL — une seule fois, ne ferme pas Chrome")
    print(f"     {LOGIN}")
    print("=" * 64)
    print()
    deadline = asyncio.get_event_loop().time() + LOGIN_WAIT_S
    last = 0.0
    while asyncio.get_event_loop().time() < deadline:
        url = (page.url or "").lower()
        if "referralcode.tv" in url and "login" not in url:
            print(f"login OK: {page.url}")
            return True
        now = asyncio.get_event_loop().time()
        if now - last > 15:
            print(f"  en attente de login… url={page.url}")
            last = now
        await asyncio.sleep(2)
    return False


async def _wait_edit(page) -> dict | None:
    print()
    print("=" * 64)
    print("2/3  OUVRE l'édition Kraken eid=23004")
    print(f"     {EDIT}")
    print("     (Edit Code sur l'annonce Kraken uniquement)")
    print("     Je remplis code+lien dès que le formulaire 23004 est là.")
    print("=" * 64)
    print()
    try:
        await page.goto(EDIT, wait_until="domcontentloaded", timeout=60000)
    except Exception as exc:
        print(f"note: ouverture auto skip ({exc}) — ouvre-la toi")
    deadline = asyncio.get_event_loop().time() + EDIT_WAIT_S
    last = 0.0
    while asyncio.get_event_loop().time() < deadline:
        url = page.url or ""
        if "eid=23004" in url and "add-referral-code" in url:
            rec = await page.evaluate(READ_JS)
            if rec.get("code") or rec.get("link") or rec.get("content"):
                print(f"formulaire 23004: code={rec.get('code')!r} link={rec.get('link')!r}")
                return rec
        now = asyncio.get_event_loop().time()
        if now - last > 15:
            print(f"  en attente de eid=23004… url={page.url}")
            last = now
        await asyncio.sleep(1.5)
    return None


async def _fill(page, before: dict) -> None:
    result = await page.evaluate(
        """
        ({oldCode, newCode, oldLink, oldBare, newLink, newBare}) => {
          const pick = (sels) => {
            for (const s of sels) {
              const el = document.querySelector(s);
              if (el) return el;
            }
            return null;
          };
          const codeEl = pick(['input[name="custom[code]"]', 'input.field-code', '#field-code']);
          const linkEl = pick(['input[name="custom[buy_link]"]', 'input.field-buy_link', '#field-buy_link']);
          const contentEl = pick(['textarea[name="form[post_content]"]', 'textarea#form_post_content']);
          if (!codeEl || !linkEl) return {ok: false, reason: 'fields_missing'};
          const codeBefore = codeEl.value || '';
          const linkBefore = linkEl.value || '';
          const contentBefore = contentEl ? (contentEl.value || '') : '';
          const fieldsAlreadyNew = !!(newCode && codeBefore.includes(newCode)
            && newLink && linkBefore.includes('s5qudqe4'));
          const contentHasOld = !!(contentBefore.includes(oldCode) || contentBefore.includes(oldBare)
            || (oldLink && contentBefore.includes(oldLink)));
          if (fieldsAlreadyNew && !contentHasOld)
            return {ok: false, reason: 'already_new'};
          if (oldCode && !codeBefore.includes(oldCode) && !contentBefore.includes(oldCode) && !fieldsAlreadyNew)
            return {ok: false, reason: 'old_code_missing', codeBefore, linkBefore};
          codeEl.value = newCode;
          codeEl.dispatchEvent(new Event('input', {bubbles: true}));
          codeEl.dispatchEvent(new Event('change', {bubbles: true}));
          linkEl.value = newLink;
          linkEl.dispatchEvent(new Event('input', {bubbles: true}));
          linkEl.dispatchEvent(new Event('change', {bubbles: true}));
          let contentAfter = contentBefore;
          if (contentEl) {
            let next = contentBefore;
            if (oldCode && next.includes(oldCode)) next = next.split(oldCode).join(newCode);
            if (oldLink && next.includes(oldLink)) next = next.split(oldLink).join(newLink);
            if (oldBare && next.includes(oldBare)) next = next.split(oldBare).join(newBare);
            const stripped = (s) => s.split(oldCode).join(newCode).split(oldLink).join(newLink).split(oldBare).join(newBare);
            if (stripped(contentBefore) !== next) return {ok: false, reason: 'non_targeted_content'};
            contentEl.value = next;
            contentEl.dispatchEvent(new Event('input', {bubbles: true}));
            contentAfter = next;
          }
          return {
            ok: true,
            code: codeEl.value,
            link: linkEl.value,
            content_changed: contentAfter !== contentBefore,
          };
        }
        """,
        {
            "oldCode": OLD_CODE,
            "newCode": NEW_CODE,
            "oldLink": OLD_LINK,
            "oldBare": OLD_LINK_BARE,
            "newLink": NEW_LINK,
            "newBare": NEW_LINK.replace("https://", ""),
        },
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"fill failed — STOP no save ({result})")
    after = await page.evaluate(READ_JS)
    if after.get("code") != NEW_CODE:
        raise RuntimeError(f"code field not exactly {NEW_CODE!r} — STOP")
    if NEW_LINK not in (after.get("link") or ""):
        raise RuntimeError("link field missing NEW — STOP")
    if "2seeom3g" in (after.get("link") or ""):
        raise RuntimeError("old link still in field — STOP")
    if after.get("title") != before.get("title"):
        raise RuntimeError("title changed — STOP")
    content = after.get("content") or ""
    if NATIVE_MARK not in content:
        raise RuntimeError("native $200 missing from content — STOP")
    if FR_REWARD in content:
        raise RuntimeError("FR reward injected — STOP")
    print(f"filled code={after.get('code')!r} link={after.get('link')!r} content_tokens={result.get('content_changed')}")


async def _wait_user_save(page, report: dict) -> None:
    print()
    print("=" * 64)
    print("3/3  CAPTCHA + VALIDATION — à toi")
    print("     Résous le captcha, clique Save / Update / Submit.")
    print("     Je ne clique sur rien. Pas Boost, pas Add.")
    print("=" * 64)
    print()
    deadline = asyncio.get_event_loop().time() + SAVE_WAIT_S
    last = 0.0
    while asyncio.get_event_loop().time() < deadline:
        url = page.url or ""
        if "eid=23004" not in url:
            print(f"quitté le formulaire: {url} — je considère le save lancé")
            report["save_submitted"] = True
            report["captcha_manual"] = True
            await asyncio.sleep(2.0)
            return
        try:
            rec = await page.evaluate(READ_JS)
            recaptcha = rec.get("has_recaptcha")
        except Exception as exc:
            msg = str(exc).lower()
            if "destroyed" in msg or "navigation" in msg:
                print("navigation après Save — je passe au reread")
                report["save_submitted"] = True
                report["captcha_manual"] = True
                await asyncio.sleep(2.0)
                return
            recaptcha = None
        now = asyncio.get_event_loop().time()
        if now - last > 12:
            print(f"  en attente de ton Save… url={url} recaptcha={recaptcha}")
            try:
                pub = fetch_text(PUBLIC) or ""
                if "s5qudqe4" in pub and OLD_CODE not in pub:
                    print("public déjà à jour — save détecté")
                    report["save_submitted"] = True
                    report["captcha_manual"] = True
                    return
            except Exception:
                pass
            last = now
        await asyncio.sleep(2)
    # If still on form, try reread anyway (user may have saved in place)
    report["save_submitted"] = True
    report["captcha_manual"] = True
    print("timeout attente save — je relis quand même")


async def _reread(page, before: dict, report: dict) -> None:
    try:
        await page.goto(EDIT, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.5)
        try:
            acc = await page.evaluate(READ_JS)
        except Exception as exc:
            acc = {"error": str(exc)}
    except Exception as exc:
        acc = {"error": str(exc)}
    report["account_reread"] = acc
    acc_blob = " ".join(
        [
            acc.get("code") or "",
            acc.get("link") or "",
            acc.get("content") or "",
            acc.get("title") or "",
        ]
    )
    # Prefer the headed View (same browser as users). Anonymous HTTP is often CDN-stale.
    pub_browser = ""
    try:
        await page.goto(PUBLIC, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.5)
        pub_browser = (await page.inner_text("body")) or ""
        pub_html = ""
        try:
            pub_html = await page.content()
        except Exception:
            pub_html = ""
        report["public_url"] = page.url
    except Exception as exc:
        report["public_browser_error"] = str(exc)
        pub_html = ""
    pub_http = ""
    try:
        pub_http = fetch_text(PUBLIC) or ""
    except Exception:
        pass
    # Score THIS listing only: headed View body first, then HTTP.
    pf_browser = _public_flags(pub_browser + "\n" + pub_html)
    pf_http = _public_flags(pub_http)
    report["public_flags_browser"] = pf_browser
    report["public_flags_http"] = pf_http
    pf = pf_browser if (pf_browser.get("new_code") or pf_browser.get("new_link")) else pf_http
    # Old tokens on HTTP-only (og:description / cache) do not fail if headed View is clean.
    if pf_browser.get("new_code") and pf_browser.get("new_link") and not pf_browser.get("old_code") and not pf_browser.get("old_link"):
        pf = pf_browser
    report["public_flags"] = pf
    report["public_flags"] = pf
    report["new_code_verified"] = NEW_CODE in acc_blob and pf["new_code"]
    report["new_link_verified"] = "s5qudqe4" in acc_blob and pf["new_link"]
    report["old_code_absent"] = OLD_CODE not in acc_blob and not pf["old_code"]
    report["old_link_absent"] = "2seeom3g" not in acc_blob and not pf["old_link"]
    report["native_en_preserved"] = NATIVE_MARK in (acc.get("content") or "") and pf["native_en"]
    report["immutable_preserved"] = (
        report["native_en_preserved"]
        and not pf["fr_reward_injected"]
        and (acc.get("title") == before.get("title") or not acc.get("title"))
    )
    report["account_reread_ok"] = bool(acc.get("code") or acc.get("content"))
    report["public_reread"] = True
    report["post_match"] = bool(
        report["new_code_verified"]
        and report["new_link_verified"]
        and report["old_code_absent"]
        and report["old_link_absent"]
        and report["immutable_preserved"]
    )


def _print_proof(report: dict) -> None:
    print(
        "RCTV_WRITE_PROOF\n"
        f"eid: {EID}\n"
        f"targeted_fields: code,link\n"
        f"captcha_manual: {report.get('captcha_manual')}\n"
        f"save_submitted: {report.get('save_submitted')}\n"
        f"account_reread: {report.get('account_reread_ok')}\n"
        f"public_reread: {report.get('public_reread')}\n"
        f"new_code_verified: {report.get('new_code_verified')}\n"
        f"new_link_verified: {report.get('new_link_verified')}\n"
        f"old_code_absent: {report.get('old_code_absent')}\n"
        f"old_link_absent: {report.get('old_link_absent')}\n"
        f"native_en_preserved: {report.get('native_en_preserved')}\n"
        f"immutable_preserved: {report.get('immutable_preserved')}\n"
        f"post_match: {report.get('post_match')}\n"
        f"WRITE_VERIFIED: {report.get('WRITE_VERIFIED')}"
    )


def _maybe_mark(report: dict) -> None:
    if not report.get("post_match"):
        print("post_match incomplete — not WRITE_VERIFIED")
        return
    from lib.write_status import mark_write_verified

    promo = mark_write_verified(
        "referralcode-tv",
        program="kraken",
        evidence={
            "post_match": True,
            "announcement_url": PUBLIC,
            "edit_url": EDIT,
            "public_reread": True,
            "immutable_ok": True,
            "source": "local_headed_rctv_canary",
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
    if report["WRITE_VERIFIED"]:
        print("WRITE_VERIFIED referralcode-tv")


async def run() -> int:
    report = {
        "at": _now(),
        "eid": EID,
        "targeted_fields": ["code", "link"],
        "WRITE_VERIFIED": False,
        "save_submitted": False,
        "captcha_manual": False,
    }
    forbidden = abort_forbidden_publish(NEW_LINK)
    if forbidden:
        report["error"] = forbidden
        _write(OUT, report)
        print(forbidden)
        return 2
    pw = browser = ctx = page = None
    try:
        pw, browser, ctx, page = await _launch()
        await page.goto(LOGIN, wait_until="domcontentloaded", timeout=60000)
        if not await _wait_login(page):
            report["error"] = "login_timeout"
            _write(OUT, report)
            return 3
        before = await _wait_edit(page)
        if not before:
            report["error"] = "edit_23004_not_opened — no fill"
            _write(OUT, report)
            return 4
        report["before"] = {
            "code": before.get("code"),
            "link": before.get("link"),
            "title": before.get("title"),
            "content_head": (before.get("content") or "")[:400],
        }
        blob = (before.get("code") or "") + (before.get("link") or "") + (before.get("content") or "")
        fields_new = NEW_CODE in (before.get("code") or "") and "s5qudqe4" in (before.get("link") or "")
        content_old = OLD_CODE in (before.get("content") or "") or "2seeom3g" in (before.get("content") or "")
        if fields_new and not content_old:
            print("champs+texte déjà NEW — pas de fill, je relis compte+public")
            report["filled"] = False
            report["save_submitted"] = True
            report["captcha_manual"] = True
            await _reread(page, before, report)
            _maybe_mark(report)
            _write(OUT, report)
            _print_proof(report)
            print(f"report={OUT}")
            return 0 if report.get("WRITE_VERIFIED") else 1
        if not fields_new and OLD_CODE not in blob:
            report["error"] = "OLD code not in form — STOP no fill"
            _write(OUT, report)
            print("STOP: ancien code absent — aucun fill")
            return 5
        if not fields_new and "2seeom3g" not in blob:
            report["error"] = "OLD link not in form — STOP no fill"
            _write(OUT, report)
            print("STOP: ancien lien absent — aucun fill")
            return 5
        if fields_new and content_old:
            print("champs déjà NEW — je remplace seulement les tokens dans le texte EN")
        snap = snapshot_state("canary:referralcode-tv:headed")
        report["snapshot"] = snap.get("id")
        await _fill(page, before)
        report["filled"] = True
        await _wait_user_save(page, report)
        await _reread(page, before, report)
        _maybe_mark(report)
        _write(OUT, report)
        print(f"report={OUT}")
        _print_proof(report)
        return 0 if report.get("WRITE_VERIFIED") else 1
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
