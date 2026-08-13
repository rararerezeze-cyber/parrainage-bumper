"""Writer Code-Parrainage — login → edit → save → reread (account + public if any).

Uses the same login path as the historical bumper (email/password + slider solver).
Never clicks Actualiser/boost — only Enregistrer/Sauvegarder on the edit form.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from lib.http_fetch import fetch_text
from lib.mapping_guards import write_blocked_reason
from lib.offers import OffersRepository
from lib.phase import content_write_allowed, phase_name
from lib.safety import live_write_blocked_reason, maybe_trip_from_error
from lib.renderer import MappingRepository, Renderer, TemplateRepository
from lib.template_builder import extract_values_via_template

log = logging.getLogger("code_parrainage.writer")


def _bumper():
    import bumper as bumper_mod

    return bumper_mod


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
    platform: str = "code-parrainage",
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

    # Prefer dedicated edit_url; some mappings store edit under announcement_url (/modif/)
    ann = mapping.announcement_url
    edit = getattr(mapping, "edit_url", None)
    if not edit and ann and ("/modif/" in ann or "/edit" in ann):
        edit = ann
        ann = None

    return WritePlan(
        platform=platform,
        program=program,
        language=language,
        announcement_url=ann,
        edit_url=edit,
        historical=historical,
        rendered=rendered,
        variables=variables,
        platform_values=hist_vals,
        changed_fields=changed,
        structure_preserved=structure_preserved,
        mutable_fields=list(mapping.mutable_fields),
    )


def plan_report_lines(plan: WritePlan) -> list[str]:
    lines = [
        f"WRITE PLAN {plan.platform}/{plan.program}.{plan.language}",
        f"public: {plan.announcement_url}",
        f"edit:   {plan.edit_url}",
        f"Structure preserved: {plan.structure_preserved}",
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
        "action": "WOULD_UPDATE" if plan.changed_fields else "NOOP",
        "pipeline": ["login", "edit", "save", "reread_account", "reread_public_if_any"],
        "live": False,
        "canary_ready_gate": content_write_allowed("code-parrainage"),
        "note": "Login uses historical bumper slider solver (same as bump path).",
    }


async def _login(page, cfg: dict) -> None:
    bumper = _bumper()
    if not cfg.get("email") or not cfg.get("password"):
        raise RuntimeError("CODE_PARRAINAGE_EMAIL/PASSWORD manquants")
    await page.goto(f"{cfg['url']}/login", wait_until="networkidle", timeout=60000)
    await bumper.human_sleep(1.0, 2.0)
    await bumper.robust_fill(page, 'input[type="email"]', cfg["email"])
    await bumper.robust_fill(page, 'input[type="password"]', cfg["password"])
    slider_ok = False
    for _ in range(3):
        if await bumper.solve_slider(page):
            slider_ok = True
            break
        await bumper.human_sleep(1.0, 2.0)
        await page.goto(f"{cfg['url']}/login", wait_until="networkidle", timeout=60000)
        await bumper.robust_fill(page, 'input[type="email"]', cfg["email"])
        await bumper.robust_fill(page, 'input[type="password"]', cfg["password"])
    if not slider_ok:
        raise RuntimeError("slider captcha non resolu (pas de contournement anti-bot)")
    await asyncio.sleep(0.8)
    await bumper.human_click(
        page, page.locator('button:has-text("Se connecter"), button[type="submit"]').first
    )
    try:
        await page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
    except Exception:
        pass
    await page.wait_for_load_state("networkidle")
    if not await bumper.verify_login(page, "/login", "code-parrainage"):
        raise RuntimeError("login code-parrainage echoue")


async def _resolve_edit_url(page, plan: WritePlan, base: str) -> str:
    if plan.edit_url:
        return plan.edit_url
    await page.goto(f"{base}/moncompte", wait_until="networkidle", timeout=60000)
    await _bumper().human_sleep(1.5, 2.5)
    rows = await page.evaluate(
        """
        (program) => {
          const out = [];
          for (const a of document.querySelectorAll('a[href]')) {
            const href = a.href || '';
            const label = ((a.innerText||'') + ' ' + href).toLowerCase();
            if (!href) continue;
            if (label.includes('actualis') || label.includes('supprim') || label.includes('boost')) continue;
            const isEdit = label.includes('modif') || href.includes('modif') || href.includes('edit');
            if (!isEdit) continue;
            const row = a.closest('tr, .card, li, article, div');
            const rowText = row ? (row.innerText||'').toLowerCase() : '';
            const isProg = label.includes(program) || rowText.includes(program) || href.includes(program);
            out.push({href, isProg});
          }
          return out;
        }
        """,
        plan.program,
    )
    for h in rows or []:
        if h.get("isProg"):
            return h["href"]
    if rows:
        return rows[0]["href"]
    raise RuntimeError(f"edit URL introuvable pour {plan.program} sur /moncompte")


async def _fill_and_save(page, text: str, code: str | None, link: str | None) -> list[str]:
    bumper = _bumper()
    steps: list[str] = []
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
    # contenteditable fallbacks
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
        raise RuntimeError("aucun champ editable trouve sur code-parrainage")

    btn = page.locator(
        'button:has-text("Enregistrer"), button:has-text("Sauvegarder"), '
        'button:has-text("Modifier"), button:has-text("Valider"), '
        'input[type="submit"], button[type="submit"]'
    )
    count = await btn.count()
    chosen = None
    for i in range(count):
        b = btn.nth(i)
        label = ((await b.inner_text()) or (await b.get_attribute("value") or "")).lower()
        if any(x in label for x in ("actualis", "boost", "remont", "supprim", "delete")):
            continue
        chosen = b
        break
    if chosen is None:
        raise RuntimeError("bouton Enregistrer introuvable (pas Actualiser)")
    await bumper.human_click(page, chosen)
    await page.wait_for_load_state("networkidle")
    await bumper.human_sleep(1.5, 2.5)
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


def _extract_public_body(html: str) -> str:
    text = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text)
    m = re.search(r"(⭐️ Offre Parrainage[\s\S]*?discord\.gg/\S+ ↩️)", text)
    if m:
        return m.group(1).strip()
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
    circ = live_write_blocked_reason("code-parrainage")
    if circ and not dry_run:
        return WriteResult(ok=False, plan=plan, error=f"CIRCUIT_OPEN: {circ}", steps=["circuit"])
    if not plan.structure_preserved:
        return WriteResult(
            ok=False, plan=plan, error="structure_not_preserved", steps=steps
        )
    if not plan.changed_fields:
        return WriteResult(
            ok=True,
            plan=plan,
            steps=["noop"],
            post_match=True,
            post_publish_text=plan.historical,
            evidence_checks={
                "authenticated": False,
                "targeted_edit": False,
                "submit_ok": False,
                "reread_account": False,
                "expected_values_present": True,
                "immutable_preserved": True,
            },
        )
    if dry_run or not content_write_allowed("code-parrainage"):
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
    cfg = bumper.CONFIG["code"]
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
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = await bumper.new_context(browser)
        page = await ctx.new_page()
        try:
            steps.append("login")
            await _login(page, cfg)
            steps.append("find_edit")
            edit_url = await _resolve_edit_url(page, plan, cfg["url"])
            steps.append(f"edit_url={edit_url}")
            await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
            await bumper.human_sleep(1.2, 2.0)
            try:
                await page.screenshot(path="debug_code_write_before.png", full_page=True)
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
            try:
                await page.screenshot(path="debug_code_write_after.png", full_page=True)
            except Exception:
                pass
        except Exception as exc:
            maybe_trip_from_error(str(exc), platform="code-parrainage")
            return WriteResult(
                ok=False, plan=plan, edit_url=edit_url, error=str(exc), steps=steps
            )
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    account_ok = bool(account_text) and (
        _norm(plan.rendered) in _norm(account_text)
        or _values_present(account_text, plan)
    )
    published = None
    public_match = None
    if plan.announcement_url and "/modif/" not in plan.announcement_url:
        steps.append("reread_public")
        await asyncio.sleep(2)
        try:
            html = fetch_text(plan.announcement_url)
            published = _extract_public_body(html)
            public_match = _norm(published) == _norm(plan.rendered) or _norm(
                plan.rendered
            ) in _norm(published)
            steps.append(f"post_match_public={public_match}")
        except Exception as exc:
            steps.append(f"public_reread_error={exc}")
            public_match = None
    else:
        steps.append("public_url_absent_or_is_edit — account reread is authoritative")

    # post_match: prefer public; fall back to account when no public URL
    if public_match is True:
        match = True
    elif public_match is False:
        match = False
    else:
        match = account_ok

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
