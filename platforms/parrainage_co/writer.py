"""Writer Parrainage.co — login → edit → save → reread (account + public).

CANARY_READY path: content_write_allowed() when status is CANARY_READY.
WRITE_VERIFIED only after post_match + evidence checks (controlled_write tool).
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
sys.path.insert(0, str(_ROOT / "tools"))

from lib.http_fetch import fetch_text
from lib.mapping_guards import write_blocked_reason
from lib.offers import OffersRepository
from lib.phase import content_write_allowed, phase_name
from lib.renderer import MappingRepository, Renderer, TemplateRepository
from lib.template_builder import extract_values_via_template

log = logging.getLogger("parrainage_co.writer")


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
    platform: str = "parrainage-co",
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

    return WritePlan(
        platform=platform,
        program=program,
        language=language,
        announcement_url=mapping.announcement_url,
        edit_url=getattr(mapping, "edit_url", None),
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
        "pipeline": ["login", "edit", "save", "reread_account", "reread_public"],
        "live": False,
        "canary_ready_gate": content_write_allowed("parrainage-co"),
    }


async def _login(page, ctx, cfg: dict) -> None:
    bumper = _bumper()
    rm = cfg.get("rm_cookie") or ""
    if rm:
        await ctx.add_cookies(
            [
                {
                    "name": "parrainageco_rm",
                    "value": rm.strip(),
                    "domain": "parrainage.co",
                    "path": "/",
                }
            ]
        )
    await page.goto(f"{cfg['url']}/account/offers", wait_until="domcontentloaded", timeout=60000)
    await bumper.human_sleep(1.5, 2.5)
    if "/login" in page.url:
        if not cfg.get("email") or not cfg.get("password"):
            raise RuntimeError(
                "session requise — set PARRAINAGE_CO_RM_COOKIE or "
                "PARRAINAGE_CO_EMAIL + PARRAINAGE_CO_PASSWORD"
            )
        await page.goto(f"{cfg['url']}/account/login", wait_until="domcontentloaded", timeout=60000)
        ok = await bumper.smart_login_parrainage(page, cfg["email"], cfg["password"])
        if not ok:
            raise RuntimeError("login parrainage.co echoue")
        await page.goto(f"{cfg['url']}/account/offers", wait_until="domcontentloaded", timeout=60000)
        await bumper.human_sleep(1.5, 2.5)


async def _resolve_edit_url(page, plan: WritePlan) -> str:
    if plan.edit_url and "/account/" in plan.edit_url:
        return plan.edit_url
    hrefs = await page.evaluate(
        """
        (program) => {
          const out = [];
          for (const a of document.querySelectorAll('a[href]')) {
            const href = a.href || '';
            const label = ((a.innerText||'') + ' ' + href).toLowerCase();
            if (!href || href.includes('boost') || href.includes('delete') || href.includes('supprim')) continue;
            const isEdit = label.includes('modifier') || href.includes('/edit') || href.includes('update');
            const isProg = label.includes(program) || href.includes(program);
            if (isEdit) out.push({href, isProg, label: label.slice(0,80)});
          }
          return out;
        }
        """,
        plan.program,
    )
    for h in hrefs or []:
        if h.get("isProg"):
            return h["href"]
    if plan.announcement_url and "/offers/" in plan.announcement_url:
        oid = plan.announcement_url.rstrip("/").split("/")[-1]
        for h in hrefs or []:
            if oid in h["href"]:
                return h["href"]
        return f"https://parrainage.co/account/offers/edit/{oid}"
    if hrefs:
        return hrefs[0]["href"]
    raise RuntimeError(f"edit URL introuvable pour {plan.program}")


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
    if code:
        for sel in ('input[name*="code" i]', 'input[id*="code" i]'):
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
        raise RuntimeError("aucun champ editable trouve sur parrainage.co")

    btn = page.locator(
        'button:has-text("Enregistrer"), button:has-text("Sauvegarder"), '
        'button:has-text("Mettre à jour"), input[type="submit"]'
    )
    count = await btn.count()
    chosen = None
    for i in range(count):
        b = btn.nth(i)
        label = ((await b.inner_text()) or (await b.get_attribute("value") or "")).lower()
        if any(x in label for x in ("boost", "remont", "supprim", "delete")):
            continue
        chosen = b
        break
    if chosen is None:
        raise RuntimeError("bouton Enregistrer introuvable (ou seulement boost)")
    await bumper.human_click(page, chosen)
    await page.wait_for_load_state("networkidle")
    await bumper.human_sleep(1.5, 2.5)
    steps.append("saved")
    return steps


async def _reread_account_fields(page) -> str:
    """Read edit-form values after save (account-side reread)."""
    payload = await page.evaluate(
        """
        () => {
          const areas = Array.from(document.querySelectorAll('textarea'))
            .map(t => (t.value || '').trim())
            .filter(Boolean);
          const inputs = Array.from(document.querySelectorAll('input'))
            .map(i => (i.value || '').trim())
            .filter(v => v.length > 2 && v.length < 500);
          const body = areas.sort((a,b)=>b.length-a.length)[0] || '';
          return {body, inputs};
        }
        """
    )
    body = (payload or {}).get("body") or ""
    extras = "\n".join((payload or {}).get("inputs") or [])
    return (body + "\n" + extras).strip()


def _extract_public_body(html: str) -> str:
    m = re.search(r"(⭐️ Offre Parrainage[\s\S]*?discord\.gg/\S+ ↩️)", html)
    if m:
        return m.group(1)
    text = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text)
    m = re.search(r"(⭐️ Offre Parrainage[\s\S]*?discord\.gg/\S+ ↩️)", text)
    if m:
        return m.group(1).strip()
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:4000]


def _norm(s: str) -> str:
    return "\n".join(line.rstrip() for line in s.replace("\r\n", "\n").split("\n")).strip()


def _values_present(haystack: str, plan: WritePlan) -> bool:
    for field in plan.mutable_fields:
        val = plan.variables.get(field)
        if val and str(val) not in haystack:
            return False
    return True


async def execute_write(plan: WritePlan, *, dry_run: bool = True) -> WriteResult:
    steps: list[str] = []
    blocked = write_blocked_reason(plan.platform, plan.program, plan.language)
    if blocked:
        return WriteResult(
            ok=False,
            plan=plan,
            error=f"WRITE_BLOCKED: {blocked} — mapping absent/stale, pas de publication",
            steps=["blocked_stale_or_absent"],
        )
    if not plan.structure_preserved:
        return WriteResult(
            ok=False,
            plan=plan,
            error="ABORT: structure non preserve",
            steps=steps,
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
    if dry_run or not content_write_allowed("parrainage-co"):
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
    cfg = bumper.CONFIG["parrainage"]
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
            await _login(page, ctx, cfg)
            steps.append("find_edit")
            edit_url = await _resolve_edit_url(page, plan)
            steps.append(f"edit_url={edit_url}")
            await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
            await bumper.human_sleep(1.2, 2.0)
            try:
                await page.screenshot(path="debug_parrainage_write_before.png", full_page=True)
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
            # Account reread: reopen edit page
            steps.append("reread_account")
            await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
            await bumper.human_sleep(1.0, 1.8)
            account_text = await _reread_account_fields(page)
            steps.append(f"account_reread_len={len(account_text or '')}")
            try:
                await page.screenshot(path="debug_parrainage_write_after.png", full_page=True)
            except Exception:
                pass
        except Exception as exc:
            return WriteResult(
                ok=False, plan=plan, edit_url=edit_url, error=str(exc), steps=steps
            )
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    steps.append("reread_public")
    url = plan.announcement_url
    if not url or "/account/" in url:
        return WriteResult(
            ok=False,
            plan=plan,
            edit_url=edit_url,
            account_reread_text=account_text,
            error="pas d'announcement_url public pour post-verify",
            steps=steps,
        )
    await asyncio.sleep(2)
    try:
        html = fetch_text(url)
        published = _extract_public_body(html)
    except Exception as exc:
        return WriteResult(
            ok=False,
            plan=plan,
            edit_url=edit_url,
            account_reread_text=account_text,
            error=f"relecture publique: {exc}",
            steps=steps,
        )

    public_match = _norm(published) == _norm(plan.rendered) or _norm(plan.rendered) in _norm(
        published
    )
    account_ok = bool(account_text) and (
        _norm(plan.rendered) in _norm(account_text)
        or _values_present(account_text, plan)
    )
    expected_ok = _values_present(published, plan) or _values_present(account_text or "", plan)
    checks = {
        "authenticated": True,
        "targeted_edit": bool(plan.changed_fields),
        "submit_ok": "saved" in steps,
        "reread_account": account_ok,
        "expected_values_present": expected_ok,
        "immutable_preserved": plan.structure_preserved,
        "reread_public": public_match,
    }
    steps.append(f"post_match={public_match}")
    steps.append(f"account_match={account_ok}")
    if not public_match:
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
        post_publish_text=published,
        account_reread_text=account_text,
        post_match=True,
        steps=steps,
        evidence_checks=checks,
    )
