#!/usr/bin/env python3
"""Read-only GitHub-headless census of the 1Parrainage Save controls.

The probe authenticates, opens the exact Kraken edit page, and captures the
form/control structure at three checkpoints: initial edit DOM, after the
canary account-normalization helper, and after preparing a marker in CKEditor.
It never resolves or clicks a Save control and never submits the edit form.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platforms.oneparrainage.writer import (  # noqa: E402
    _bumper,
    _cfg,
    _detect_challenge,
    _login,
    _resolve_edit_url,
    build_write_plan,
)
from tools.canary_write_1parrainage import (  # noqa: E402
    _read_account,
    _set_body_without_save,
)

DEFAULT_OUTPUT = ROOT / "diagnostic-artifacts" / "1parrainage-save-dom.json"

_CENSUS_JS = r"""
() => {
  const clean = (value, limit = 300) => ((value || '') + '')
    .trim().replace(/\s+/g, ' ').slice(0, limit);
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return !!(rect.width && rect.height)
      && style.display !== 'none'
      && style.visibility !== 'hidden'
      && style.opacity !== '0';
  };
  const box = (el) => {
    const rect = el.getBoundingClientRect();
    return {
      x: rect.x, y: rect.y, width: rect.width, height: rect.height,
      top: rect.top, right: rect.right, bottom: rect.bottom, left: rect.left,
    };
  };
  const formInfo = (form) => form ? {
    id: clean(form.id),
    name: clean(form.getAttribute('name')),
    action: clean(form.action || form.getAttribute('action')),
    method: clean(form.method || form.getAttribute('method')).toUpperCase(),
    matches_edit_form: (form.action || '').includes('parrainages/edit'),
  } : null;
  const minimalOuter = (el) => {
    const names = ['id', 'class', 'name', 'type', 'role', 'form',
      'disabled', 'aria-disabled', 'aria-label', 'title'];
    const attrs = names.flatMap((name) => {
      const value = el.getAttribute(name);
      return value === null ? [] : [`${name}="${clean(value, 180)}"`];
    });
    const text = clean(el.innerText || el.value || '', 180);
    return `<${el.tagName.toLowerCase()} ${attrs.join(' ')}>${text}</${el.tagName.toLowerCase()}>`;
  };
  const controls = Array.from(document.querySelectorAll(
    'button, input[type="submit"], input[type="button"], [role="button"]'
  )).map((el, index) => {
    const text = clean(el.innerText);
    const value = clean(el.value);
    const label = clean(text || value || el.getAttribute('aria-label'));
    const owner = el.form || el.closest('form');
    const isVisible = visible(el);
    const disabled = !!el.disabled || el.getAttribute('aria-disabled') === 'true';
    return {
      index,
      tag: el.tagName.toLowerCase(),
      text,
      value,
      label,
      name: clean(el.getAttribute('name')),
      id: clean(el.id),
      class: clean(el.className),
      type: clean(el.getAttribute('type')),
      role: clean(el.getAttribute('role')),
      visible: isVisible,
      enabled: !disabled,
      disabled,
      bbox: box(el),
      in_viewport: isVisible && box(el).bottom > 0 && box(el).right > 0
        && box(el).top < innerHeight && box(el).left < innerWidth,
      form: formInfo(owner),
      matches_save_term: /envoyer|valider|modifier|enregistrer|sauvegarder/i.test(label),
      outer_html_minimized: minimalOuter(el),
    };
  });
  const editForms = Array.from(document.querySelectorAll('form'))
    .filter((form) => (form.action || '').includes('parrainages/edit'))
    .map(formInfo);
  return {
    url: location.origin + location.pathname,
    viewport: {width: innerWidth, height: innerHeight},
    edit_form_count: editForms.length,
    edit_forms: editForms,
    control_count: controls.length,
    controls,
    save_term_controls: controls.filter((control) => control.matches_save_term),
  };
}
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _control_key(control: dict[str, Any]) -> str:
    fields = (
        control.get("tag"),
        control.get("id"),
        control.get("name"),
        control.get("type"),
        control.get("label"),
        control.get("class"),
    )
    return "|".join(str(value or "") for value in fields)


