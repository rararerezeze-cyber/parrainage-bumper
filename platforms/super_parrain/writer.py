"""Ecriture contenu Super-Parrain (annonce) — separe du bump bumper.py."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(_ROOT / "tools"))

from lib.http_fetch import fetch_text
from lib.offers import OffersRepository
from lib.renderer import MappingRepository, Renderer, TemplateRepository
from lib.template_builder import extract_values_via_template
import capture_super_parrain as csp

log = logging.getLogger("super_parrain.writer")


def _bumper():
    import bumper as bumper_mod

    return bumper_mod


@dataclass
class WritePlan:
    platform: str
    program: str
    language: str
    announcement_url: str
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
    platform: str = "super-parrain",
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

    # Structure: only mutable values differ
    check = historical
    for field in mapping.mutable_fields:
        old = hist_vals.get(field)
        new = variables.get(field)
        if old and new is not None and old in check:
            check = check.replace(old, new, 1)
    structure_preserved = check == rendered

    url = mapping.announcement_url or (
        f"https://www.super-parrain.com/offres/{program}/parrainage-{program}/annonces/adrien-b-8"
    )
    return WritePlan(
        platform=platform,
        program=program,
        language=language,
        announcement_url=url,
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
        f"Structure preserved (only mutable vars): {plan.structure_preserved}",
        "Changed fields:",
    ]
    if not plan.changed_fields:
        lines.append("  (none — already in sync)")
    for k, d in plan.changed_fields.items():
        lines.append(f"  {k}: {d.get('old')!r} -> {d.get('new')!r}")
    lines.append("--- published (historical) ---")
    lines.append(plan.historical)
    lines.append("--- to publish (offers.json render) ---")
    lines.append(plan.rendered)
    return lines


async def _login_super(page, cfg: dict[str, str]) -> None:
    bumper_mod = _bumper()
    await page.goto(f"{cfg['url']}/login", wait_until="networkidle", timeout=60000)
    await bumper_mod.human_sleep(2, 3)
    await bumper_mod.wait_cloudflare(page)
    await bumper_mod.robust_fill(
        page, 'input[name="_username"], input[type="email"]', cfg["email"]
    )
    await bumper_mod.robust_fill(
        page, 'input[name="_password"], input[type="password"]', cfg["password"]
    )
    await bumper_mod.human_sleep(0.8, 1.5)
    await bumper_mod.human_click(
        page,
        page.locator(
            'input[type="submit"], button:has-text("Connexion"), button[type="submit"]'
        ).first,
    )
    try:
        await page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
    except Exception:
        pass
    await page.wait_for_load_state("networkidle")
    if not await bumper_mod.verify_login(page, "/login", "super-parrain-write"):
        raise RuntimeError("Login Super-Parrain echoue")


async def _find_kraken_edit_url(page, base: str, program: str) -> str:
    """Trouve l'URL d'edition de l'annonce (pas seulement codes-promo bump)."""
    bumper_mod = _bumper()
    candidates_pages = [
        f"{base}/tableau-de-bord/annonces",
        f"{base}/tableau-de-bord/mes-annonces",
        f"{base}/tableau-de-bord",
        f"{base}/tableau-de-bord/codes-promo",
        f"{base}/tableau-de-bord/parrainages",
    ]
    found: list[str] = []
    for url in candidates_pages:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await bumper_mod.human_sleep(1.5, 2.5)
        except Exception:
            continue
        hrefs = await page.evaluate(
            """
            (program) => {
              const out = [];
              const all = Array.from(document.querySelectorAll('a[href]'));
              for (const a of all) {
                const href = a.href || '';
                const txt = ((a.innerText || a.textContent || '') + ' ' + href).toLowerCase();
                if (!href) continue;
                if (href.includes('boost') || href.includes('supprim') || href.includes('delete')) continue;
                const isEdit = href.includes('edit') || href.includes('modifier')
                  || (a.innerText || '').toLowerCase().includes('modifier')
                  || (a.innerText || '').toLowerCase().includes('éditer')
                  || (a.innerText || '').toLowerCase().includes('editer');
                const isKraken = txt.includes('kraken') || href.includes('kraken');
                if (isEdit && (isKraken || href.includes('annonce') || href.includes('parrainage'))) {
                  out.push({href, txt: (a.innerText||'').trim().slice(0,80), kraken: isKraken});
                }
              }
              return out;
            }
            """,
            program,
        )
        for h in hrefs:
            if h.get("kraken"):
                return h["href"]
            found.append(h["href"])

        # Also scan page text for kraken rows + nearest edit link
        pair = await page.evaluate(
            """
            () => {
              const rows = Array.from(document.querySelectorAll('tr, .card, li, article, .list-item, .row'));
              for (const row of rows) {
                const t = (row.innerText || '').toLowerCase();
                if (!t.includes('kraken')) continue;
                const a = row.querySelector('a[href*="edit"], a[href*="modifier"], a[href*="annonce"]');
                if (a && a.href) return a.href;
              }
              return null;
            }
            """
        )
        if pair:
            return pair

    # Public announcement page sometimes has edit for owner
    try:
        await page.goto(
            f"{base}/offres/kraken/parrainage-kraken/annonces/adrien-b-8",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        await _bumper().human_sleep(1, 2)
        edit = await page.evaluate(
            """
            () => {
              const a = Array.from(document.querySelectorAll('a[href]')).find(x => {
                const t = (x.innerText||'').toLowerCase();
                const h = x.href || '';
                return t.includes('modifier') || t.includes('éditer') || t.includes('editer')
                  || h.includes('edit') || h.includes('modifier');
              });
              return a ? a.href : null;
            }
            """
        )
        if edit:
            return edit
    except Exception:
        pass

    if found:
        # last resort: first edit-like link
        return found[0]
    raise RuntimeError(
        "URL d'edition Kraken introuvable sur le tableau de bord Super-Parrain"
    )


async def _fill_announcement_body(page, text: str) -> str:
    """Remplit le corps d'annonce sans cliquer de bump. Retourne le selecteur utilise."""
    # Prefer textarea with current long content or name message/description
    info = await page.evaluate(
        """
        () => {
          const areas = Array.from(document.querySelectorAll('textarea'));
          return areas.map((t, i) => ({
            i,
            name: t.name || '',
            id: t.id || '',
            len: (t.value || t.innerText || '').length,
            preview: (t.value || '').slice(0, 80),
          }));
        }
        """
    )
    if not info:
        # contenteditable
        ce = page.locator('[contenteditable="true"]').first
        if await ce.count() > 0:
            await ce.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.type(text, delay=5)
            return "contenteditable"
        raise RuntimeError("Aucun textarea/contenteditable sur la page d'edition")

    # Choose longest textarea (likely announcement body)
    best = max(info, key=lambda x: x["len"])
    # Prefer names containing message/description/contenu/texte
    for cand in info:
        n = (cand.get("name") or cand.get("id") or "").lower()
        if any(k in n for k in ("message", "description", "contenu", "texte", "annonce", "body")):
            best = cand
            break

    idx = best["i"]
    loc = page.locator("textarea").nth(idx)
    await loc.wait_for(state="visible", timeout=15000)
    await loc.scroll_into_view_if_needed()
    await loc.click()
    await _bumper().human_sleep(0.2, 0.4)
    await loc.fill(text)
    # Verify fill
    val = await loc.input_value()
    if val != text:
        # try evaluate set
        await page.evaluate(
            """
            ({idx, text}) => {
              const t = document.querySelectorAll('textarea')[idx];
              if (!t) return;
              t.value = text;
              t.dispatchEvent(new Event('input', {bubbles: true}));
              t.dispatchEvent(new Event('change', {bubbles: true}));
            }
            """,
            {"idx": idx, "text": text},
        )
        val = await loc.input_value()
    if val != text:
        raise RuntimeError(
            f"Echec remplissage textarea (len got={len(val)} expected={len(text)})"
        )
    return f"textarea[{idx}] name={best.get('name')}"


async def _click_save_not_boost(page) -> None:
    """Clique Enregistrer/Sauvegarder — jamais Remonter/Boost/Actualiser."""
    # Refuse if only boost buttons
    btn = page.locator(
        'button:has-text("Enregistrer"), input[type="submit"][value*="Enregistrer" i], '
        'button:has-text("Sauvegarder"), button:has-text("Mettre à jour"), '
        'button:has-text("Modifier"), input[type="submit"]'
    )
    count = await btn.count()
    if count == 0:
        raise RuntimeError("Bouton Enregistrer introuvable")

    # Pick first safe button
    chosen = None
    for i in range(count):
        b = btn.nth(i)
        label = ((await b.inner_text()) or (await b.get_attribute("value") or "")).lower()
        if any(x in label for x in ("remont", "boost", "actualis", "supprim", "delete")):
            continue
        if any(x in label for x in ("enregistr", "sauvegard", "mettre à jour", "modifier", "save", "update")) or label.strip() == "":
            chosen = b
            break
    if chosen is None:
        # fallback first submit if not boost-looking
        chosen = btn.first
        label = ((await chosen.inner_text()) or "").lower()
        if any(x in label for x in ("remont", "boost", "actualis")):
            raise RuntimeError(f"Refus de cliquer un bouton bump: {label!r}")

    await _bumper().human_click(page, chosen)
    await page.wait_for_load_state("networkidle")
    await _bumper().human_sleep(2, 3)


def _reread_public(url: str) -> str:
    html = fetch_text(url)
    text = csp.extract_message(html)
    if not text:
        raise RuntimeError(f"Relecture publique: message introuvable sur {url}")
    return text


async def execute_write(plan: WritePlan, *, dry_run: bool = True) -> WriteResult:
    steps: list[str] = []
    if not plan.structure_preserved:
        return WriteResult(
            ok=False,
            plan=plan,
            error="ABORT: structure non preserve — le rendu ne change pas seulement les champs mutables",
            steps=steps,
        )
    if not plan.changed_fields:
        return WriteResult(
            ok=True,
            plan=plan,
            error=None,
            steps=["noop: deja en sync"],
            post_match=True,
            post_publish_text=plan.historical,
        )

    if dry_run:
        return WriteResult(
            ok=True,
            plan=plan,
            steps=["dry-run only — aucune publication"],
            post_match=None,
        )

    bumper_mod = _bumper()
    cfg = bumper_mod.CONFIG["super"]
    if not cfg.get("email") or not cfg.get("password"):
        return WriteResult(
            ok=False,
            plan=plan,
            error="SUPER_PARRAIN_EMAIL/PASSWORD manquants dans l'environnement",
            steps=steps,
        )

    from playwright.async_api import async_playwright

    edit_url = None
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
        ctx = await bumper_mod.new_context(browser)
        page = await ctx.new_page()
        try:
            steps.append("login")
            await _login_super(page, cfg)
            steps.append("find_edit_url")
            edit_url = await _find_kraken_edit_url(page, cfg["url"], plan.program)
            steps.append(f"edit_url={edit_url}")
            await page.goto(edit_url, wait_until="networkidle", timeout=60000)
            await bumper_mod.human_sleep(1.5, 2.5)

            # Screenshot debug without secrets
            try:
                await page.screenshot(path="debug_super_write_before.png", full_page=True)
            except Exception:
                pass

            steps.append("fill_body")
            sel = await _fill_announcement_body(page, plan.rendered)
            steps.append(f"filled_via={sel}")

            steps.append("save")
            await _click_save_not_boost(page)
            try:
                await page.screenshot(path="debug_super_write_after.png", full_page=True)
            except Exception:
                pass
            steps.append("saved")
        except Exception as exc:
            return WriteResult(
                ok=False,
                plan=plan,
                edit_url=edit_url,
                error=str(exc),
                steps=steps,
            )
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    # Relecture publique immediate
    steps.append("reread_public")
    try:
        await asyncio.sleep(2)
        published = _reread_public(plan.announcement_url)
    except Exception as exc:
        return WriteResult(
            ok=False,
            plan=plan,
            edit_url=edit_url,
            error=f"relecture publique echouee: {exc}",
            steps=steps,
        )

    match = published == plan.rendered
    steps.append(f"post_match={match}")
    if not match:
        # Detailed mismatch report
        return WriteResult(
            ok=False,
            plan=plan,
            edit_url=edit_url,
            post_publish_text=published,
            post_match=False,
            error="POST-UPDATE MISMATCH: texte public != rendu attendu — STOP propagation",
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
