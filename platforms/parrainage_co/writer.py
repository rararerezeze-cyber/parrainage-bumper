"""Writer Parrainage.co — meme contrat que Super-Parrain (fetch→render→diff→write→verify)."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

from lib.http_fetch import fetch_text
from lib.offers import OffersRepository
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
    post_match: bool | None = None
    error: str | None = None
    steps: list[str] | None = None


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
        f"URL: {plan.announcement_url}",
        f"Structure preserved: {plan.structure_preserved}",
        "Changed fields:",
    ]
    if not plan.changed_fields:
        lines.append("  (none)")
    for k, d in plan.changed_fields.items():
        lines.append(f"  {k}: {d.get('old')!r} -> {d.get('new')!r}")
    return lines


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
            raise RuntimeError("session requise (cookie/login)")
        await page.goto(f"{cfg['url']}/account/login", wait_until="domcontentloaded", timeout=60000)
        ok = await bumper.smart_login_parrainage(page, cfg["email"], cfg["password"])
        if not ok:
            raise RuntimeError("login parrainage.co echoue")
        await page.goto(f"{cfg['url']}/account/offers", wait_until="domcontentloaded", timeout=60000)
        await bumper.human_sleep(1.5, 2.5)


async def _find_edit_url(page, program: str, announcement_url: str | None) -> str:
    # Prefer mapping announcement → find modifier nearby
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
        program,
    )
    for h in hrefs:
        if h.get("isProg"):
            return h["href"]
    # offer id from announcement_url /offers/113735
    if announcement_url and "/offers/" in announcement_url:
        oid = announcement_url.rstrip("/").split("/")[-1]
        for h in hrefs:
            if oid in h["href"]:
                return h["href"]
        # try common edit path
        return f"https://parrainage.co/account/offers/{oid}/edit"
    if hrefs:
        return hrefs[0]["href"]
    raise RuntimeError(f"edit URL introuvable pour {program}")


async def _fill_and_save(page, text: str, code: str | None, link: str | None) -> list[str]:
    bumper = _bumper()
    steps = []
    # textareas
    areas = page.locator("textarea")
    n = await areas.count()
    filled = False
    if n:
        # pick longest
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

    # Save — never boost
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


def _extract_public_body(html: str) -> str:
    """Extrait le bloc annonce si possible, sinon texte utile."""
    import re
    from html import unescape

    m = re.search(
        r"(⭐️ Offre Parrainage[\s\S]*?discord\.gg/\S+ ↩️)",
        html,
    )
    if m:
        # might be in HTML entities
        return m.group(1)
    # from raw page text
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
    if not plan.structure_preserved:
        return WriteResult(
            ok=False,
            plan=plan,
            error="ABORT: structure non preserve",
            steps=steps,
        )
    if not plan.changed_fields:
        return WriteResult(ok=True, plan=plan, steps=["noop"], post_match=True, post_publish_text=plan.historical)
    if dry_run:
        return WriteResult(ok=True, plan=plan, steps=["dry-run only"])

    bumper = _bumper()
    cfg = bumper.CONFIG["parrainage"]
    from playwright.async_api import async_playwright

    edit_url = None
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = await bumper.new_context(browser)
        page = await ctx.new_page()
        try:
            steps.append("login")
            await _login(page, ctx, cfg)
            steps.append("find_edit")
            edit_url = await _find_edit_url(page, plan.program, plan.announcement_url)
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
        except Exception as exc:
            return WriteResult(ok=False, plan=plan, edit_url=edit_url, error=str(exc), steps=steps)
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    # re-fetch public
    steps.append("reread")
    url = plan.announcement_url
    if not url:
        return WriteResult(
            ok=False, plan=plan, edit_url=edit_url,
            error="pas d'announcement_url pour post-verify", steps=steps,
        )
    await asyncio.sleep(2)
    try:
        html = fetch_text(url)
        published = _extract_public_body(html)
    except Exception as exc:
        return WriteResult(ok=False, plan=plan, edit_url=edit_url, error=f"relecture: {exc}", steps=steps)

    # Flexible match: rendered must be contained OR exact after normalize
    def norm(s: str) -> str:
        return "\n".join(line.rstrip() for line in s.replace("\r\n", "\n").split("\n")).strip()

    match = norm(published) == norm(plan.rendered) or norm(plan.rendered) in norm(published)
    steps.append(f"post_match={match}")
    if not match:
        return WriteResult(
            ok=False,
            plan=plan,
            edit_url=edit_url,
            post_publish_text=published,
            post_match=False,
            error="POST-UPDATE MISMATCH — STOP",
            steps=steps,
        )
    return WriteResult(
        ok=True,
        plan=plan,
        edit_url=edit_url,
        post_publish_text=published,
        post_match=True,
        steps=steps,
    )