def compare_census(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_controls = {_control_key(item): item for item in before.get("controls", [])}
    after_controls = {_control_key(item): item for item in after.get("controls", [])}
    shared = sorted(set(before_controls) & set(after_controls))
    changed = []
    for key in shared:
        left = before_controls[key]
        right = after_controls[key]
        fields = [
            name
            for name in ("visible", "enabled", "disabled", "bbox", "in_viewport", "form")
            if left.get(name) != right.get(name)
        ]
        if fields:
            changed.append({"control": key, "fields": fields})
    return {
        "edit_form_count_before": before.get("edit_form_count"),
        "edit_form_count_after": after.get("edit_form_count"),
        "control_count_before": before.get("control_count"),
        "control_count_after": after.get("control_count"),
        "added_controls": sorted(set(after_controls) - set(before_controls)),
        "removed_controls": sorted(set(before_controls) - set(after_controls)),
        "changed_controls": changed,
    }


async def _census(page, phase: str) -> dict[str, Any]:
    result = await page.evaluate(_CENSUS_JS)
    result["phase"] = phase
    return result


async def run(output: Path) -> dict[str, Any]:
    if not os.environ.get("ONEPARRAINAGE_EMAIL") or not os.environ.get(
        "ONEPARRAINAGE_PASSWORD"
    ):
        raise RuntimeError("ONEPARRAINAGE_EMAIL/PASSWORD missing")

    from playwright.async_api import async_playwright

    plan = build_write_plan("1parrainage", "kraken", "fr")
    marker = f"AUTOFRESH_1P_SAVE_DOM_DIAGNOSTIC_{os.environ.get('GITHUB_RUN_ID') or 'local'}"
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "authenticated_edit_dom_only_no_save",
        "platform": "1parrainage",
        "program": "kraken",
        "started_at": _now(),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "platform_writes": 0,
        "save_clicks": 0,
        "edit_form_submits_after_login": 0,
        "marker": marker,
        "checkpoints": {},
    }

    pw = await async_playwright().start()
    browser = None
    context = None
    page = None
    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=fr-FR"],
        )
        context = await _bumper().new_context(browser)
        page = await context.new_page()
        await page.set_viewport_size({"width": 1280, "height": 720})
        await _login(page, _cfg())
        edit_url = await _resolve_edit_url(page, plan)
        if edit_url != plan.edit_url:
            raise RuntimeError(
                f"edit identity drift: expected={plan.edit_url} got={edit_url}"
            )
        await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
        await _detect_challenge(page)

        checkpoint_a = await _census(page, "A_AFTER_EDIT_OPEN")
        original_body, account = await _read_account(
            page, "diagnostic_account", marker
        )
        checkpoint_b = await _census(page, "B_AFTER_ACCOUNT_NORMALIZATION")
        marker_html = (
            f'<p data-autofresh-canary="1parrainage-save-dom-diagnostic">{marker}</p>'
        )
        prepared = await _set_body_without_save(
            page,
            original_body.rstrip() + "\n" + marker_html,
            marker,
            expect_marker=True,
        )
        checkpoint_c = await _census(page, "C_AFTER_MARKER_PREP")

        report["account"] = account
        report["marker_prepared_dom_only"] = prepared
        report["checkpoints"] = {
            "A": checkpoint_a,
            "B": checkpoint_b,
            "C": checkpoint_c,
        }
        report["comparisons"] = {
            "A_to_B": compare_census(checkpoint_a, checkpoint_b),
            "B_to_C": compare_census(checkpoint_b, checkpoint_c),
            "A_to_C": compare_census(checkpoint_a, checkpoint_c),
        }
    finally:
        if page is not None:
            await page.close()
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        await pw.stop()

    report["finished_at"] = _now()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args.output))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        failure = {
            "schema_version": 1,
            "mode": "authenticated_edit_dom_only_no_save",
            "platform_writes": 0,
            "save_clicks": 0,
            "finished_at": _now(),
            "error": str(exc),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
