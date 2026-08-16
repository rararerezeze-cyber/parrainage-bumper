#!/usr/bin/env python3
"""READ-ONLY diagnostic — why did the code-parrainage/Kraken canary Save
(GH run 31962858807) never persist a change to textarea#offre?

Explicit operator authorization (2026-08-16), diagnostic-only phase. Real
login, real navigation to the real edit form (/modif/84601) — but NO click
on "Enregistrer les modifications", NO click on "Actualiser", NO submit of
any kind, NO override mutation left behind. The 2 Save clicks already
consumed by the canary run are NOT reused or repeated here.

What this captures, all without ever submitting:
  - full <form> census: action/method/enctype/hidden fields/submit button
    attributes; whether the Save button and textarea#offre share the same
    <form> ancestor.
  - textarea#offre census: value/defaultValue/textContent/maxlength/
    minlength/required/class/data-* attributes; whether any OTHER element
    also matches [name="offre"] or #offre (decorative-vs-real-field split).
  - the "613 / 200" counter element: located by regex on visible text,
    captured with its outerHTML/class for later JS cross-reference.
  - baseline form.checkValidity() / textarea.validity / validationMessage.
  - inline + external <script> text scanned for a fixed keyword list
    (#offre, offre, 200, submit, Enregistrer les modifications, modifpost,
    keyup/keydown/input/change/blur) with short context windows.
  - TEST 1: Playwright .fill() with the exact same canary-rendered text
    the live canary run used, immediate + 1.5s-later re-read of value/
    counter/validity (does anything asynchronously revert the fill?).
  - RELOAD (no save) to leave a clean tab, re-dump the form to prove the
    server-side value is still untouched.
  - TEST 2: the same canary text entered via real keystroke simulation
    (press_sequentially, dispatches keydown/keyup/input per character —
    unlike .fill(), which only dispatches input+change) — same re-read set.
  - RELOAD again (no save) — final proof no accidental submit ever
    happened during this diagnostic session.
  - every background network request observed during the whole session
    (for spotting an autosave/validation XHR the two prior canary Saves
    might have depended on).

No POST from an actual Save click can be observed here (that requires the
click this script deliberately never makes) — this is documented in the
report rather than guessed at.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.http_fetch import fetch_text  # noqa: E402
from platforms.code_parrainage.writer import _dump_form_debug, _login  # noqa: E402
from tools.canary_write_code_parrainage import (  # noqa: E402
    CANARY_VALUE,
    EDIT_URL,
    EXPECTED_CODE_OU_LIEN,
    EXPECTED_COMPANY,
    ORIGINAL_VALUE,
    PLATFORM,
    PROGRAM,
    _canary_and_original_renders,
)

OUT = ROOT / "data" / "captures"
REPORT_PATH = OUT / "diagnose-code-parrainage-offre-save.json"

KEYWORDS = (
    "#offre", "offre", "200", "compteur", "counter", "submit",
    "Enregistrer les modifications", "modifpost", "keyup", "keydown",
    "input", "change", "blur", "checkValidity", "maxlength", "minlength",
)


def _bumper():
    import bumper as bumper_mod

    return bumper_mod


async def _form_and_field_census(page) -> dict:
    return await page.evaluate(
        """
        () => {
          const ta = document.querySelector('textarea#offre');
          const btn = Array.from(document.querySelectorAll('button, input[type="submit"]'))
            .find(b => (b.innerText || b.value || '').includes('Enregistrer les modifications'));
          const form = ta ? ta.closest('form') : null;
          const btnForm = btn ? btn.closest('form') : null;

          const matches = Array.from(document.querySelectorAll('[name="offre"], #offre'))
            .map(el => ({
              tag: el.tagName, id: el.id, name: el.name || '',
              type: el.type || '', visible: !!(el.offsetWidth || el.offsetHeight),
            }));

          let formInfo = null;
          if (form) {
            formInfo = {
              action: form.action || '', method: form.method || '', enctype: form.enctype || '',
              hidden_fields: Array.from(form.querySelectorAll('input[type="hidden"]'))
                .map(i => ({name: i.name, value: i.value})),
              same_form_as_save_button: form === btnForm,
            };
          }

          let taInfo = null;
          if (ta) {
            const cs = getComputedStyle(ta);
            taInfo = {
              value: ta.value, defaultValue: ta.defaultValue, textContent: ta.textContent,
              valueLen: ta.value.length, defaultValueLen: ta.defaultValue.length,
              maxLength: ta.maxLength, minLength: ta.minLength, required: ta.required,
              className: ta.className, dataset: Object.assign({}, ta.dataset),
              display: cs.display, visibility: cs.visibility, readOnly: ta.readOnly,
              disabled: ta.disabled,
            };
          }

          const bodyText = document.body.innerText || '';
          const counterMatch = bodyText.match(/(\\d+)\\s*\\/\\s*(\\d+)/);
          let counterInfo = null;
          if (counterMatch) {
            const el = Array.from(document.querySelectorAll('*')).find(
              e => e.children.length === 0 && (e.innerText || '').trim() === counterMatch[0]
            );
            counterInfo = {
              text: counterMatch[0], left: counterMatch[1], right: counterMatch[2],
              element_outerHTML: el ? el.outerHTML.slice(0, 300) : null,
              element_class: el ? el.className : null,
            };
          }

          const editorGlobals = {
            CKEDITOR: !!window.CKEDITOR, tinymce: !!window.tinymce, Quill: !!window.Quill,
            Vue: !!window.Vue, React: !!window.React, Alpine: !!window.Alpine,
            jQuery: !!window.jQuery,
          };

          return {
            offre_element_matches: matches, form_info: formInfo, textarea_info: taInfo,
            counter_info: counterInfo, editor_globals: editorGlobals,
            save_button_present: !!btn,
          };
        }
        """
    )


async def _validity_snapshot(page) -> dict:
    return await page.evaluate(
        """
        () => {
          const ta = document.querySelector('textarea#offre');
          if (!ta) return null;
          const form = ta.closest('form');
          const v = ta.validity;
          return {
            form_checkValidity: form ? form.checkValidity() : null,
            textarea_checkValidity: ta.checkValidity(),
            validity: {
              valid: v.valid, valueMissing: v.valueMissing, tooShort: v.tooShort,
              tooLong: v.tooLong, patternMismatch: v.patternMismatch,
              customError: v.customError,
            },
            validationMessage: ta.validationMessage,
          };
        }
        """
    )


async def _read_state(page) -> dict:
    census = await _form_and_field_census(page)
    validity = await _validity_snapshot(page)
    return {
        "value_len": (census.get("textarea_info") or {}).get("valueLen"),
        "value_preview": ((census.get("textarea_info") or {}).get("value") or "")[:120],
        "counter_text": (census.get("counter_info") or {}).get("text"),
        "validity": validity,
    }


async def _scan_scripts(page, base_url: str) -> list[dict]:
    srcs = await page.evaluate(
        """
        () => Array.from(document.scripts).map(s => ({src: s.src || '', inline: s.src ? '' : (s.textContent || '').slice(0, 200000)}))
        """
    )
    findings: list[dict] = []
    for i, s in enumerate(srcs or []):
        text = None
        origin = None
        if s.get("src"):
            origin = s["src"]
            try:
                text = fetch_text(s["src"])
            except Exception as exc:  # noqa: BLE001
                findings.append({"script_index": i, "src": s["src"], "fetch_error": str(exc)})
                continue
        else:
            origin = f"inline#{i}"
            text = s.get("inline") or ""
        if not text:
            continue
        hits = []
        low = text.lower()
        for kw in KEYWORDS:
            pos = low.find(kw.lower())
            if pos != -1:
                start = max(0, pos - 60)
                hits.append({"keyword": kw, "context": text[start:pos + len(kw) + 60]})
        if hits:
            findings.append({"script_index": i, "src": origin, "hits": hits})
    return findings


async def main() -> int:
    report: dict = {
        "at_start": datetime.now(timezone.utc).isoformat(),
        "platform": PLATFORM,
        "program": PROGRAM,
        "edit_url": EDIT_URL,
        "mode": "diagnostic_read_only_never_saves",
        "reused_canary_report": "data/captures/canary-code-parrainage-kraken.json",
    }

    try:
        rendered_canary, rendered_original, _vc, _vo = _canary_and_original_renders()
    except Exception as exc:  # noqa: BLE001
        report["abort_before_browser"] = str(exc)
        OUT.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"ABORT before browser: {exc}")
        return 2

    bumper = _bumper()
    cfg = {
        "url": bumper.CONFIG["code"]["url"],
        "email": os.environ.get("CODE_PARRAINAGE_EMAIL") or "",
        "password": os.environ.get("CODE_PARRAINAGE_PASSWORD") or "",
    }

    from playwright.async_api import async_playwright

    requests_log: list[dict] = []
    console_log: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = await bumper.new_context(browser)
        page = await ctx.new_page()
        page.on("console", lambda msg: console_log.append(f"[{msg.type}] {msg.text}"[:300]))
        page.on(
            "request",
            lambda req: requests_log.append(
                {"method": req.method, "url": req.url, "resource_type": req.resource_type}
            ) if req.method != "GET" or "modif" in req.url else None,
        )

        try:
            await _login(page, cfg)
            await page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=60000)
            await bumper.human_sleep(1.2, 2.0)

            report["identity_before"] = {
                "company": None, "code_ou_lien": None,
            }
            baseline_dump = await _dump_form_debug(page, "debug_diag_baseline.json")
            report["identity_before"]["company"] = next(
                (i.get("preview") for i in baseline_dump.get("inputs") or [] if i.get("name") == "company"), None
            )
            report["identity_before"]["code_ou_lien"] = next(
                (i.get("preview") for i in baseline_dump.get("inputs") or [] if i.get("name") == "code_ou_lien"), None
            )

            report["census_baseline"] = await _form_and_field_census(page)
            report["validity_baseline"] = await _validity_snapshot(page)
            report["script_scan"] = await _scan_scripts(page, cfg["url"])

            # --- TEST 1: Playwright .fill() (same mechanism the canary used) ---
            await page.locator("textarea#offre").fill(rendered_canary)
            report["test1_fill_immediate"] = await _read_state(page)
            await asyncio.sleep(1.5)
            report["test1_fill_after_wait_1500ms"] = await _read_state(page)

            # --- reload, no save, confirm server state untouched ---
            await page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=60000)
            await bumper.human_sleep(1.0, 1.5)
            reload1_dump = await _dump_form_debug(page, "debug_diag_reload1.json")
            offre_after_reload1 = next(
                (i.get("preview") for i in reload1_dump.get("inputs") or [] if i.get("name") == "offre"), None
            )
            report["server_state_after_test1_reload"] = {
                "offre_preview": offre_after_reload1,
                "matches_original_start": bool(offre_after_reload1)
                and offre_after_reload1.startswith(ORIGINAL_VALUE.split(" ")[0])
                and CANARY_VALUE not in (offre_after_reload1 or ""),
            }

            # --- TEST 2: real keystroke simulation (keydown/keyup/input per char) ---
            ta = page.locator("textarea#offre")
            await ta.fill("")
            await ta.press_sequentially(rendered_canary, delay=5)
            report["test2_keystrokes_immediate"] = await _read_state(page)
            await asyncio.sleep(1.5)
            report["test2_keystrokes_after_wait_1500ms"] = await _read_state(page)

            # --- final reload, no save, confirm still untouched ---
            await page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=60000)
            await bumper.human_sleep(1.0, 1.5)
            reload2_dump = await _dump_form_debug(page, "debug_diag_reload2.json")
            offre_after_reload2 = next(
                (i.get("preview") for i in reload2_dump.get("inputs") or [] if i.get("name") == "offre"), None
            )
            company_final = next(
                (i.get("preview") for i in reload2_dump.get("inputs") or [] if i.get("name") == "company"), None
            )
            code_final = next(
                (i.get("preview") for i in reload2_dump.get("inputs") or [] if i.get("name") == "code_ou_lien"), None
            )
            report["server_state_after_test2_reload"] = {
                "offre_preview": offre_after_reload2,
                "matches_original_start": bool(offre_after_reload2)
                and offre_after_reload2.startswith(ORIGINAL_VALUE.split(" ")[0])
                and CANARY_VALUE not in (offre_after_reload2 or ""),
                "company": company_final,
                "code_ou_lien": code_final,
                "identity_unchanged": company_final == EXPECTED_COMPANY and code_final == EXPECTED_CODE_OU_LIEN,
            }

        except Exception as exc:  # noqa: BLE001
            report["fatal_error"] = str(exc)
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    report["requests_log"] = requests_log
    report["console_log"] = console_log[:200]
    report["at_end"] = datetime.now(timezone.utc).isoformat()

    OUT.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report.get("fatal_error") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
