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
from lib.content_plan import (
    REASON_DISAGREEMENT,
    classify_disagreement,
    load_plan as load_content_plan,
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


def _notify_disagreement(program: str, blocked_fields: list[str]) -> None:
    """BEST_EFFORT: a refused content mutation must still be observable."""
    try:
        from lib.notify import emit

        emit(
            "ERROR",
            "workflow_error",
            platform="super-parrain",
            program=program,
            action="content_prefill",
            result="FAIL_CLOSED",
            block_reason=REASON_DISAGREEMENT,
            new_value=",".join(blocked_fields) or None,
            source="platforms.super_parrain.prefill",
        )
    except Exception:
        pass


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

    # Prefill contenu seulement si live writes actives pour Super-Parrain
    # (VALIDATION_LIVE + enabled/write_verified). Sinon bump seul.
    if not live_writes_enabled("super-parrain"):
        result["skipped"] = True
        result["reason"] = f"live_writes_off_for_platform ({phase_name()})"
        result["policy"] = "content_writes_disabled"
        log.info(
            f"  Autofresh [{program}]: content writes OFF for super-parrain "
            f"(phase={phase_name()}) — Enregistrer = remontee seule"
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

    # --- Single source of truth: the pre-check plan is the authority ---------
    #
    # Incident 2026-08-27 (GH run 33098049116): the pre-check reported
    # canary_need_update_count=0 and this function still filled the body and
    # triggered a real Save. The two disagreed because they read `current.body`
    # from different places -- the repository golden vs this live edit form.
    #
    # The runtime may narrow the plan (it just did, above, when needs_update is
    # false) but it may never widen it. A program the pre-check did not
    # authorize gets bump-only, whatever this form says.
    plan = load_content_plan()
    verdict = classify_disagreement(
        program=program, plan=plan, runtime_needs_update=diff.needs_update
    )
    result["content_plan"] = {
        "precheck_allowed": verdict["precheck_allowed"],
        "disagreement": verdict["disagreement"],
    }
    if not verdict["content_mutation_allowed"]:
        result["skipped"] = True
        result["needs_update"] = False
        result["fields_filled"] = []
        result["reason"] = verdict["reason"]
        result["blocked_changed_fields"] = sorted(diff.changed_fields.keys())
        if verdict["disagreement"]:
            log.error(
                "  Autofresh [%s]: PRECHECK/RUNTIME DISAGREEMENT on %s — "
                "FAIL CLOSED, no content written, bump only",
                program, sorted(diff.changed_fields.keys()),
            )
            _notify_disagreement(program, sorted(diff.changed_fields.keys()))
        else:
            log.info(
                "  Autofresh [%s]: %s — Enregistrer = remontee seule",
                program, verdict["reason"],
            )
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
