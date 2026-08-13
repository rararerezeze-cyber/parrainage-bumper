"""Writer 1Parrainage — login → edit → save → reread (account + public list).

CANARY_READY path: content_write_allowed() when status is CANARY_READY.
No live write unless dry_run=False AND canary gate open.
Never clicks Boost / Remonter — only Enregistrer on the edit form.
Stop on 403/429/CAPTCHA/auth/unexpected DOM. No anti-bot bypass.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from lib.auth_policy import classify_auth_failure, should_stop_platform
from lib.http_fetch import fetch_text
from lib.mapping_guards import write_blocked_reason
from lib.offers import OffersRepository
from lib.paths import mapping_path
from lib.phase import content_write_allowed, phase_name
from lib.renderer import MappingRepository, Renderer, TemplateRepository
from lib.safety import abort_forbidden_publish, live_write_blocked_reason, maybe_trip_from_error
from lib.template_builder import extract_values_via_template

log = logging.getLogger("oneparrainage.writer")

BASE = "https://www.1parrainage.com"
LOGIN_URL = f"{BASE}/login"
PUBLIC_LIST = "https://www.1parrainage.com/listeannonces_98906_Adrien89.php"

# Proven public + likely member targets (resolved after login)
KNOWN_PATHS = {
    "login": LOGIN_URL,
    "public_list": PUBLIC_LIST,
    "inscription": f"{BASE}/inscription.php",
    "password_reset": f"{BASE}/password_reset.php",
}

MEMBER_CANDIDATES = (
    f"{BASE}/espace_parrain/",
    f"{BASE}/espace_parrain/annonces/",
    f"{BASE}/espace_parrain/offres/",
    f"{BASE}/espace_membre.php",
    f"{BASE}/espace-parrain",
    f"{BASE}/mes-annonces.php",
    f"{BASE}/mes_annonces.php",
    f"{BASE}/gestion_annonces.php",
    f"{BASE}/annonces_membre.php",
    f"{BASE}/membre",
    f"{BASE}/account",
    f"{BASE}/dashboard",
    f"{BASE}/parrain.php",
    PUBLIC_LIST,
)


def _bumper():
    import bumper as bumper_mod

    return bumper_mod


def _cfg() -> dict[str, str]:
    return {
        "url": BASE,
        "email": os.environ.get("ONEPARRAINAGE_EMAIL") or "",
        "password": os.environ.get("ONEPARRAINAGE_PASSWORD") or "",
    }


@dataclass
class WritePlan:
    platform: str
    program: str
    language: str
    announcement_url: str | None
    edit_url: str | None
    historical: str
    rendered: str
    variables: dict[str, str | None]
    platform_values: dict[str, str]
    changed_fields: dict[str, dict[str, str | None]]
    structure_preserved: bool
    mutable_fields: list[str]
    platform_offer_id: str | None = None
    style_policy: str = "native_platform_style_only"


@dataclass
class WriteResult:
    ok: bool
    plan: WritePlan
    edit_url: str | None = None
    post_publish_text: str | None = None
    account_reread_text: str | None = None
    post_match: bool | None = None
    error: str | None = None
    steps: list[str] | None = None
    evidence_checks: dict[str, bool] | None = None


def build_write_plan(
    platform: str = "1parrainage",
    program: str = "kraken",
    language: str = "fr",
) -> WritePlan:
    mapping = MappingRepository().load(platform, program, language)
    templates = TemplateRepository()
    renderer = Renderer(OffersRepository())
    template = templates.load_text(platform, program, language)
    historical = templates.load_golden(platform, program, language)
    offer = renderer.offers.get_by_slug(program)
    variables = renderer.build_variables(mapping, offer=offer)
    rendered = renderer.render(template, mapping, offer=offer)

    hist_vals = dict(mapping.platform_values or {})
    extracted = extract_values_via_template(
        template, historical, mapping.mutable_fields, mapping.markers
    )
    for k, v in extracted.items():
        hist_vals.setdefault(k, v)

    changed: dict[str, dict[str, str | None]] = {}
    for field in mapping.mutable_fields:
        old = hist_vals.get(field)
        new = variables.get(field)
        if old != new:
            changed[field] = {"old": old, "new": new}

    check = historical
    for field in mapping.mutable_fields:
        old = hist_vals.get(field)
        new = variables.get(field)
        if old and new is not None and old in check:
            check = check.replace(old, new, 1)
    structure_preserved = check == rendered

    offer_id = getattr(mapping, "platform_offer_id", None)
    if not offer_id:
        raw_ann = mapping.announcement_url or ""
        m = re.search(r"[#?&]id=(\d+)", raw_ann)
        if m:
            offer_id = m.group(1)
        else:
            try:
                raw = json.loads(mapping_path(platform, program, language).read_text(encoding="utf-8"))
                offer_id = raw.get("platform_offer_id")
            except Exception:
                offer_id = None

    return WritePlan(
        platform=platform,
        program=program,
        language=language,
        announcement_url=mapping.announcement_url or PUBLIC_LIST,
        edit_url=getattr(mapping, "edit_url", None),
        historical=historical,
        rendered=rendered,
        variables=variables,
        platform_values=hist_vals,
        changed_fields=changed,
        structure_preserved=structure_preserved,
        mutable_fields=list(mapping.mutable_fields),
        platform_offer_id=str(offer_id) if offer_id else None,
    )


def plan_report_lines(plan: WritePlan) -> list[str]:
    lines = [
        f"WRITE PLAN {plan.platform}/{plan.program}.{plan.language}",
        f"public: {plan.announcement_url}",
        f"edit:   {plan.edit_url}",
        f"offer_id: {plan.platform_offer_id}",
        f"Structure preserved: {plan.structure_preserved}",
        f"style: {plan.style_policy}",
        "Changed fields:",
    ]
    if not plan.changed_fields:
        lines.append("  (none)")
    for k, d in plan.changed_fields.items():
        lines.append(f"  {k}: {d.get('old')!r} -> {d.get('new')!r}")
    return lines


def dry_run_report(program: str = "kraken", language: str = "fr") -> dict[str, Any]:
    plan = build_write_plan(program=program, language=language)
    return {
        "platform": plan.platform,
        "program": plan.program,
        "structure_preserved": plan.structure_preserved,
        "changed_fields": plan.changed_fields,
        "announcement_url": plan.announcement_url,
        "edit_url": plan.edit_url,
        "platform_offer_id": plan.platform_offer_id,
        "action": "WOULD_UPDATE" if plan.changed_fields else "NOOP",
        "pipeline": ["login", "edit", "save", "reread_account", "reread_public"],
        "live": False,
        "canary_ready_gate": content_write_allowed("1parrainage"),
        "style_policy": plan.style_policy,
        "known_paths": KNOWN_PATHS,
        "secrets": ["ONEPARRAINAGE_EMAIL", "ONEPARRAINAGE_PASSWORD"],
        "note": "Native list style only. Login is /login (not connexion.php). No live write in dry-run.",
    }


def _raise_if_stop(message: str, *, status: int | None = None) -> None:
    kind = classify_auth_failure(message, status_code=status)
    if should_stop_platform(kind):
        maybe_trip_from_error(message, platform="1parrainage", status_code=status)
        raise RuntimeError(f"STOP_{kind.value}: {message}")
    stop = maybe_trip_from_error(message, platform="1parrainage", status_code=status)
    if stop:
        raise RuntimeError(f"STOP_{stop}: {message}")


async def _detect_challenge(page) -> None:
    url = page.url or ""
    title = ""
    try:
        title = (await page.title()) or ""
    except Exception:
        pass
    body = ""
    try:
        body = ((await page.inner_text("body")) or "")[:1500]
    except Exception:
        pass
    blob = f"{url} {title} {body}".lower()
    if "just a moment" in blob or "cf-browser" in blob or "attention required" in blob:
        _raise_if_stop("cloudflare challenge")
    if "captcha" in blob or "recaptcha" in blob or "hcaptcha" in blob:
        _raise_if_stop("captcha present — no bypass")
    if "429" in blob or "too many requests" in blob:
        _raise_if_stop("rate limit", status=429)
    if "access denied" in blob or "403" in url:
        _raise_if_stop("forbidden", status=403)


async def _login(page, cfg: dict) -> None:
    bumper = _bumper()
    email = cfg.get("email") or ""
    password = cfg.get("password") or ""
    if not email or not password:
        raise RuntimeError(
            "AUTH_REQUIRED: ONEPARRAINAGE_EMAIL/PASSWORD not set"
        )
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    await bumper.human_sleep(1.0, 2.0)
    await _detect_challenge(page)
    # Consent banners can cover #_username in Linux headless (visible locally).
    for sel in (
        "#didomi-notice-agree-button",
        'button:has-text("Tout accepter")',
        'button:has-text("Accept all")',
        'button:has-text("J\'accepte")',
    ):
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=2000)
                await bumper.human_sleep(0.4, 0.8)
                break
        except Exception:
            continue
    if "/login" not in page.url and "connexion" not in page.url.lower():
        # already a session? confirm not bounced
        log.info("not on /login after goto — checking session")
    # Official form is form[action="/login"] with #_username (type=text) + #_password.
    # Do NOT use name*="mail" / #email — that matches the Sendinblue newsletter.
    email_ok = await bumper.smart_fill(
        page,
        [
            'form[action="/login"] input#_username',
            'form[action="/login"] input[name="_username"]',
            "input#_username",
            'input[name="_username"]',
        ],
        email,
        timeout=8000,
    )
    pass_ok = await bumper.smart_fill(
        page,
        [
            'form[action="/login"] input#_password',
            'form[action="/login"] input[name="_password"]',
            "input#_password",
            'input[name="_password"]',
            'form[action="/login"] input[type="password"]',
        ],
        password,
        timeout=8000,
    )
    if not email_ok or not pass_ok:
        await _detect_challenge(page)
        try:
            await page.screenshot(path="debug_1parrainage_login.png", full_page=True)
        except Exception:
            pass
        raise RuntimeError("unexpected_dom: login fields not found on /login")
    btn = page.locator(
        'form[action="/login"] input[type="submit"][name="submit"], '
        'form[action="/login"] input[value="Je me connecte"], '
        'input[type="submit"][name="submit"]'
    ).first
    await bumper.human_click(page, btn)
    try:
        await page.wait_for_load_state("networkidle", timeout=25000)
    except Exception:
        pass
    await bumper.human_sleep(1.0, 1.8)
    await _detect_challenge(page)
    if "/login" in page.url:
        body = ""
        try:
            body = ((await page.inner_text("body")) or "").lower()
        except Exception:
            pass
        if any(x in body for x in ("incorrect", "invalide", "erreur", "mot de passe")):
            raise RuntimeError("login 1parrainage echoue (invalid credentials?)")
        raise RuntimeError("login 1parrainage: still on /login after submit")


def _edit_candidates(offer_id: str | None) -> list[str]:
    if not offer_id:
        return []
    oid = offer_id
    return [
        f"{BASE}/modifier_annonce.php?id={oid}",
        f"{BASE}/edit_annonce.php?id={oid}",
        f"{BASE}/editer_annonce.php?id={oid}",
        f"{BASE}/annonce_edit.php?id={oid}",
        f"{BASE}/parrain_edit.php?id={oid}",
        f"{BASE}/modifier.php?id={oid}",
        f"{BASE}/annonce.php?action=edit&id={oid}",
        f"{BASE}/editer.php?id={oid}",
    ]


async def _resolve_edit_url(page, plan: WritePlan) -> str:
    bumper = _bumper()
    if plan.edit_url and "login" not in (plan.edit_url or ""):
        return plan.edit_url

    found: list[str] = []
    for url in MEMBER_CANDIDATES:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await bumper.human_sleep(0.8, 1.4)
            await _detect_challenge(page)
            if "/login" in page.url:
                continue
            hrefs = await page.evaluate(
                """
                (offerId) => {
                  const out = [];
                  for (const a of document.querySelectorAll('a[href]')) {
                    const href = a.href || '';
                    const label = ((a.innerText||'') + ' ' + href).toLowerCase();
                    if (!href.startsWith('http')) continue;
                    if (label.includes('boost') || label.includes('supprim') || label.includes('delete')) continue;
                    const isEdit = label.includes('modif') || label.includes('edit')
                      || href.includes('modif') || href.includes('edit')
                      || href.includes('editer');
                    const hasId = !offerId || href.includes(offerId) || label.includes(offerId);
                    if (isEdit && hasId) out.push(href);
                    else if (isEdit) out.push(href);
                  }
                  return out;
                }
                """,
                plan.platform_offer_id or "",
            )
            for h in hrefs or []:
                if h not in found:
                    found.append(h)
            if found:
                break
        except Exception as exc:
            _raise_if_stop(str(exc))
            continue

    oid = plan.platform_offer_id
    if oid:
        for h in found:
            if oid in h:
                return h
    if found:
        return found[0]

    # Last resort: try known edit URL patterns without submitting
    for cand in _edit_candidates(oid):
        try:
            await page.goto(cand, wait_until="domcontentloaded", timeout=30000)
            await _detect_challenge(page)
            if "/login" in page.url:
                continue
            areas = await page.locator("textarea").count()
            if areas:
                return cand
        except Exception as exc:
            _raise_if_stop(str(exc))
            continue
    raise RuntimeError(
        f"unexpected_dom: edit URL introuvable pour {plan.program} "
        f"(offer_id={oid}). Auth/edit not proven on this session."
    )


async def _fill_and_save(page, text: str, code: str | None, link: str | None) -> list[str]:
    bumper = _bumper()
    steps: list[str] = []
    await _detect_challenge(page)
    areas = page.locator("textarea")
    n = await areas.count()
    filled = False
    if n:
        best_i, best_len = 0, -1
        for i in range(n):
            v = await areas.nth(i).input_value()
            if len(v) > best_len:
                best_len = len(v)
                best_i = i
        await areas.nth(best_i).fill(text)
        steps.append(f"textarea[{best_i}]")
        filled = True
    if not filled:
        ce = page.locator('[contenteditable="true"], .ql-editor, .ProseMirror').first
        if await ce.count():
            await ce.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.type(text, delay=5)
            steps.append("contenteditable")
            filled = True
    if code:
        for sel in (
            'input[name*="code" i]',
            'input[id*="code" i]',
            'input[placeholder*="code" i]',
        ):
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.fill(code)
                steps.append("code_field")
                break
    if link:
        for sel in (
            'input[name*="lien" i]',
            'input[name*="link" i]',
            'input[name*="url" i]',
            'input[id*="lien" i]',
        ):
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.fill(link)
                steps.append("link_field")
                break
    if not filled and not steps:
        raise RuntimeError("unexpected_dom: aucun champ editable trouve sur 1parrainage")

    btn = page.locator(
        'button:has-text("Enregistrer"), button:has-text("Sauvegarder"), '
        'button:has-text("Mettre à jour"), button:has-text("Valider"), '
        'button:has-text("Modifier"), input[type="submit"], button[type="submit"]'
    )
    count = await btn.count()
    chosen = None
    for i in range(count):
        b = btn.nth(i)
        label = ((await b.inner_text()) or (await b.get_attribute("value") or "")).lower()
        if any(x in label for x in ("boost", "remont", "supprim", "delete", "actualis")):
            continue
        chosen = b
        break
    if chosen is None:
        raise RuntimeError("unexpected_dom: bouton Enregistrer introuvable (pas Boost/Remonter)")
    await bumper.human_click(page, chosen)
    await page.wait_for_load_state("networkidle")
    await bumper.human_sleep(1.5, 2.5)
    await _detect_challenge(page)
    steps.append("saved")
    return steps


async def _reread_account_fields(page) -> str:
    payload = await page.evaluate(
        """
        () => {
          const areas = Array.from(document.querySelectorAll('textarea'))
            .map(t => (t.value || t.innerText || '').trim())
            .filter(t => t.length > 10);
          const ce = document.querySelector('[contenteditable="true"], .ql-editor, .ProseMirror');
          let body = areas.sort((a,b)=>b.length-a.length)[0] || '';
          if (ce) {
            const t = (ce.innerText||'').trim();
            if (t.length > body.length) body = t;
          }
          const inputs = Array.from(document.querySelectorAll('input'))
            .map(i => (i.value||'').trim())
            .filter(v => v.length > 2 && v.length < 500);
          return {body, inputs};
        }
        """
    )
    body = (payload or {}).get("body") or ""
    extras = "\n".join((payload or {}).get("inputs") or [])
    return (body + "\n" + extras).strip()


def _norm(s: str) -> str:
    return "\n".join(line.rstrip() for line in s.replace("\r\n", "\n").split("\n")).strip()


def _values_present(haystack: str, plan: WritePlan) -> bool:
    for field in plan.mutable_fields:
        val = plan.variables.get(field)
        if val and str(val) not in haystack:
            return False
    return True


def _extract_public_block(html: str, plan: WritePlan) -> str:
    oid = plan.platform_offer_id
    if oid:
        m = re.search(
            rf"id={re.escape(oid)}[\s\S]{{0,250}}?(Offre[\s\S]{{40,800}}?)(?:J.en profite|coupon-wrapper|coupon-list)",
            html,
            flags=re.I,
        )
        if m:
            text = re.sub(r"<[^>]+>", "\n", unescape(m.group(1)))
            return re.sub(r"\n{3,}", "\n\n", text).strip()
    text = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text)
    needle = plan.historical.split("\n", 1)[0].strip()[:40]
    if needle and needle in text:
        i = text.find(needle)
        return re.sub(r"\n{3,}", "\n\n", text[i : i + 1200]).strip()
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:4000]


async def execute_write(plan: WritePlan, *, dry_run: bool = True) -> WriteResult:
    steps: list[str] = []
    blocked = write_blocked_reason(plan.platform, plan.program, plan.language)
    if blocked:
        return WriteResult(
            ok=False,
            plan=plan,
            error=f"WRITE_BLOCKED: {blocked}",
            steps=["blocked"],
        )
    circ = live_write_blocked_reason("1parrainage")
    if circ and not dry_run:
        return WriteResult(ok=False, plan=plan, error=f"CIRCUIT_OPEN: {circ}", steps=["circuit"])
    if not plan.structure_preserved:
        return WriteResult(ok=False, plan=plan, error="structure_not_preserved", steps=steps)
    if not plan.changed_fields:
        return WriteResult(
            ok=True,
            plan=plan,
            steps=["noop"],
            post_match=None,
            post_publish_text=plan.historical,
            error="NO_SAFE_DIFF",
            evidence_checks={
                "authenticated": False,
                "targeted_edit": False,
                "submit_ok": False,
                "reread_account": False,
                "expected_values_present": True,
                "immutable_preserved": True,
            },
        )
    forbidden = abort_forbidden_publish(
        plan.rendered,
        *(str((d or {}).get("new") or "") for d in (plan.changed_fields or {}).values()),
    )
    if forbidden:
        return WriteResult(
            ok=False, plan=plan, error=forbidden, steps=["forbidden_publish"]
        )
    if dry_run or not content_write_allowed("1parrainage"):
        return WriteResult(
            ok=True,
            plan=plan,
            steps=[
                "dry-run only"
                if dry_run
                else f"LIVE_DISABLED ({phase_name()}) — need CANARY_READY/WRITE_VERIFIED"
            ],
            post_match=None,
        )

    bumper = _bumper()
    cfg = _cfg()
    from playwright.async_api import async_playwright

    edit_url = None
    account_text = None
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--lang=fr-FR",
                "--window-size=1280,720",
            ],
        )
        ctx = await bumper.new_context(browser)
        try:
            await ctx.set_viewport_size({"width": 1280, "height": 720})
        except Exception:
            pass
        page = await ctx.new_page()
        try:
            steps.append("login")
            await _login(page, cfg)
            steps.append("find_edit")
            edit_url = await _resolve_edit_url(page, plan)
            steps.append(f"edit_url={edit_url}")
            await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
            await bumper.human_sleep(1.2, 2.0)
            try:
                await page.screenshot(path="debug_1parrainage_write_before.png", full_page=True)
            except Exception:
                pass
            steps.append("fill_save")
            fill_steps = await _fill_and_save(
                page,
                plan.rendered,
                plan.variables.get("personal_code"),
                plan.variables.get("personal_link"),
            )
            steps.extend(fill_steps)
            steps.append("reread_account")
            await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
            await bumper.human_sleep(1.0, 1.8)
            account_text = await _reread_account_fields(page)
            steps.append(f"account_reread_len={len(account_text or '')}")
        except Exception as exc:
            maybe_trip_from_error(str(exc), platform="1parrainage")
            return WriteResult(
                ok=False, plan=plan, edit_url=edit_url, error=str(exc), steps=steps
            )
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    account_ok = bool(account_text) and (
        _norm(plan.rendered) in _norm(account_text) or _values_present(account_text, plan)
    )
    published = None
    public_match = None
    if plan.announcement_url:
        steps.append("reread_public")
        await asyncio.sleep(2)
        try:
            html = fetch_text(
                plan.announcement_url.split("#")[0] if plan.announcement_url else PUBLIC_LIST
            )
            published = _extract_public_block(html, plan)
            public_match = _norm(published) == _norm(plan.rendered) or _norm(
                plan.rendered
            ) in _norm(published)
            steps.append(f"post_match_public={public_match}")
        except Exception as exc:
            steps.append(f"public_reread_error={exc}")
            public_match = None

    if public_match is True:
        match = True
    elif public_match is False and not account_ok:
        match = False
    else:
        match = account_ok or public_match is True

    checks = {
        "authenticated": True,
        "targeted_edit": bool(plan.changed_fields),
        "submit_ok": "saved" in steps,
        "reread_account": account_ok,
        "expected_values_present": _values_present(account_text or "", plan)
        or (bool(published) and _values_present(published, plan)),
        "immutable_preserved": plan.structure_preserved,
    }
    if public_match is not None:
        checks["reread_public"] = public_match
    steps.append(f"post_match={match}")
    if not match:
        return WriteResult(
            ok=False,
            plan=plan,
            edit_url=edit_url,
            post_publish_text=published,
            account_reread_text=account_text,
            post_match=False,
            error="POST-UPDATE MISMATCH — STOP",
            steps=steps,
            evidence_checks=checks,
        )
    return WriteResult(
        ok=True,
        plan=plan,
        edit_url=edit_url,
        post_publish_text=published or account_text,
        account_reread_text=account_text,
        post_match=True,
        steps=steps,
        evidence_checks=checks,
    )
