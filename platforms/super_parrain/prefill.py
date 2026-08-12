"""Prefill Autofresh sur une page d'edition Super-Parrain deja ouverte par bumper.

Appelé juste avant le clic Enregistrer historique.
Une seule sauvegarde = update eventuel + remontee.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from lib.super_parrain_content import (
    ContentDiff,
    compare_current_to_desired,
    get_desired_content,
    program_from_edit_url,
)
from lib.phase import live_writes_enabled, phase_name
from lib.super_parrain_policy import should_prefill_content

log = logging.getLogger("super_parrain.prefill")


async def _read_form_values(page) -> dict[str, str | None]:
    data = await page.evaluate(
        """
        () => {
          const out = {code: null, link: null, title: null, body: null, reward: null};
          const inputs = Array.from(document.querySelectorAll('input, textarea'));
          for (const el of inputs) {
            const name = ((el.name || '') + ' ' + (el.id || '') + ' ' + (el.placeholder || '')).toLowerCase();
            const val = (el.value != null ? el.value : el.innerText || '').trim();
            if (!val) continue;
            if (!out.code && /code/.test(name) && !/postal|zip/.test(name)) out.code = val;
            if (!out.link && /(lien|link|url|invite)/.test(name)) out.link = val;
            if (!out.title && /(titre|title|libelle|name)/.test(name)) out.title = val;
            if (el.tagName === 'TEXTAREA' && val.length > (out.body||'').length) out.body = val;
          }
          // contenteditable
          const ce = document.querySelector('[contenteditable="true"], .ql-editor, .ProseMirror');
          if (ce) {
            const t = (ce.innerText || '').trim();
            if (t.length > (out.body||'').length) out.body = t;
          }
          return out;
        }
        """
    )
    return data or {}


async def _set_input(page, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.count() == 0:
                continue
            if not await loc.is_visible():
                continue
            await loc.fill(value)
            return True
        except Exception:
            continue
    return False


async def _set_body(page, value: str) -> bool:
    # textarea longest
    n = await page.locator("textarea").count()
    if n:
        best_i, best_len = 0, -1
        for i in range(n):
            try:
                v = await page.locator("textarea").nth(i).input_value()
            except Exception:
                v = ""
            if len(v) > best_len:
                best_len = len(v)
                best_i = i
        try:
            await page.locator("textarea").nth(best_i).fill(value)
            return True
        except Exception:
            pass
    ce = page.locator('[contenteditable="true"], .ql-editor, .ProseMirror').first
    if await ce.count():
        try:
            await page.evaluate(
                """
                (text) => {
                  const el = document.querySelector('[contenteditable="true"], .ql-editor, .ProseMirror');
                  if (!el) return false;
                  el.focus();
                  el.innerText = text;
                  el.dispatchEvent(new Event('input', {bubbles: true}));
                  return true;
                }
                """,
                value,
            )
            return True
        except Exception:
            return False
    return False


async def prepare_before_save(page, edit_url: str) -> dict[str, Any]:
    """PRE-CHECK + prefill eventuel. Ne clique PAS Enregistrer.

    Respecte la politique canary: hors liste canary → bump seul, aucun contenu injecte.
    Returns stats dict for cycle report.
    """
    program = program_from_edit_url(edit_url)
    result: dict[str, Any] = {
        "edit_url": edit_url,
        "program": program,
        "needs_update": False,
        "fields_filled": [],
        "changed_fields": {},
        "skipped": False,
        "reason": "",
        "policy": "",
    }
    if not program:
        result["skipped"] = True
        result["reason"] = "program_unknown_from_url"
        log.info("  Autofresh: programme inconnu pour URL — bump seul")
        return result

    # Phase BASE: jamais de prefill contenu (canary reporte jusqu'a BASE_READY_ALL)
    if not live_writes_enabled():
        result["skipped"] = True
        result["reason"] = f"base_phase_no_live_writes ({phase_name()})"
        result["policy"] = "base_phase"
        log.info(
            f"  Autofresh [{program}]: phase BASE — prefill contenu OFF, "
            "Enregistrer = remontee seule (canary deferred)"
        )
        return result

    # Canary / rollout gate — avant tout calcul de contenu
    allow, policy_reason = should_prefill_content(program)
    result["policy"] = policy_reason
    if not allow:
        result["skipped"] = True
        result["reason"] = policy_reason
        log.info(f"  Autofresh [{program}]: {policy_reason} — Enregistrer = remontee seule")
        return result

    desired = get_desired_content(program)
    if not desired.has_mapping or desired.error:
        result["skipped"] = True
        result["reason"] = desired.error or "no_mapping"
        log.info(f"  Autofresh [{program}]: pas de mapping — bump seul")
        return result

    if not desired.structure_preserved:
        result["skipped"] = True
        result["reason"] = "structure_not_preserved"
        log.warning(f"  Autofresh [{program}]: structure non preservee — bump seul, pas d'update")
        return result

    current = await _read_form_values(page)
    # If form body empty, compare key discrete fields + use mapping platform values
    if not current.get("code") and desired.variables:
        # still try fill from desired vs empty form using golden-based platform values
        from lib.renderer import MappingRepository

        try:
            mapping = MappingRepository().load("super-parrain", program, "fr")
            pv = mapping.platform_values or {}
            current.setdefault("code", pv.get("personal_code"))
            current.setdefault("link", pv.get("personal_link"))
            current.setdefault("reward", pv.get("referee_reward"))
        except Exception:
            pass

    diff: ContentDiff = compare_current_to_desired(program, current, desired)
    # Always compute field-level desired vs platform_values if form didn't expose body
    if not diff.needs_update and desired.variables:
        # Check platform_values drift even if form values missing
        try:
            from lib.renderer import MappingRepository

            mapping = MappingRepository().load("super-parrain", program, "fr")
            pv = mapping.platform_values or {}
            for logical, key in (
                ("personal_code", "code"),
                ("personal_link", "link"),
                ("referee_reward", "reward"),
            ):
                if logical not in desired.mutable_fields:
                    continue
                old = pv.get(logical)
                new = desired.variables.get(logical)
                if old and new and str(old) != str(new):
                    diff.changed_fields[logical] = {"old": old, "new": new}
                    diff.needs_update = True
                    diff.reason = "diff"
        except Exception:
            pass

    result["changed_fields"] = diff.changed_fields
    result["needs_update"] = diff.needs_update

    if not diff.needs_update:
        result["reason"] = "in_sync"
        log.info(f"  Autofresh [{program}]: in_sync — Enregistrer = remontee seule")
        return result

    filled = []
    # Apply only mapped/changed fields
    if "personal_code" in diff.changed_fields and desired.code:
        if await _set_input(
            page,
            [
                'input[name*="code" i]',
                'input[id*="code" i]',
                'input[placeholder*="code" i]',
            ],
            desired.code,
        ):
            filled.append("code")

    if "personal_link" in diff.changed_fields and desired.link:
        if await _set_input(
            page,
            [
                'input[name*="lien" i]',
                'input[name*="link" i]',
                'input[name*="url" i]',
                'input[id*="lien" i]',
                'input[id*="link" i]',
            ],
            desired.link,
        ):
            filled.append("link")

    if "title" in diff.changed_fields and desired.title:
        if await _set_input(
            page,
            [
                'input[name*="titre" i]',
                'input[name*="title" i]',
                'input[id*="titre" i]',
                'input[id*="title" i]',
            ],
            desired.title,
        ):
            filled.append("title")

    # Body / multi-section: one rendered body updates all markers (reward in title+body etc.)
    if desired.rendered_body and (
        "body" in diff.changed_fields
        or "referee_reward" in diff.changed_fields
        or "conditions" in diff.changed_fields
        or "personal_code" in diff.changed_fields
        or "personal_link" in diff.changed_fields
    ):
        # Only overwrite body if a substantial textarea/contenteditable exists
        form = await _read_form_values(page)
        if form.get("body") and len(form["body"]) > 80:
            if await _set_body(page, desired.rendered_body):
                filled.append("body")
        elif not form.get("body"):
            # try set body anyway if textarea appears empty but present
            n = await page.locator("textarea").count()
            if n and await _set_body(page, desired.rendered_body):
                filled.append("body")

    result["fields_filled"] = filled
    if not filled:
        result["reason"] = "diff_but_no_editable_fields"
        log.warning(
            f"  Autofresh [{program}]: diff detecte mais aucun champ editable "
            f"— Enregistrer = remontee seule ({list(diff.changed_fields)})"
        )
    else:
        result["reason"] = "prefilled"
        log.info(
            f"  Autofresh [{program}]: prefill {filled} "
            f"changes={list(diff.changed_fields)} — 1x Enregistrer = update+remonte"
        )
    return result
