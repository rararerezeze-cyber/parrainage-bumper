"""Ecriture contenu Super-Parrain (annonce) — separe du bump bumper.py."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
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
    """Trouve l'URL d'edition du contenu Kraken (prefere annonces > codes-promo)."""
    bumper_mod = _bumper()
    # Prefer content-oriented dashboards first (codes-promo edit = often remontee/bump only)
    candidates_pages = [
        f"{base}/tableau-de-bord/annonces",
        f"{base}/tableau-de-bord/mes-annonces",
        f"{base}/tableau-de-bord/parrainages",
        f"{base}/tableau-de-bord",
        f"{base}/tableau-de-bord/codes-promo",
    ]
    ranked: list[tuple[int, str]] = []
    for url in candidates_pages:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await bumper_mod.human_sleep(1.2, 2.0)
            await _dismiss_blocking_modals(page)
        except Exception:
            continue
        hrefs = await page.evaluate(
            """
            () => {
              const out = [];
              for (const a of document.querySelectorAll('a[href]')) {
                const href = a.href || '';
                const label = ((a.innerText || a.textContent || '') + ' ' + href).toLowerCase();
                if (!href) continue;
                if (href.includes('boost') || href.includes('supprim') || href.includes('delete')) continue;
                const isEdit = href.includes('/edit') || href.includes('modifier')
                  || label.includes('modifier') || label.includes('éditer') || label.includes('editer');
                if (!isEdit) continue;
                const isKraken = label.includes('kraken') || href.includes('kraken');
                out.push({href, isKraken, isCodesPromo: href.includes('codes-promo')});
              }
              return out;
            }
            """
        )
        for h in hrefs:
            if not h.get("isKraken"):
                continue
            # Prefer non-codes-promo edit URLs (likely content)
            score = 0
            if not h.get("isCodesPromo"):
                score += 10
            if "annonce" in h["href"]:
                score += 5
            ranked.append((score, h["href"]))

        pair = await page.evaluate(
            """
            () => {
              const rows = Array.from(document.querySelectorAll('tr, .card, li, article, .list-item, .row'));
              for (const row of rows) {
                const t = (row.innerText || '').toLowerCase();
                if (!t.includes('kraken')) continue;
                const a = row.querySelector('a[href*="edit"], a[href*="modifier"]');
                if (a && a.href) return a.href;
              }
              return null;
            }
            """
        )
        if pair:
            score = 10 if "codes-promo" not in pair else 1
            ranked.append((score, pair))

    if ranked:
        ranked.sort(key=lambda x: -x[0])
        return ranked[0][1]

    # Public page owner edit
    try:
        await page.goto(
            f"{base}/offres/kraken/parrainage-kraken/annonces/adrien-b-8",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        await bumper_mod.human_sleep(1, 2)
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

    raise RuntimeError(
        "URL d'edition Kraken introuvable (contenu). "
        "Le lien codes-promo/edit est le flux remontee et peut etre bloque 24h."
    )


async def _dismiss_blocking_modals(page) -> list[str]:
    notes: list[str] = []
    # Modal 24h remontee
    body = ""
    try:
        body = await page.inner_text("body")
    except Exception:
        pass
    if "moins de 24" in body.lower() or "24h" in body.lower() and "réessayer" in body.lower():
        notes.append("modal_24h_detected")
        for sel in (
            'button:has-text("OK")',
            'button:has-text("Fermer")',
            'button:has-text("Close")',
            '[aria-label="Close"]',
            ".modal .close",
            "button.close",
            ".modal button",
        ):
            loc = page.locator(sel).first
            try:
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=2000)
                    notes.append(f"dismissed:{sel}")
                    await _bumper().human_sleep(0.5, 1.0)
                    break
            except Exception:
                continue
    return notes


async def _dump_form_debug(page, path: str) -> dict[str, Any]:
    data = await page.evaluate(
        """
        () => {
          const inputs = Array.from(document.querySelectorAll('input, textarea, select, [contenteditable="true"]'))
            .map(el => ({
              tag: el.tagName,
              type: el.type || '',
              name: el.name || '',
              id: el.id || '',
              role: el.getAttribute('role') || '',
              contenteditable: el.getAttribute('contenteditable') || '',
              className: (el.className || '').toString().slice(0, 120),
              valueLen: ((el.value != null ? el.value : el.innerText) || '').length,
              preview: ((el.value != null ? el.value : el.innerText) || '').slice(0, 100),
            }));
          const iframes = Array.from(document.querySelectorAll('iframe')).map(f => ({
            src: f.src || '', id: f.id || '', className: (f.className||'').toString().slice(0,80)
          }));
          return {url: location.href, title: document.title, inputs, iframes, bodyPreview: document.body.innerText.slice(0, 1500)};
        }
        """
    )
    try:
        Path(path).write_text(
            __import__("json").dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    return data


async def _fill_announcement_body(page, text: str, *, code: str | None = None, link: str | None = None) -> str:
    """Remplit le corps d'annonce. Gere textarea, contenteditable, editeurs iframe, champs code/lien."""
    await _dismiss_blocking_modals(page)
    debug = await _dump_form_debug(page, "debug_super_write_form.json")

    # 1) textareas classiques
    info = await page.evaluate(
        """
        () => Array.from(document.querySelectorAll('textarea')).map((t, i) => ({
            i, name: t.name || '', id: t.id || '',
            len: (t.value || '').length,
            preview: (t.value || '').slice(0, 80),
          }))
        """
    )
    if info:
        best = max(info, key=lambda x: x["len"])
        for cand in info:
            n = (cand.get("name") or cand.get("id") or "").lower()
            if any(k in n for k in ("message", "description", "contenu", "texte", "annonce", "body", "content")):
                best = cand
                break
        idx = best["i"]
        loc = page.locator("textarea").nth(idx)
        await loc.wait_for(state="visible", timeout=15000)
        await loc.click()
        await _bumper().human_sleep(0.2, 0.4)
        await loc.fill(text)
        val = await loc.input_value()
        if val != text:
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
        if val == text:
            return f"textarea[{idx}] name={best.get('name')}"

    # 2) contenteditable / TinyMCE-like
    ce = page.locator('[contenteditable="true"], .ql-editor, .ProseMirror, .note-editable').first
    if await ce.count() > 0:
        await ce.click()
        await page.keyboard.press("Control+A")
        # Prefer insert via evaluate for exact whitespace
        await page.evaluate(
            """
            (text) => {
              const el = document.querySelector('[contenteditable="true"], .ql-editor, .ProseMirror, .note-editable');
              if (!el) return;
              el.focus();
              el.innerText = text;
              el.dispatchEvent(new Event('input', {bubbles: true}));
            }
            """,
            text,
        )
        return "contenteditable"

    # 3) iframe editors (tinymce/ckeditor)
    for frame in page.frames:
        try:
            body = frame.locator("body[contenteditable='true'], body.mce-content-body, .cke_editable")
            if await body.count() > 0:
                await body.first.evaluate(
                    """(el, text) => { el.innerText = text; el.dispatchEvent(new Event('input', {bubbles:true})); }""",
                    text,
                )
                return f"iframe_editor:{frame.url[:60]}"
        except Exception:
            continue

    # 4) Fallback: discrete code/title/link inputs (Super-Parrain codes-promo table)
    filled_parts = []
    if code:
        for sel in (
            'input[name*="code" i]',
            'input[id*="code" i]',
            'input[placeholder*="code" i]',
        ):
            loc = page.locator(sel).first
            try:
                if await loc.count() and await loc.is_visible():
                    await loc.fill(code)
                    filled_parts.append(f"code:{sel}")
                    break
            except Exception:
                continue
    if link:
        for sel in (
            'input[name*="lien" i]',
            'input[name*="link" i]',
            'input[name*="url" i]',
            'input[id*="lien" i]',
            'input[id*="link" i]',
        ):
            loc = page.locator(sel).first
            try:
                if await loc.count() and await loc.is_visible():
                    await loc.fill(link)
                    filled_parts.append(f"link:{sel}")
                    break
            except Exception:
                continue

    if filled_parts:
        return "fields:" + ",".join(filled_parts)

    # Helpful error with page context
    preview = (debug.get("bodyPreview") or "")[:300].replace("\n", " ")
    raise RuntimeError(
        "Aucun champ d'edition de texte trouve sur la page. "
        f"url={debug.get('url')} inputs={len(debug.get('inputs') or [])} "
        f"iframes={len(debug.get('iframes') or [])} body={preview!r}"
    )


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
    from lib.phase import content_write_allowed, phase_name

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

    if dry_run or not content_write_allowed("super-parrain"):
        return WriteResult(
            ok=True,
            plan=plan,
            steps=[
                "dry-run only — aucune publication"
                if dry_run
                else f"LIVE_DISABLED ({phase_name()}) — need CANARY_READY/WRITE_VERIFIED"
            ],
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

            # Detect 24h remontee lock before filling
            dismiss_notes = await _dismiss_blocking_modals(page)
            steps.extend(dismiss_notes)
            body_now = (await page.inner_text("body")).lower()
            if "moins de 24" in body_now or (
                "24h" in body_now and "réessayer" in body_now
            ) or ("24h" in body_now and "reessayer" in body_now):
                return WriteResult(
                    ok=False,
                    plan=plan,
                    edit_url=edit_url,
                    error=(
                        "Super-Parrain bloque l'edition/remontee: code promo "
                        "modifie il y a moins de 24h. Impossible d'ecrire maintenant "
                        "sans risquer le flux bump. Reessayer apres le delai 24h."
                    ),
                    steps=steps + ["blocked_24h"],
                )

            steps.append("fill_body")
            sel = await _fill_announcement_body(
                page,
                plan.rendered,
                code=plan.variables.get("personal_code"),
                link=plan.variables.get("personal_link"),
            )
            steps.append(f"filled_via={sel}")

            # If we only filled discrete code/link fields, still try full text path warning
            if sel.startswith("fields:") and "textarea" not in sel:
                steps.append(
                    "warn: only discrete code/link fields found — full announcement body may be separate"
                )

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
