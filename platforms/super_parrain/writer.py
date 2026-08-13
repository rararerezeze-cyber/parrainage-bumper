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
from lib.super_parrain_resource import (
    HARD_STOP_WRONG_RESOURCE,
    assert_announcement_edit_url,
    assert_not_codes_promo_form,
    classify_edit_url,
)
from lib.template_builder import extract_values_via_template, structure_preserved_via_markers
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


def build_write_plan(
    platform: str = "super-parrain",
    program: str = "kraken",
    language: str = "fr",
    *,
    overrides: dict[str, str | None] | None = None,
    only_fields: list[str] | None = None,
) -> WritePlan:
    mapping = MappingRepository().load(platform, program, language)
    templates = TemplateRepository()
    renderer = Renderer(OffersRepository())
    template = templates.load_text(platform, program, language)
    historical = templates.load_golden(platform, program, language)
    offer = renderer.offers.get_by_slug(program)

    hist_vals = dict(mapping.platform_values or {})
    extracted = extract_values_via_template(
        template, historical, mapping.mutable_fields, mapping.markers
    )
    for k, v in extracted.items():
        hist_vals.setdefault(k, v)

    lock: dict[str, str | None] = dict(overrides or {})
    if only_fields:
        allowed = {f.strip() for f in only_fields if f and f.strip()}
        for field in mapping.mutable_fields:
            if field not in allowed and field in hist_vals:
                lock[field] = hist_vals[field]

    variables = renderer.build_variables(mapping, offer=offer, overrides=lock or None)
    rendered = renderer.render(template, mapping, offer=offer, overrides=lock or None)

    changed: dict[str, dict[str, str | None]] = {}
    for field in mapping.mutable_fields:
        old = hist_vals.get(field)
        new = variables.get(field)
        if old != new:
            changed[field] = {"old": old, "new": new}

    structure_preserved = structure_preserved_via_markers(
        template,
        historical,
        rendered,
        mapping.mutable_fields,
        mapping.markers,
        hist_vals,
        variables,
    )

    url = mapping.announcement_url or (
        f"https://www.super-parrain.com/offres/{program}/parrainage-{program}/annonces/adrien-b-8"
    )
    return WritePlan(
        platform=platform,
        program=program,
        language=language,
        announcement_url=url,
        edit_url=mapping.edit_url,
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
        f"EDIT: {plan.edit_url}",
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


def _page_blocked_reason(body: str, url: str = "") -> str | None:
    blob = f"{body or ''} {url or ''}".lower()
    if "captcha" in blob or "recaptcha" in blob or "hcaptcha" in blob or "cloudflare" in blob and "challenge" in blob:
        return "CAPTCHA_OR_CHALLENGE"
    if "403" in blob and ("forbidden" in blob or "accès refusé" in blob or "access denied" in blob):
        return "HTTP_403"
    if "429" in blob or "too many requests" in blob or "trop de requêtes" in blob:
        return "HTTP_429"
    return None


async def _find_program_edit_url(
    page,
    base: str,
    program: str,
    announcement_url: str | None = None,
    preferred_edit_url: str | None = None,
) -> str:
    """Mes annonces edit only. codes-promo is a hard reject."""
    bumper_mod = _bumper()
    needle = (program or "").strip().lower()
    if not needle:
        raise RuntimeError("program manquant pour trouver l'URL d'edition")

    if preferred_edit_url:
        assert_announcement_edit_url(preferred_edit_url)
        return preferred_edit_url

    candidates_pages = [
        f"{base}/tableau-de-bord/annonces",
        f"{base}/tableau-de-bord/mes-annonces",
        f"{base}/tableau-de-bord/parrainages",
    ]
    ranked: list[tuple[int, str]] = []
    for url in candidates_pages:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await bumper_mod.human_sleep(1.2, 2.0)
            await _dismiss_blocking_modals(page)
        except Exception:
            continue
        blocked = _page_blocked_reason(await page.inner_text("body"), page.url)
        if blocked:
            raise RuntimeError(f"STOP {blocked} on {page.url}")
        hrefs = await page.evaluate(
            """
            (needle) => {
              const out = [];
              for (const a of document.querySelectorAll('a[href]')) {
                const href = a.href || '';
                const label = ((a.innerText || a.textContent || '') + ' ' + href).toLowerCase();
                if (!href) continue;
                if (href.includes('boost') || href.includes('supprim') || href.includes('delete')) continue;
                if (href.includes('/codes-promo/')) continue;
                const isEdit = href.includes('/edit') || href.includes('modifier')
                  || label.includes('modifier') || label.includes('éditer') || label.includes('editer');
                if (!isEdit) continue;
                const isTarget = label.includes(needle) || href.includes(needle);
                out.push({href, isTarget});
              }
              return out;
            }
            """,
            needle,
        )
        for h in hrefs:
            href = h.get("href") or ""
            if not h.get("isTarget"):
                continue
            if classify_edit_url(href) == "CODES_PROMO" or "/codes-promo/" in href.lower():
                raise RuntimeError(f"{HARD_STOP_WRONG_RESOURCE}: codes-promo url={href}")
            score = 5
            if "annonce" in href:
                score += 10
            ranked.append((score, href))

    public = announcement_url or f"{base}/offres/{needle}/parrainage-{needle}/annonces/adrien-b-8"
    try:
        await page.goto(public, wait_until="domcontentloaded", timeout=45000)
        await bumper_mod.human_sleep(1, 2)
        blocked = _page_blocked_reason(await page.inner_text("body"), page.url)
        if blocked:
            raise RuntimeError(f"STOP {blocked} on {page.url}")
        edit = await page.evaluate(
            """
            () => {
              const a = Array.from(document.querySelectorAll('a[href]')).find(x => {
                const t = (x.innerText||'').toLowerCase();
                const h = x.href || '';
                if ((h || '').includes('/codes-promo/')) return false;
                return t.includes('modifier') || t.includes('éditer') || t.includes('editer')
                  || h.includes('edit') || h.includes('modifier');
              });
              return a ? a.href : null;
            }
            """
        )
        if edit:
            if "/codes-promo/" in edit.lower():
                raise RuntimeError(f"{HARD_STOP_WRONG_RESOURCE}: codes-promo url={edit}")
            ranked.append((20, edit))
    except RuntimeError:
        raise
    except Exception:
        pass

    if not ranked:
        raise RuntimeError(
            f"URL d'edition Mes annonces introuvable pour {program}. "
            f"{HARD_STOP_WRONG_RESOURCE}: refusing codes-promo fallback"
        )
    ranked.sort(key=lambda x: -x[0])
    chosen = ranked[0][1]
    return assert_announcement_edit_url(chosen)


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
    """Remplit le corps d'annonce Mes annonces. Refuse codes-promo avant tout fill."""
    await _dismiss_blocking_modals(page)
    debug = await _dump_form_debug(page, "debug_super_write_form.json")
    form_names = [str((i or {}).get("name") or "") for i in (debug.get("inputs") or [])]
    form_names.append(str(debug.get("url") or page.url or ""))
    assert_not_codes_promo_form(url=page.url, form_names=form_names, html=debug.get("bodyPreview"))
    assert_announcement_edit_url(page.url)

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
            if "edit_code_promo_by_user_form" in n or "condition" in n:
                continue
            if any(k in n for k in ("message", "contenu", "texte", "annonce", "body", "presentation")):
                best = cand
                break
        best_name = (best.get("name") or best.get("id") or "").lower()
        if "edit_code_promo_by_user_form" in best_name:
            raise RuntimeError(
                f"{HARD_STOP_WRONG_RESOURCE}: refusing textarea {best.get('name')}"
            )
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

    preview = (debug.get("bodyPreview") or "")[:300].replace("\n", " ")
    raise RuntimeError(
        f"{HARD_STOP_WRONG_RESOURCE}: no Mes annonces body field "
        f"(refusing codes-promo / discrete fallback). "
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


async def execute_write(
    plan: WritePlan, *, dry_run: bool = True, inspect_only: bool = False
) -> WriteResult:
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

    if dry_run and not inspect_only:
        return WriteResult(
            ok=True,
            plan=plan,
            steps=["dry-run only — aucune publication"],
            post_match=None,
        )
    if not inspect_only and not content_write_allowed("super-parrain"):
        return WriteResult(
            ok=True,
            plan=plan,
            steps=[f"LIVE_DISABLED ({phase_name()}) — need CANARY_READY/WRITE_VERIFIED"],
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
    account_text: str | None = None
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
            edit_url = await _find_program_edit_url(
                page,
                cfg["url"],
                plan.program,
                plan.announcement_url,
                preferred_edit_url=plan.edit_url,
            )
            steps.append(f"edit_url={edit_url}")
            assert_announcement_edit_url(edit_url)
            if plan.program.lower() not in (edit_url or "").lower() and plan.program.lower() not in (
                plan.announcement_url or ""
            ).lower():
                return WriteResult(
                    ok=False,
                    plan=plan,
                    edit_url=edit_url,
                    error=f"STOP unexpected edit URL (not {plan.program}): {edit_url}",
                    steps=steps,
                )
            await page.goto(edit_url, wait_until="networkidle", timeout=60000)
            await bumper_mod.human_sleep(1.5, 2.5)
            blocked = _page_blocked_reason(await page.inner_text("body"), page.url)
            if blocked:
                return WriteResult(
                    ok=False,
                    plan=plan,
                    edit_url=edit_url,
                    error=f"STOP {blocked} on edit page {page.url}",
                    steps=steps,
                )

            # Screenshot debug without secrets
            try:
                await page.screenshot(path="debug_super_write_before.png", full_page=True)
            except Exception:
                pass

            # Detect 24h remontee lock before filling
            dismiss_notes = await _dismiss_blocking_modals(page)
            steps.extend(dismiss_notes)
            body_now = await page.inner_text("body")
            body_low = body_now.lower()
            if "moins de 24" in body_low or (
                "24h" in body_low and "réessayer" in body_low
            ) or ("24h" in body_low and "reessayer" in body_low):
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

                                    form_dump = await _dump_form_debug(page, "debug_super_write_form.json")
            form_names = [
                str((i or {}).get("name") or "")
                for i in (form_dump.get("inputs") or [])
            ]
            assert_not_codes_promo_form(
                url=page.url,
                form_names=form_names,
                html=form_dump.get("bodyPreview"),
            )

            if plan.program == "poulpeo":
                from lib.super_parrain_resource import poulpeo_pre_save_assertions

                message_loc = page.locator('textarea[name="form[message]"]')
                if await message_loc.count() != 1:
                    return WriteResult(
                        ok=False,
                        plan=plan,
                        edit_url=edit_url,
                        error=f"{HARD_STOP_WRONG_RESOURCE}: expected exactly one form[message]",
                        steps=steps,
                    )

                current_message = await message_loc.input_value()

                chk = poulpeo_pre_save_assertions(
                    page_url=page.url,
                    page_text=current_message,
                    public_listing=plan.announcement_url,
                    rendered=plan.rendered,
                    historical=plan.historical,
                )
                steps.append(f"poulpeo_pre_save={chk}")

                if not chk["ok"]:
                    return WriteResult(
                        ok=False,
                        plan=plan,
                        edit_url=edit_url,
                        error=f"STOP pre-save assertions failed: {chk['errors']}",
                        steps=steps,
                    )

            if inspect_only:
                steps.append("inspect_only — no fill, no Enregistrer")
                return WriteResult(
                    ok=True,
                    plan=plan,
                    edit_url=edit_url,
                    account_reread_text=body_now,
                    steps=steps,
                    post_match=None,
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

            steps.append("reread_account")
            try:
                await page.goto(edit_url, wait_until="networkidle", timeout=60000)
                account_text = await page.evaluate(
                    """
                    () => {
                      const t = document.querySelector('textarea');
                      if (t && t.value) return t.value;
                      const ce = document.querySelector('[contenteditable="true"], .ql-editor, .ProseMirror, .note-editable');
                      if (ce) return ce.innerText || '';
                      return '';
                    }
                    """
                )
            except Exception as exc:
                return WriteResult(
                    ok=False,
                    plan=plan,
                    edit_url=edit_url,
                    error=f"relecture compte echouee: {exc}",
                    steps=steps,
                )
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
            account_reread_text=account_text,
            error=f"relecture publique echouee: {exc}",
            steps=steps,
        )

    match = published == plan.rendered
    steps.append(f"post_match={match}")
    if not match:
        return WriteResult(
            ok=False,
            plan=plan,
            edit_url=edit_url,
            post_publish_text=published,
            account_reread_text=account_text,
            post_match=False,
            error="POST-UPDATE MISMATCH: texte public != rendu attendu — STOP propagation",
            steps=steps,
        )

    return WriteResult(
        ok=True,
        plan=plan,
        edit_url=edit_url,
        post_publish_text=published,
        account_reread_text=account_text,
        post_match=True,
        steps=steps,
    )
