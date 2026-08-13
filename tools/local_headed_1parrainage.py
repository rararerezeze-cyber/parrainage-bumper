#!/usr/bin/env python3
"""ONE headed Chrome session: login → you click Modifier Kraken → learn form → canary.

Does NOT guess edit URLs. After /espace_parrain/, waits for you to open the
real Kraken edit form. Discovery never saves. Canary runs only if OLD is exact.

  python -u tools/local_headed_1parrainage.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.cookie_consent import ConsentBlocked, handle_cookie_consent  # noqa: E402
from lib.http_fetch import fetch_text  # noqa: E402
from lib.paths import mapping_path  # noqa: E402
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
DISCOVERY = ROOT / "data" / "captures" / "1parrainage-edit-discovery.json"
LOGIN_WAIT_S = 900
EDIT_WAIT_S = 900
EDIT_FIELDS = "textarea, input:not([type='password']):not([type='hidden']):not([type='checkbox']):not([type='radio']):not([type='submit'])"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plain(s: str) -> str:
    return unescape(s or "").replace("\xa0", " ")


def _has_reward(text: str) -> bool:
    p = _plain(text)
    return REWARD in p or "200 €" in p or "200 &euro;" in (text or "")


def _wipe_local() -> None:
    if LOCAL.exists():
        shutil.rmtree(LOCAL, ignore_errors=True)


def _write(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _attach_status(page, status_map: dict[str, int]) -> None:
    def _on_response(resp) -> None:
        try:
            if resp.request.resource_type == "document":
                status_map[resp.url] = resp.status
        except Exception:
            pass

    page.on("response", _on_response)


async def _wait_espace_parrain(page) -> bool:
    print()
    print("=" * 64)
    print("1/2  LOGIN MANUEL — une seule fois, ne ferme pas Chrome")
    print(f"     {LOGIN_URL}")
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
            print(f"login OK ({page.url}) → /espace_parrain/ même session")
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


async def _editable_fields(page) -> list[dict]:
    loc = page.locator(EDIT_FIELDS)
    n = await loc.count()
    out: list[dict] = []
    for i in range(n):
        el = loc.nth(i)
        try:
            v = await el.input_value()
        except Exception:
            continue
        if not (v or "").strip():
            continue
        out.append(
            {
                "index": i,
                "name": await el.get_attribute("name"),
                "id": await el.get_attribute("id"),
                "type": await el.get_attribute("type"),
                "value": v,
                "has_old": OLD_LINK in v,
                "has_new": NEW_LINK in v,
                "has_code": CODE in v,
                "has_kraken": "kraken" in v.lower() or "invite.kraken.com" in v.lower(),
            }
        )
    return out


def _is_real_edit_form(fields: list[dict]) -> dict | None:
    """True edit page = editable field with Kraken content. 100408 in HTML is not enough."""
    for f in fields:
        if f.get("has_old") or f.get("has_new"):
            return f
        if f.get("has_code") and f.get("has_kraken"):
            return f
    return None


async def _inspect_page(page, *, requested_url: str, status_map: dict[str, int]) -> dict:
    final = page.url or ""
    title = ""
    try:
        title = await page.title()
    except Exception:
        pass
    forms: list[dict] = []
    floc = page.locator("form")
    for i in range(await floc.count()):
        fr = floc.nth(i)
        forms.append(
            {
                "action": await fr.get_attribute("action"),
                "method": await fr.get_attribute("method"),
                "id": await fr.get_attribute("id"),
            }
        )
    links = page.locator("a[href]")
    near_id: list[str] = []
    near_kraken: list[str] = []
    for i in range(min(await links.count(), 200)):
        a = links.nth(i)
        href = (await a.get_attribute("href")) or ""
        text = ((await a.inner_text()) or "").strip().replace("\n", " ")[:120]
        blob = f"{href} {text}".lower()
        if "boost" in blob or "remont" in blob:
            continue
        if OFFER_ID in href or OFFER_ID in text:
            near_id.append(f"{text} -> {href}")
        if "kraken" in blob:
            near_kraken.append(f"{text} -> {href}")
    rec = {
        "requested_url": requested_url,
        "final_url": final,
        "title": title,
        "http_status": status_map.get(final),
        "forms": forms,
        "links_near_100408": near_id[:20],
        "links_near_kraken": near_kraken[:20],
    }
    print(
        f"requested_url={rec['requested_url']}\n"
        f"final_url={rec['final_url']}\n"
        f"title={rec['title']}\n"
        f"http_status={rec['http_status']}\n"
        f"forms={len(forms)}\n"
        f"links_near_100408={near_id[:5]}\n"
        f"links_near_kraken={near_kraken[:5]}"
    )
    return rec


def _persist_edit_url(edit_url: str) -> None:
    path = mapping_path("1parrainage", "kraken", "fr")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["edit_url"] = edit_url
    data["edit_url_source"] = "headed_manual_discovery"
    data["edit_url_learned_at"] = _now()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _wait_manual_edit_form(page, status_map: dict[str, int], report: dict) -> dict | None:
    print()
    print("=" * 64)
    print("2/2  DÉCOUVERTE — AUCUN SAVE")
    print("     Navigue manuellement jusqu'à Modifier l'annonce Kraken")
    print("     puis ne touche plus à rien.")
    print("     J'apprends l'URL et les champs dès qu'un vrai formulaire")
    print("     d'édition Kraken est ouvert (pas juste le texte 100408).")
    print("=" * 64)
    print()
    logs: list[dict] = []
    last_url = ""
    deadline = asyncio.get_event_loop().time() + EDIT_WAIT_S
    last_ping = 0.0
    while asyncio.get_event_loop().time() < deadline:
        try:
            await _detect_challenge(page)
        except Exception as exc:
            print(f"STOP challenge: {exc}")
            return None
        url = page.url or ""
        if url != last_url:
            rec = await _inspect_page(page, requested_url=url, status_map=status_map)
            logs.append(rec)
            last_url = url
        fields = await _editable_fields(page)
        hit = _is_real_edit_form(fields)
        if hit:
            learned = {
                "actual_edit_url": url,
                "field_selectors": {
                    "index": hit.get("index"),
                    "name": hit.get("name"),
                    "id": hit.get("id"),
                    "type": hit.get("type"),
                },
                "current_code": CODE if any(f.get("has_code") for f in fields) or CODE in (hit.get("value") or "") else None,
                "current_link": OLD_LINK if hit.get("has_old") else (NEW_LINK if hit.get("has_new") else None),
                "current_content": (hit.get("value") or "")[:2000],
                "page_log": logs,
            }
            if CODE in (hit.get("value") or ""):
                learned["current_code"] = CODE
            print(f"FORMULAIRE D'ÉDITION appris: {url}")
            print(f"field name={hit.get('name')} id={hit.get('id')}")
            _write(DISCOVERY, learned)
            try:
                _persist_edit_url(url)
            except Exception as exc:
                print(f"note: mapping persist skipped: {exc}")
            report["discovery"] = learned
            return learned
        now = asyncio.get_event_loop().time()
        if now - last_ping > 15:
            print(f"  en attente du formulaire Modifier Kraken… url={url}")
            last_ping = now
        await asyncio.sleep(1.5)
    report["page_log"] = logs
    print("TIMEOUT: aucun vrai formulaire d'édition Kraken ouvert")
    return None


CK_ID = "edit_parrainage_presentation"


async def _ck_get(page) -> str:
    return (
        await page.evaluate(
            """
            (id) => {
              const inst = window.CKEDITOR && CKEDITOR.instances && CKEDITOR.instances[id];
              if (inst) return inst.getData() || '';
              const ta = document.getElementById(id);
              return ta ? (ta.value || '') : '';
            }
            """,
            CK_ID,
        )
        or ""
    )


async def _set_link_only(page, field: dict) -> None:
    # Hidden CKEditor textarea is not fill()-able. Edit via CKEDITOR API only.
    result = await page.evaluate(
        """
        ({id, oldLink, newLink}) => {
          const inst = window.CKEDITOR && CKEDITOR.instances && CKEDITOR.instances[id];
          if (!inst) return {ok: false, reason: 'no_ckeditor'};
          const data = inst.getData() || '';
          if (!data.includes(oldLink)) return {ok: false, reason: 'old_missing', data};
          if (data.includes(newLink)) return {ok: false, reason: 'new_already'};
          const next = data.split(oldLink).join(newLink);
          if (next === data) return {ok: false, reason: 'no_change'};
          inst.setData(next);
          if (inst.updateElement) inst.updateElement();
          const after = inst.getData() || '';
          const ta = document.getElementById(id);
          if (ta) ta.value = after;
          return {
            ok: after.includes(newLink) && !after.includes(oldLink),
            after,
          };
        }
        """,
        {"id": CK_ID, "oldLink": OLD_LINK, "newLink": NEW_LINK},
    )
    if isinstance(result, dict) and result.get("ok"):
        await asyncio.sleep(0.4)
        return
    if isinstance(result, dict) and result.get("reason") == "no_ckeditor":
        frame = page.frame_locator("iframe.cke_wysiwyg_frame, iframe.cke_wysiwyg_div iframe").first
        body = frame.locator("body")
        html = await body.inner_html()
        if OLD_LINK not in html or NEW_LINK in html:
            raise RuntimeError("targeted replace failed — STOP no save")
        nxt = html.replace(OLD_LINK, NEW_LINK)
        await body.evaluate("(el, html) => { el.innerHTML = html; }", nxt)
        got = await body.inner_html()
        if NEW_LINK not in got or OLD_LINK in got:
            raise RuntimeError("targeted replace failed — STOP no save")
        return
    raise RuntimeError(f"targeted replace failed — STOP no save ({result})")


async def _click_save(page) -> str:
    try:
        await page.evaluate(
            """
            (id) => {
              const inst = window.CKEDITOR && CKEDITOR.instances && CKEDITOR.instances[id];
              if (inst && inst.updateElement) inst.updateElement();
            }
            """,
            CK_ID,
        )
    except Exception:
        pass
    # NEVER the site search form (texte_results.php). Only the announcement form.
    form = page.locator('form[action*="parrainages/edit"]').first
    if await form.count() == 0:
        raise RuntimeError("unexpected_dom: formulaire parrainages/edit introuvable")
    candidates = form.locator(
        'input[type="submit"], button[type="submit"], '
        'button:has-text("Valider"), button:has-text("Enregistrer"), '
        'input[value*="Valid" i], input[value*="Enregistr" i]'
    )
    count = await candidates.count()
    chosen = None
    chosen_label = ""
    for i in range(count):
        b = candidates.nth(i)
        label = ((await b.inner_text()) or (await b.get_attribute("value") or "")).strip()
        low = label.lower()
        if any(x in low for x in ("boost", "remont", "supprim", "delete", "recherch", "search")):
            continue
        if not chosen:
            chosen, chosen_label = b, label
        if any(x in low for x in ("valid", "enregistr", "sauvegard")):
            chosen, chosen_label = b, label
            break
    if chosen is None:
        raise RuntimeError("unexpected_dom: bouton Valider du formulaire d'annonce introuvable")
    print(f"click save on announcement form: {chosen_label!r}")
    await chosen.click()
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(2.0)
    return chosen_label or "valider"


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
        "no_guessed_urls": True,
        "offer_id": OFFER_ID,
        "WRITE_VERIFIED": False,
        "save_submitted": False,
    }
    forbidden = abort_forbidden_publish(NEW_LINK)
    if forbidden:
        report["error"] = forbidden
        _write(OUT, report)
        return 2

    pw = browser = ctx = page = None
    status_map: dict[str, int] = {}
    try:
        pw, browser, ctx, page = await _launch()
        _attach_status(page, status_map)
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            consent = await handle_cookie_consent(page)
        except ConsentBlocked as exc:
            report["error"] = str(exc)
            _write(OUT, report)
            print(str(exc))
            return 7
        report["cookie_consent_handled"] = consent.get("cookie_consent_handled")
        report["login_form_visible"] = bool(consent.get("login_form_visible"))
        print(f"cookie_consent_handled={report['cookie_consent_handled']}")
        print(f"login_form_visible={report['login_form_visible']}")
        if not report["login_form_visible"]:
            report["error"] = "CONSENT_BLOCKED: #_username not visible"
            _write(OUT, report)
            return 7

        ok = await _wait_espace_parrain(page)
        report["authenticated"] = ok
        if not ok:
            report["error"] = "espace_parrain_not_reached"
            _write(OUT, report)
            return 3

        # Inspect account home (no guessed navigation).
        home = await _inspect_page(page, requested_url=page.url, status_map=status_map)
        report["espace_parrain_inspect"] = home

        known = None
        try:
            known = (json.loads(mapping_path("1parrainage", "kraken", "fr").read_text(encoding="utf-8")) or {}).get("edit_url")
        except Exception:
            known = None
        if known and "parrainages/edit/" in str(known):
            print(f"URL d'édition déjà apprise — ouverture même session: {known}")
            await page.goto(str(known), wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1.0)

        learned = await _wait_manual_edit_form(page, status_map, report)
        if not learned:
            report["error"] = "edit_form_not_opened — no save"
            _write(OUT, report)
            return 4

        edit_url = learned["actual_edit_url"]
        report["actual_edit_url"] = edit_url
        report["offer_100408_found"] = True
        report["edit_url"] = edit_url

        field = await _locate_presentation(page, learned)
        value = (field or {}).get("value") or ""
        plain = _plain(value)
        report["old_link_verified"] = OLD_LINK in value
        hay_code = CODE in value or CODE in plain
        hay_reward = _has_reward(value)
        if not field or not report["old_link_verified"]:
            report["error"] = "OLD link not exactly in edit field — STOP no save"
            _write(OUT, report)
            print("STOP: ancien lien absent du champ — aucun save")
            return 5
        if NEW_LINK in value:
            report["error"] = "NEW already present — STOP no save"
            _write(OUT, report)
            return 5
        if not hay_code or not hay_reward:
            others = await _editable_fields(page)
            blob = "\n".join(f.get("value") or "" for f in others)
            hay_code = hay_code or CODE in blob or CODE in _plain(blob)
            hay_reward = hay_reward or _has_reward(blob)
        if not hay_code or not hay_reward:
            report["error"] = "precondition code/reward missing — STOP no save"
            _write(OUT, report)
            return 5

        print("=== DISCOVERY DONE — canary targeted link only (CKEditor) ===")
        current = await _ck_get(page) or value
        if OLD_LINK not in current or NEW_LINK in current:
            report["error"] = "CKEditor data missing OLD — STOP no save"
            _write(OUT, report)
            return 6
        snap = snapshot_state("canary:1parrainage:headed")
        report["snapshot"] = snap.get("id")
        await _set_link_only(page, field)
        after = await _ck_get(page)
        if NEW_LINK not in after or OLD_LINK in after:
            report["error"] = "replace failed — STOP no save"
            _write(OUT, report)
            return 6
        if after.replace(NEW_LINK, OLD_LINK) != current:
            report["error"] = "non-targeted text change — STOP no save"
            _write(OUT, report)
            return 6
        report["targeted_field_only"] = True

        label = await _click_save(page)
        report["save_submitted"] = True
        report["save_label"] = label
        print(f"saved via {label!r}")

        await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.8)
        acc_blob = await _ck_get(page)
        if not acc_blob:
            acc_blob = "\n".join(f.get("value") or "" for f in await _editable_fields(page))
        report["account_reread"] = True
        report["new_link_verified_account"] = NEW_LINK in acc_blob and OLD_LINK not in acc_blob
        report["code_unchanged"] = CODE in acc_blob
        report["reward_unchanged"] = _has_reward(acc_blob)

        pub = fetch_text(PUBLIC_LIST)
        report["public_reread"] = True
        report["new_link_verified_public"] = NEW_LINK in (pub or "")
        report["old_absent_public"] = OLD_LINK not in (pub or "")
        report["code_public"] = CODE in (pub or "")
        report["reward_public"] = REWARD in (pub or "") or (
            "200" in (pub or "") and "crypto" in (pub or "").lower()
        )
        report["new_link_verified"] = bool(
            report["new_link_verified_account"] and report["new_link_verified_public"]
        )
        report["immutable_preserved"] = bool(
            report.get("targeted_field_only")
            and report["code_unchanged"]
            and report["reward_unchanged"]
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
            print("WRITE_VERIFIED 1parrainage")
        else:
            print("post_match incomplete — not WRITE_VERIFIED")

        _write(OUT, report)
        print(f"report={OUT}")
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
        _wipe_local()
        print("local auth/state wiped")


async def _locate_presentation(page, learned: dict | None = None) -> dict | None:
    for sel in ("#edit_parrainage_presentation", "textarea[name='edit_parrainage[presentation]']"):
        loc = page.locator(sel).first
        try:
            if await loc.count():
                v = await loc.input_value(timeout=5000)
                return {
                    "index": 0,
                    "name": "edit_parrainage[presentation]",
                    "id": "edit_parrainage_presentation",
                    "selector": sel,
                    "value": v,
                    "has_old": OLD_LINK in v,
                    "has_new": NEW_LINK in v,
                }
        except Exception:
            continue
    fields = await _editable_fields(page)
    sel = (learned or {}).get("field_selectors") or {}
    for f in fields:
        if sel.get("name") and f.get("name") == sel.get("name"):
            return f
        if sel.get("id") and f.get("id") == sel.get("id"):
            return f
        if f.get("has_old") or f.get("has_new"):
            return f
    return None


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
