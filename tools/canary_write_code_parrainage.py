#!/usr/bin/env python3
"""One-shot, rollback-guaranteed canary write for Code-Parrainage / Kraken.

Explicit operator authorization (2026-08-16): single program (kraken),
single field (the offre textarea's embedded referee_reward span), exactly
2 saves maximum (canary, rollback), in ONE browser session/login.

Real edit form (confirmed by content 2026-08-16, GH runs 31951629497 /
31951796737): https://code-parrainage.net/modif/84601
  - input#company = "Kraken" -- NEVER touched
  - input#code_ou_lien = "cpbrgddy" -- NEVER touched (this platform has a
    single combined code/link field, unlike parrainage-co/1parrainage)
  - textarea#offre = full 750-char description -- the ONLY field filled
  - button "Enregistrer les modifications" -- the real Save

Pre-Save gates, all mandatory, all ABORT-before-any-fill on failure:
  1. Real snapshot of company/code_ou_lien/offre/hidden modifpost.
  2. Real slider/captcha check via bumper.solve_slider() (same official
     solver already proven at login on this platform) -- if a widget is
     detected and NOT solved, ABORT. No bypass, no second solver, no retry
     of the same puzzle (solve_slider's own internal policy).
  3. Save button must be visible AND enabled with nothing else requested
     -- no click on "règles de rédaction" or any other link/button ever
     happens, so no modal it might open can be in the way. If the Save
     button is not directly visible+enabled anyway, ABORT (ambiguous
     blocking state) rather than guess a dismissal action.
  4. company == "Kraken" and code_ou_lien == "cpbrgddy" verified
     immediately before the fill.

MAY_HAVE_WRITTEN discipline: exactly one click per phase (canary,
rollback), never a retry of the same click. A timeout/exception after a
click is never treated as "no write happened" -- the next step is always
a FRESH reread (never cached data) to determine the real state before
deciding anything. If the canary reread is ambiguous, the rollback is
still attempted (single click) -- the canary click is never repeated.

CRITICAL FAIL: if company or code_ou_lien is ever observed to differ from
"Kraken"/"cpbrgddy" at ANY checkpoint (before, after canary, after
rollback), that is reported as a critical failure -- immediate stop, no
further action of any kind.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.canonical_text import canonical_contains, canonical_lines, canonical_match  # noqa: E402
from lib.http_fetch import fetch_text  # noqa: E402
from lib.offers import OffersRepository  # noqa: E402
from lib.operator_overrides import OperatorOverrideStore  # noqa: E402
from lib.renderer import MappingRepository, Renderer, TemplateRepository  # noqa: E402
from platforms.code_parrainage.writer import (  # noqa: E402
    WritePlan,
    _dump_form_debug,
    _extract_public_body,
    _login,
)

PLATFORM = "code-parrainage"
PROGRAM = "kraken"
LANGUAGE = "fr"
FIELD = "referee_reward"
ORIGINAL_VALUE = "200 € en cryptomonnaies"
CANARY_VALUE = "200 € en crypto-monnaies"
EDIT_URL = "https://code-parrainage.net/modif/84601"
PUBLIC_URL = "https://code-parrainage.net/annonce/84601"
EXPECTED_COMPANY = "Kraken"
EXPECTED_CODE_OU_LIEN = "cpbrgddy"

OUT = ROOT / "data" / "captures"
REPORT_PATH = OUT / "canary-code-parrainage-kraken.json"


def _bumper():
    import bumper as bumper_mod

    return bumper_mod


def _sha256(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canary_and_original_renders() -> tuple[str, str, dict, dict]:
    """Compute both full render targets up front, touching the override
    store only transiently -- byte-identical restoration afterward (same
    fix already proven for parrainage-co's canary script).
    """
    from lib.paths import OPERATOR_OVERRIDES_PATH

    original_bytes = (
        OPERATOR_OVERRIDES_PATH.read_bytes() if OPERATOR_OVERRIDES_PATH.exists() else None
    )

    store = OperatorOverrideStore()
    existing = [
        o for o in store.load()
        if o.program == PROGRAM and o.field == FIELD and o.platform == PLATFORM
    ]
    if existing:
        raise RuntimeError(
            f"REFUSING: a PLATFORM_OPERATOR override for {PLATFORM}/{PROGRAM}/{FIELD} "
            "already exists -- must never touch a pre-existing operator decision."
        )

    mapping = MappingRepository().load(PLATFORM, PROGRAM, LANGUAGE)
    templates = TemplateRepository()
    renderer = Renderer(OffersRepository())
    template = templates.load_text(PLATFORM, PROGRAM, LANGUAGE)
    offer = renderer.offers.get_by_slug(PROGRAM)

    rendered_original = renderer.render(template, mapping, offer=offer)
    variables_original = renderer.build_variables(mapping, offer=offer)

    try:
        store.upsert(
            PROGRAM, FIELD, CANARY_VALUE, platform=PLATFORM,
            message="canary_write_verified_probe_2026-08-16_code-parrainage",
        )
        try:
            rendered_canary = renderer.render(template, mapping, offer=offer)
            variables_canary = renderer.build_variables(mapping, offer=offer)
        finally:
            store.remove(PROGRAM, FIELD, platform=PLATFORM)
    finally:
        if original_bytes is not None:
            OPERATOR_OVERRIDES_PATH.write_bytes(original_bytes)

    remaining = [
        o for o in store.load()
        if o.program == PROGRAM and o.field == FIELD and o.platform == PLATFORM
    ]
    if remaining:
        raise RuntimeError("override_cleanup_failed — refusing to proceed")
    if original_bytes is not None and OPERATOR_OVERRIDES_PATH.read_bytes() != original_bytes:
        raise RuntimeError("override_store_not_byte_identical_after_cleanup — refusing to proceed")

    if variables_canary.get("personal_code") != variables_original.get("personal_code"):
        raise RuntimeError("personal_code differs between canary/original renders — abort")
    if variables_canary.get("personal_link") != variables_original.get("personal_link"):
        raise RuntimeError("personal_link differs between canary/original renders — abort")
    if variables_original.get(FIELD) != ORIGINAL_VALUE:
        raise RuntimeError(
            f"expected original {FIELD}={ORIGINAL_VALUE!r}, "
            f"got {variables_original.get(FIELD)!r} — abort, values have drifted"
        )
    if variables_canary.get(FIELD) != CANARY_VALUE:
        raise RuntimeError("canary render did not pick up CANARY_VALUE — abort")

    return rendered_canary, rendered_original, variables_canary, variables_original


def _read_dump_field(dump: dict, name: str) -> str | None:
    """Prefers the untruncated `full` value (see _dump_form_debug's
    docstring for why: comparing a 200-char `preview` against the full
    ~787-char rendered offer via canonical_contains() can never match
    regardless of real success -- confirmed root cause of the false
    canary_ok/rollback_ok=False on run 31962858807 despite a genuinely
    persisted write later proven on run 32044775992). Falls back to
    `preview` for dumps that predate this field (older captured JSON,
    and hand-built fixtures in tests that only set `preview`).
    """
    for item in (dump or {}).get("inputs") or []:
        if item.get("name") == name:
            full = item.get("full")
            return full if full is not None else item.get("preview")
    return None


def _account_snapshot(dump: dict) -> dict:
    return {
        "company": _read_dump_field(dump, "company"),
        "code_ou_lien": _read_dump_field(dump, "code_ou_lien"),
        "offre": _read_dump_field(dump, "offre"),
        "modifpost": _read_dump_field(dump, "modifpost"),
        "offre_sha256": _sha256(_read_dump_field(dump, "offre")),
    }


class _IdentityCriticalFail(RuntimeError):
    """Raised ONLY by _guard_identity on a proven company/code_ou_lien
    drift. This is the ONE exception type allowed to skip the mandatory
    rollback attempt below (an automated restore after a proven identity
    drift could itself be more dangerous than stopping) -- every other
    exception after canary_may_have_written becomes True must still lead
    to a rollback attempt. Kept as a distinct type (not bare RuntimeError)
    precisely so that distinction can be made structurally, not by string
    matching.
    """


def _guard_identity(snapshot: dict, *, phase: str, report: dict) -> None:
    """CRITICAL FAIL check: company/code_ou_lien must never drift. Raises
    _IdentityCriticalFail to halt everything immediately -- no further
    action of any kind, except the caller's own decision about whether a
    rollback attempt already in flight is safe to continue (it never is,
    once identity has drifted).
    """
    company = snapshot.get("company")
    code_ou_lien = snapshot.get("code_ou_lien")
    ok = company == EXPECTED_COMPANY and code_ou_lien == EXPECTED_CODE_OU_LIEN
    report.setdefault("identity_checks", {})[phase] = {
        "company": company,
        "code_ou_lien": code_ou_lien,
        "ok": ok,
    }
    if not ok:
        report["critical_fail"] = (
            f"IDENTITY DRIFT at phase={phase}: company={company!r} "
            f"(expected {EXPECTED_COMPANY!r}), code_ou_lien={code_ou_lien!r} "
            f"(expected {EXPECTED_CODE_OU_LIEN!r})"
        )
        raise _IdentityCriticalFail(report["critical_fail"])


async def _check_slider_and_solve(page, report: dict, *, phase: str) -> None:
    """Real, live detection -- never presumed. Aborts (raises) if a
    widget is present and not solved. No bypass, no retry beyond
    solve_slider's own single-attempt policy.
    """
    bumper = _bumper()
    solved = await bumper.solve_slider(page)
    diag_path = ROOT / "data" / "captures" / "code-parrainage-slider-diag.json"
    diag = None
    try:
        if diag_path.exists():
            diag = json.loads(diag_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    report.setdefault("slider_checks", {})[phase] = {"solved": solved, "diag": diag}
    if not solved:
        report["abort_reason"] = f"slider present and not resolved at phase={phase} — no bypass"
        raise RuntimeError(report["abort_reason"])


async def _check_save_button_clickable(page, report: dict, *, phase: str):
    """No dismissal of any modal is ever attempted -- 'règles de rédaction'
    is never clicked, so its modal never opens. If Save isn't directly
    visible+enabled anyway, this is an ambiguous blocking state: abort.
    """
    btn = page.locator('button:has-text("Enregistrer les modifications")').first
    count = await btn.count()
    visible = bool(count) and await btn.is_visible()
    enabled = bool(count) and await btn.is_enabled()
    report.setdefault("save_button_checks", {})[phase] = {
        "count": count, "visible": visible, "enabled": enabled,
    }
    if not (visible and enabled):
        report["abort_reason"] = (
            f"Save button not directly visible+enabled at phase={phase} "
            "(ambiguous blocking state) — no dismissal attempted, aborting"
        )
        raise RuntimeError(report["abort_reason"])
    return btn


async def _register_network_listeners(page, network_evidence: dict, phase_ref: dict) -> None:
    """Captures ONLY the modification.php request/response, scoped to
    whichever phase is currently active in phase_ref["name"] (set to
    "canary" / "rollback" immediately around each _click_save call, and
    to None otherwise so the login POST and any unrelated traffic is never
    touched by this listener at all).

    Never logs cookies, Set-Cookie, Authorization, or any session/password
    data -- only method/url/content-type/status, the already-known-public
    form field names being sent (company/code_ou_lien/offre/modifpost,
    the exact same fields this script itself fills), and a hash + short
    excerpt of the response body for later human review.
    """

    async def _on_request(request) -> None:
        phase = phase_ref.get("name")
        if not phase or "modification.php" not in request.url:
            return
        post_data = request.post_data or ""
        parsed = dict(urllib.parse.parse_qsl(post_data, keep_blank_values=True))
        offre_val = parsed.get("offre")
        network_evidence.setdefault(phase, {})["request"] = {
            "method": request.method,
            "url": request.url,
            "content_type": request.headers.get("content-type"),
            "payload_fields_present": sorted(parsed.keys()),
            "payload_offre_len": len(offre_val) if offre_val is not None else None,
            "payload_offre_sha256": _sha256(offre_val),
            "payload_contains_canary_value": bool(offre_val) and CANARY_VALUE in offre_val,
            "payload_contains_original_value": bool(offre_val) and ORIGINAL_VALUE in offre_val,
            "payload_company": parsed.get("company"),
            "payload_code_ou_lien": parsed.get("code_ou_lien"),
            "payload_modifpost": parsed.get("modifpost"),
        }

    async def _on_response(response) -> None:
        phase = phase_ref.get("name")
        if not phase or "modification.php" not in response.url:
            return
        try:
            body = await response.text()
        except Exception:  # noqa: BLE001
            body = ""
        network_evidence.setdefault(phase, {})["response"] = {
            "status": response.status,
            "url": response.url,
            "content_type": response.headers.get("content-type"),
            "body_sha256": _sha256(body) if body else None,
            "body_len": len(body),
            "body_excerpt": body[:800],
        }

    page.on("request", lambda req: asyncio.create_task(_on_request(req)))
    page.on("response", lambda resp: asyncio.create_task(_on_response(resp)))


EXPECTED_CONFIRM_MESSAGE = "Êtes-vous sûr de vouloir modifier cette annonce ?"


def _normalize_dialog_message(msg: str | None) -> str:
    return " ".join((msg or "").split()).strip()


async def _register_dialog_handler(page, report: dict, phase_ref: dict) -> None:
    """Root cause (2026-08-16, confirmed from the site's own JS,
    debug_code_script_2.js): the edit form's submit handler calls native
    window.confirm('Etes-vous sur de vouloir modifier cette annonce ?')
    and does e.preventDefault() if it is not accepted. Playwright's
    default behavior for an unhandled dialog is to DISMISS it -- which is
    exactly what silently blocked both Save clicks in GH run 31962858807
    (no request was ever sent to modification.php; nothing to do with
    server-side rejection or the 200-character counter). A real human
    clicking "Enregistrer les modifications" would click OK on this
    exact prompt, so accepting it reproduces real user behavior, not a
    bypass of any actual site protection.

    STRICT gate (2026-08-16 operator instruction): a dialog is accepted
    ONLY when it is a "confirm" type, its normalized message matches
    EXPECTED_CONFIRM_MESSAGE exactly, AND phase_ref["name"] is currently
    "canary" or "rollback" (i.e. we are inside one of the two authorized
    Save-click windows). Anything else -- an unexpected alert, a confirm
    with different wording, or the expected confirm firing outside a
    Save window -- is NEVER auto-accepted: it is logged, dismissed (so it
    cannot hang the run), and recorded under report["unexpected_dialog"]
    for the operator to review. This is deliberately a SEPARATE field
    from report["critical_fail"] (reserved for proven identity drift) --
    an unexpected dialog is not, by itself, proof that a rollback attempt
    would be unsafe, so it must not silently skip the mandatory rollback.
    """

    async def _on_dialog(dialog) -> None:
        phase = phase_ref.get("name")
        is_expected_confirm = (
            dialog.type == "confirm"
            and _normalize_dialog_message(dialog.message) == EXPECTED_CONFIRM_MESSAGE
        )
        in_save_phase = phase in ("canary", "rollback")
        entry = {
            "type": dialog.type,
            "message": dialog.message,
            "phase": phase,
            "expected_confirm": is_expected_confirm,
            "in_save_phase": in_save_phase,
        }
        if is_expected_confirm and in_save_phase:
            entry["action"] = "accept"
            report.setdefault("dialogs_seen", []).append(entry)
            await dialog.accept()
            return
        entry["action"] = "dismiss"
        report.setdefault("dialogs_seen", []).append(entry)
        report["unexpected_dialog"] = entry
        try:
            await dialog.dismiss()
        except Exception:  # noqa: BLE001
            pass

    page.on("dialog", lambda d: asyncio.create_task(_on_dialog(d)))


async def _fill_offre_only(page, text: str) -> None:
    await page.locator("textarea#offre").fill(text)


async def _click_save(page, btn) -> None:
    bumper = _bumper()
    await bumper.human_click(page, btn)
    await page.wait_for_load_state("networkidle")
    await bumper.human_sleep(1.5, 2.5)


async def _fetch_public_evidence(expected_rendered: str, *, phase: str) -> dict:
    try:
        html = fetch_text(PUBLIC_URL)
        raw_sha = _sha256(html)
        extracted = _extract_public_body(html)
        return {
            "phase": phase,
            "raw_html_sha256": raw_sha,
            "extracted_text_sha256": _sha256(extracted),
            "extracted_text_len": len(extracted or ""),
            "canonical_lines": canonical_lines(extracted or ""),
            "contains_original_reward": ORIGINAL_VALUE in (extracted or ""),
            "contains_canary_reward": CANARY_VALUE in (extracted or ""),
            "canonical_match_vs_expected": (
                canonical_match(extracted, expected_rendered) if extracted else None
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"phase": phase, "error": str(exc), "canonical_match_vs_expected": None}


async def main() -> int:
    report: dict = {
        "at_start": datetime.now(timezone.utc).isoformat(),
        "platform": PLATFORM,
        "program": PROGRAM,
        "field": FIELD,
        "original_value": ORIGINAL_VALUE,
        "canary_value": CANARY_VALUE,
        "edit_url": EDIT_URL,
        "public_url": PUBLIC_URL,
        "comparison_mechanism": "canonical_line_sequence_v2",
        "phases": {},
    }

    try:
        rendered_canary, rendered_original, variables_canary, variables_original = (
            _canary_and_original_renders()
        )
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

    canary_may_have_written = False
    network_evidence: dict = {}
    phase_ref: dict = {"name": None}
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
        await _register_network_listeners(page, network_evidence, phase_ref)
        await _register_dialog_handler(page, report, phase_ref)
        identity_failed = False
        try:
            # --- login + BEFORE snapshot (nothing written yet; an abort here
            # is always safe -- canary_may_have_written is still False) ---
            await _login(page, cfg)
            await page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=60000)
            await bumper.human_sleep(1.2, 2.0)

            before_dump = await _dump_form_debug(page, "debug_code_canary_before.json")
            before_snapshot = _account_snapshot(before_dump)
            report["phases"]["before_account"] = before_snapshot
            try:
                await page.screenshot(path="debug_code_canary_before.png", full_page=True)
            except Exception:
                pass
            _guard_identity(before_snapshot, phase="before", report=report)
            before_public = await _fetch_public_evidence(rendered_original, phase="before")
            report["phases"]["before_public"] = before_public

            # --- Pre-Save gates (slider + Save-button clickability) ---
            await _check_slider_and_solve(page, report, phase="canary")
            save_btn = await _check_save_button_clickable(page, report, phase="canary")

            # --- CANARY SAVE (single click, no retry) ---
            canary_may_have_written = True  # set BEFORE clicking. From this
            # point on, ONLY an _IdentityCriticalFail (set via identity_failed
            # below) is allowed to skip the mandatory rollback -- every other
            # exception (click timeout, networkidle failure, reread failure,
            # instrumentation error, ...) must be caught locally so execution
            # still reaches the rollback block further down.
            await _fill_offre_only(page, rendered_canary)
            phase_ref["name"] = "canary"
            try:
                await _click_save(page, save_btn)
                report["phases"]["canary_save"] = {"clicked": True}
            except Exception as exc:  # noqa: BLE001 -- MAY_HAVE_WRITTEN: never skip rollback for this
                report["phases"]["canary_save"] = {"clicked": True, "click_exception": str(exc)}
            finally:
                await asyncio.sleep(0.5)  # let scheduled request/response/dialog listeners flush
                phase_ref["name"] = None

            # --- CANARY: fresh reread, account + public (best-effort). Only
            # an identity drift here is allowed to skip the rollback below. ---
            try:
                canary_dump = None
                try:
                    await page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=60000)
                    await bumper.human_sleep(1.0, 1.8)
                    canary_dump = await _dump_form_debug(page, "debug_code_canary_after.json")
                except Exception as exc:  # noqa: BLE001
                    report["phases"]["canary_save"]["account_reread_error"] = str(exc)

                canary_snapshot = _account_snapshot(canary_dump) if canary_dump else {}
                report["phases"]["canary_account"] = canary_snapshot
                if canary_dump:
                    _guard_identity(canary_snapshot, phase="canary", report=report)
                report["phases"]["canary_account"]["canonical_match_vs_expected"] = (
                    canonical_contains(canary_snapshot.get("offre") or "", rendered_canary)
                    if canary_snapshot.get("offre") else None
                )
                report["phases"]["canary_public"] = await _fetch_public_evidence(
                    rendered_canary, phase="canary"
                )
            except _IdentityCriticalFail as exc:
                identity_failed = True
                report["fatal_error"] = str(exc)
            except Exception as exc:  # noqa: BLE001
                report["phases"].setdefault("canary_save", {})["unexpected_post_click_error"] = str(exc)

            # --- MANDATORY ROLLBACK — always attempted once canary_may_have_
            # written is True, UNLESS identity_failed (the ONE operator-
            # authorized exception: an automated restore after a proven
            # identity drift could itself be more dangerous than stopping). ---
            if identity_failed:
                report["rollback_skipped_reason"] = "identity_critical_fail_before_rollback"
            else:
                try:
                    # --- FRESH pre-rollback identity re-validation
                    # (2026-08-17 patch): never trust the canary reread for
                    # this, even on its happy path -- always re-navigate to
                    # EDIT_URL and take a brand-new snapshot right before
                    # even considering the rollback Save, so a completely
                    # failed canary reread (or one that returned stale/wrong
                    # data) can never lead to a rollback click without first
                    # reconfirming this is really the Kraken 84601 listing.
                    # If the guard cannot be established at all (navigation
                    # fails, dump comes back empty), _account_snapshot on an
                    # empty dump yields company=None/code_ou_lien=None, which
                    # _guard_identity treats exactly like a proven mismatch
                    # -- CRITICAL FAIL, no rollback Save, per operator
                    # instruction: "impossible de relire l'identite => aucun
                    # Save rollback".
                    rollback_guard_dump = None
                    try:
                        await page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=60000)
                        await bumper.human_sleep(1.0, 1.8)
                        rollback_guard_dump = await _dump_form_debug(
                            page, "debug_code_rollback_pre_guard.json"
                        )
                    except Exception as exc:  # noqa: BLE001
                        report["rollback_guard_navigation_error"] = str(exc)

                    rollback_guard_snapshot = (
                        _account_snapshot(rollback_guard_dump) if rollback_guard_dump else {}
                    )
                    report["phases"]["rollback_pre_guard"] = rollback_guard_snapshot
                    _guard_identity(rollback_guard_snapshot, phase="rollback_pre_guard", report=report)

                    # --- Guard passed: only now proceed with the rollback Save ---
                    await _check_slider_and_solve(page, report, phase="rollback")
                    save_btn2 = await _check_save_button_clickable(page, report, phase="rollback")
                    await _fill_offre_only(page, rendered_original)
                    phase_ref["name"] = "rollback"
                    try:
                        await _click_save(page, save_btn2)
                        report["phases"]["rollback_save"] = {"clicked": True}
                    except Exception as exc:  # noqa: BLE001
                        report["phases"]["rollback_save"] = {"clicked": True, "click_exception": str(exc)}
                    finally:
                        await asyncio.sleep(0.5)  # let scheduled listeners flush
                        phase_ref["name"] = None

                    rollback_dump = None
                    try:
                        await page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=60000)
                        await bumper.human_sleep(1.0, 1.8)
                        rollback_dump = await _dump_form_debug(page, "debug_code_rollback_after.json")
                    except Exception as exc:  # noqa: BLE001
                        report["phases"]["rollback_save"]["account_reread_error"] = str(exc)

                    rollback_snapshot = _account_snapshot(rollback_dump) if rollback_dump else {}
                    report["phases"]["rollback_account"] = rollback_snapshot
                    if rollback_dump:
                        _guard_identity(rollback_snapshot, phase="rollback", report=report)
                    report["phases"]["rollback_account"]["canonical_match_vs_expected"] = (
                        canonical_contains(rollback_snapshot.get("offre") or "", rendered_original)
                        if rollback_snapshot.get("offre") else None
                    )
                    report["phases"]["rollback_public"] = await _fetch_public_evidence(
                        rendered_original, phase="rollback"
                    )
                except Exception as exc:  # noqa: BLE001
                    # Rollback is the last authorized action either way (max
                    # 2 saves) -- nothing further to attempt, just record.
                    report["fatal_error"] = str(exc)

        except Exception as exc:  # noqa: BLE001
            # Only reachable for failures BEFORE canary_may_have_written
            # became True (login, before-snapshot, pre-canary gates) --
            # nothing has been clicked yet, so no rollback is owed.
            report["fatal_error"] = str(exc)
            report["canary_may_have_written"] = canary_may_have_written
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    report["at_end"] = datetime.now(timezone.utc).isoformat()
    report["network_evidence"] = network_evidence

    def _match(phase_key: str) -> bool | None:
        return report.get("phases", {}).get(phase_key, {}).get("canonical_match_vs_expected")

    identity_ok = all(
        v.get("ok") for v in (report.get("identity_checks") or {}).values()
    ) and bool(report.get("identity_checks"))

    canary_ok = bool(_match("canary_account") and _match("canary_public"))
    rollback_ok = bool(_match("rollback_account") and _match("rollback_public"))
    write_verified = bool(
        canary_ok and rollback_ok and identity_ok
        and not report.get("fatal_error") and not report.get("critical_fail")
        and not report.get("unexpected_dialog")
    )
    report["canary_ok"] = canary_ok
    report["rollback_ok"] = rollback_ok
    report["identity_ok"] = identity_ok
    report["write_verified"] = write_verified

    OUT.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if report.get("critical_fail"):
        print(f"::error::CRITICAL FAIL: {report['critical_fail']}")
        return 3

    if write_verified:
        from lib.paths import golden_path, mapping_path
        from lib.write_status import mark_write_verified

        golden_path(PLATFORM, PROGRAM, LANGUAGE).write_bytes(rendered_original.encode("utf-8"))
        mpath = mapping_path(PLATFORM, PROGRAM, LANGUAGE)
        data = json.loads(mpath.read_text(encoding="utf-8"))
        data["write_status"] = "WRITE_VERIFIED"
        data["last_write_at"] = datetime.now(timezone.utc).isoformat()
        mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        legacy_checks = {
            "authenticated": True,
            "targeted_edit": bool(report["phases"].get("canary_save", {}).get("clicked")),
            "submit_ok": bool(
                report["phases"].get("canary_save", {}).get("clicked")
                and report["phases"].get("rollback_save", {}).get("clicked")
            ),
            "reread_account": bool(canary_ok and rollback_ok),
            "expected_values_present": identity_ok,
            "immutable_preserved": identity_ok,
        }
        evidence = {
            "post_match": True,
            "canary_and_rollback": True,
            "announcement_url": PUBLIC_URL,
            "edit_url": EDIT_URL,
            "source": "canary_write_code_parrainage",
            "checks": legacy_checks,
            "full_report": report,
        }
        promo = mark_write_verified(PLATFORM, program=PROGRAM, evidence=evidence)
        print(f"WRITE_VERIFIED code-parrainage registry={promo}")
        return 0 if promo.get("ok") else 1

    if not rollback_ok:
        print("::error::ROLLBACK NOT CONFIRMED — see report for exact remaining state.")
        return 1
    print("Canary verification incomplete — rollback confirmed, no WRITE_VERIFIED promotion.")
    return 1 if not canary_ok else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
