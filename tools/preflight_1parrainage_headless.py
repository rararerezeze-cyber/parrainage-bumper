#!/usr/bin/env python3
"""Authenticated, read-only final preflight for the 1Parrainage GH canary.

The probe logs in, opens the exact Kraken edit form, reads account/public state,
and resolves (but never clicks) the strict Save control. It never mutates
CKEditor content, submits a form, changes repository status, or writes to the
platform.
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
    EDIT_FORM,
    _bumper,
    _cfg,
    _detect_challenge,
    _login,
    _resolve_edit_url,
    build_write_plan,
)
from tools.canary_write_1parrainage import (  # noqa: E402
    EXPECTED_CODE,
    EXPECTED_LINK,
    EXPECTED_REWARD,
    _public_evidence,
    _read_account,
    _resolve_save_control,
    _save_unavailable_reason,
)
from tools.diagnose_1parrainage_save_dom import _census  # noqa: E402

DEFAULT_OUTPUT = ROOT / "diagnostic-artifacts" / "1parrainage-final-preflight.json"
EXPECTED_ACCOUNT_LEN = 1062
EXPECTED_ACCOUNT_SHA256 = (
    "ad2a57ac0e2afc795ca038c936ac2f63a93faaeb141a356bb9692fcf16598afb"
)
EXPECTED_NORMALIZED_LEN = 1063
EXPECTED_NORMALIZED_SHA256 = (
    "48d1ad78c24536e00e4e2bb8b5fd674b7a65466d6c16f5a72e7772affd0af3e7"
)
ABSENCE_MARKER = "AUTOFRESH_1P_FINAL_PREFLIGHT_MUST_BE_ABSENT"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identity_matches_expected(account: dict[str, Any]) -> bool:
    return bool(
        account.get("identity_ok")
        and account.get("identity")
        == {
            "code_present": True,
            "link_present": True,
            "reward_present": True,
        }
    )


def _strict_edit_save_controls(census: dict[str, Any]) -> list[dict[str, Any]]:
    accepted = {"envoyer", "valider"}
    return [
        control
        for control in census.get("save_term_controls", [])
        if str(control.get("label") or "").strip().casefold() in accepted
        and control.get("visible") is True
        and control.get("enabled") is True
        and (control.get("form") or {}).get("matches_edit_form") is True
    ]


async def run(output: Path) -> dict[str, Any]:
    if not os.environ.get("ONEPARRAINAGE_EMAIL") or not os.environ.get(
        "ONEPARRAINAGE_PASSWORD"
    ):
        raise RuntimeError("ONEPARRAINAGE_EMAIL/PASSWORD missing")

    from playwright.async_api import async_playwright

    plan = build_write_plan("1parrainage", "kraken", "fr")
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "authenticated_final_preflight_readonly_no_save",
        "platform": "1parrainage",
        "program": "kraken",
        "started_at": _now(),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "platform_writes": 0,
        "save_clicks": 0,
        "edit_form_submits_after_login": 0,
        "headless_login": False,
        "headless_edit": False,
        "preflight_pass": False,
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
        report["headless_login"] = True

        edit_url = await _resolve_edit_url(page, plan)
        if edit_url != plan.edit_url:
            raise RuntimeError(
                f"edit identity drift: expected={plan.edit_url} got={edit_url}"
            )
        await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
        await _detect_challenge(page)
        await page.wait_for_timeout(4000)
        report["headless_edit"] = True

        census = await _census(page, "FINAL_PREFLIGHT")
        original_body, account = await _read_account(
            page, "preflight_account", ABSENCE_MARKER
        )
        public = await _public_evidence("preflight_public", plan, ABSENCE_MARKER)
        public.pop("extracted_preview", None)

        form = page.locator(EDIT_FORM).first
        quota_reason = _save_unavailable_reason(await form.inner_text())
        _, resolved_label = await _resolve_save_control(page)
        strict_controls = _strict_edit_save_controls(census)

        account_checks = {
            "source_len_exact": len(original_body) == EXPECTED_ACCOUNT_LEN,
            "source_sha_exact": account.get("body_sha256")
            == EXPECTED_ACCOUNT_SHA256,
            "identity_ok": _identity_matches_expected(account),
            "marker_absent": account.get("marker_present") is False,
            "normalized_len_exact": account.get("normalized_body_len")
            == EXPECTED_NORMALIZED_LEN,
            "normalized_sha_exact": account.get("normalized_body_sha256")
            == EXPECTED_NORMALIZED_SHA256,
            "normalization_idempotent": account.get("normalization_idempotent")
            is True,
        }
        public_checks = {
            "full_desc_detail": public.get("public_view_type")
            == "full_detail_desc_detail",
            "identity_ok": public.get("identity_ok") is True,
            "marker_absent": public.get("marker_present") is False,
        }
        save_checks = {
            "quota_blocker_absent": quota_reason is None,
            "strict_control_count": len(strict_controls) == 1,
            "resolved_label_exact": str(resolved_label).strip().casefold()
            in {"envoyer", "valider"},
            "visible": len(strict_controls) == 1
            and strict_controls[0].get("visible") is True,
            "enabled": len(strict_controls) == 1
            and strict_controls[0].get("enabled") is True,
            "associated_with_edit_form": len(strict_controls) == 1
            and (strict_controls[0].get("form") or {}).get("matches_edit_form")
            is True,
        }
        report.update(
            {
                "account": account,
                "public": public,
                "save_control": {
                    "resolved_label": resolved_label,
                    "strict_control_count": len(strict_controls),
                    "controls": strict_controls,
                },
                "quota": {
                    "blocker": quota_reason,
                    "reset_proven": quota_reason is None
                    and len(strict_controls) == 1,
                },
                "checks": {
                    "account": account_checks,
                    "public": public_checks,
                    "save": save_checks,
                },
            }
        )
        report["preflight_pass"] = all(
            value
            for group in report["checks"].values()
            for value in group.values()
        )
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
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args.output))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("preflight_pass") else 1
    except Exception as exc:  # noqa: BLE001
        failure = {
            "schema_version": 1,
            "mode": "authenticated_final_preflight_readonly_no_save",
            "platform_writes": 0,
            "save_clicks": 0,
            "edit_form_submits_after_login": 0,
            "preflight_pass": False,
            "finished_at": _now(),
            "error": str(exc),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
