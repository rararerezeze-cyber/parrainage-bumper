"""Pre-browser guarantees for the 1Parrainage headless evidence probe."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import canary_gate as gate
from lib import write_status as ws
from tools import canary_write_1parrainage as canary


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "canary_write_1parrainage.py"
WORKFLOW = ROOT / ".github" / "workflows" / "canary_write_1parrainage.yml"


class _FakePage:
    def __init__(self):
        self.url = "about:blank"
        self.viewport_sizes = []

    async def set_viewport_size(self, size):
        self.viewport_sizes.append(size)

    async def goto(self, url, **_kwargs):
        self.url = url

    async def close(self):
        return None


class _FakeContext:
    def __init__(self):
        self.page = _FakePage()

    async def new_page(self):
        return self.page

    async def close(self):
        return None


class _FakeBrowser:
    def __init__(self):
        self.context = _FakeContext()

    async def close(self):
        return None


class _FakeChromium:
    def __init__(self, browser):
        self.browser = browser

    async def launch(self, **_kwargs):
        return self.browser


class _FakePlaywright:
    def __init__(self):
        self.browser = _FakeBrowser()
        self.chromium = _FakeChromium(self.browser)

    async def start(self):
        return self

    async def stop(self):
        return None


def _status(path: Path, *, evidence: str = "NOT_RUN", status: str = "WRITE_VERIFIED"):
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "platforms": {
                    "1parrainage": {
                        "status": status,
                        "gh_headless_save": evidence,
                        "notes": "headed proof only",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_evidence_probe_gate_is_narrow_and_requires_not_run(tmp_path, monkeypatch):
    lock = tmp_path / "canary.lock"
    monkeypatch.setattr(gate, "LOCK_PATH", lock)
    monkeypatch.setattr(gate, "live_write_blocked_reason", lambda _platform: None)
    monkeypatch.setattr(gate, "get_platform_status", lambda _platform: "WRITE_VERIFIED")
    monkeypatch.setattr(
        gate, "get_platform_meta", lambda _platform: {"gh_headless_save": "NOT_RUN"}
    )
    monkeypatch.setattr(gate, "snapshot_state", lambda _reason: {"id": "snapshot-test"})

    result = gate.guard_live_evidence_probe(
        "1parrainage", evidence_field="gh_headless_save"
    )

    assert result["ok"] is True
    assert result["status"] == "WRITE_VERIFIED"
    assert json.loads(lock.read_text(encoding="utf-8"))["kind"] == "live_evidence_probe"


def test_evidence_probe_gate_refuses_other_platform_or_completed_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "LOCK_PATH", tmp_path / "canary.lock")
    monkeypatch.setattr(gate, "live_write_blocked_reason", lambda _platform: None)
    monkeypatch.setattr(gate, "get_platform_status", lambda _platform: "WRITE_VERIFIED")
    monkeypatch.setattr(
        gate, "get_platform_meta", lambda _platform: {"gh_headless_save": "PROVEN"}
    )

    wrong = gate.guard_live_evidence_probe(
        "code-parrainage", evidence_field="gh_headless_save"
    )
    done = gate.guard_live_evidence_probe(
        "1parrainage", evidence_field="gh_headless_save"
    )
    wrong_expected = gate.guard_live_evidence_probe(
        "1parrainage", evidence_field="gh_headless_save", expected_value="PROVEN"
    )

    assert wrong["error"] == "unsupported_live_evidence_probe"
    assert done["ok"] is False
    assert done["done"] is True
    assert wrong_expected["error"] == "unsupported_live_evidence_probe"


def test_evidence_probe_gate_requires_existing_write_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "LOCK_PATH", tmp_path / "canary.lock")
    monkeypatch.setattr(gate, "live_write_blocked_reason", lambda _platform: None)
    monkeypatch.setattr(gate, "get_platform_status", lambda _platform: "CANARY_READY")

    result = gate.guard_live_evidence_probe(
        "1parrainage", evidence_field="gh_headless_save"
    )

    assert result["ok"] is False
    assert result["error"] == "WRITE_VERIFIED_REQUIRED_FOR_EVIDENCE_PROBE"


def test_script_has_two_save_attempts_and_unconditional_rollback_source_order():
    src = SCRIPT.read_text(encoding="utf-8")
    assert src.count("await _click_save_once(page)") == 2
    assert "No retry loop" in src
    assert "_fill_and_save" not in src
    idx_canary_flag = src.index("canary_may_have_persisted = True")
    idx_canary_click = src.index("await _click_save_once(page)", idx_canary_flag)
    idx_finally = src.index("finally:", idx_canary_click)
    idx_rollback_guard = src.index("if canary_may_have_persisted", idx_finally)
    idx_rollback_click = src.index("await _click_save_once(page)", idx_rollback_guard)
    assert idx_canary_flag < idx_canary_click < idx_finally < idx_rollback_click


def test_viewport_is_set_on_page_never_browser_context():
    canary_src = SCRIPT.read_text(encoding="utf-8")
    writer_path = (
        Path(canary.__file__).parents[1]
        / "platforms"
        / "oneparrainage"
        / "writer.py"
    )
    writer_src = writer_path.read_text(encoding="utf-8")

    assert "ctx.set_viewport_size" not in canary_src
    assert "ctx.set_viewport_size" not in writer_src
    assert 'page.set_viewport_size({"width": 1280, "height": 720})' in canary_src
    assert 'page.set_viewport_size({"width": 1280, "height": 720})' in writer_src


def test_success_requires_canary_public_and_exact_account_rollback():
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'report.get("canary_ok")' in src
    assert 'report.get("rollback_ok")' in src
    assert 'report.get("save_attempts") == 2' in src
    assert '"rollback_account_exact"' in src
    assert '"rollback_public_marker_absent"' in src
    assert "_sha256(rollback_body) == _sha256(original_body)" in src
    assert 'rollback_account.get("normalized_body_sha256")' in src
    assert 'before_account.get("normalized_body_sha256")' in src


def _stable_account_evidence(label: str, body: str, marker: str):
    evidence = canary._account_evidence(label, body, marker)
    evidence["normalized_body_sha256"] = canary._sha256(body + "\n")
    evidence["normalized_body_len"] = len(body) + 1
    evidence["normalization_idempotent"] = True
    return evidence


def test_public_evidence_reads_full_detail_surface(monkeypatch):
    marker = "AUTOFRESH_1P_HEADLESS_CANARY_42"
    block = (
        f"<p>{canary.EXPECTED_CODE} {canary.EXPECTED_LINK} "
        f"{canary.EXPECTED_REWARD}</p><p>{marker}</p>"
    )
    monkeypatch.setattr(
        canary,
        "fetch_public_full_view",
        lambda _plan: {
            "detail_html": f'<div id="desc_detail">{block}</div>',
            "block": block,
            "detail_url": (
                "https://www.1parrainage.com/detail_parrain.php?par=98906&offre=100408"
            ),
        },
    )

    evidence = asyncio.run(
        canary._public_evidence("canary_public", SimpleNamespace(), marker)
    )

    assert evidence["marker_present"] is True
    assert evidence["identity_ok"] is True
    assert evidence["public_view_type"] == "full_detail_desc_detail"
    assert "detail_parrain.php" in evidence["public_full_content_url"]


def test_public_marker_outside_full_detail_block_does_not_prove_canary(monkeypatch):
    marker = "AUTOFRESH_1P_HEADLESS_CANARY_42"
    block = (
        f"<p>{canary.EXPECTED_CODE} {canary.EXPECTED_LINK} "
        f"{canary.EXPECTED_REWARD}</p>"
    )
    monkeypatch.setattr(
        canary,
        "fetch_public_full_view",
        lambda _plan: {
            "detail_html": f"<script>{marker}</script><div>{block}</div>",
            "block": block,
            "detail_url": (
                "https://www.1parrainage.com/detail_parrain.php?par=98906&offre=100408"
            ),
        },
    )

    evidence = asyncio.run(
        canary._public_evidence("canary_public", SimpleNamespace(), marker)
    )

    assert evidence["marker_present"] is False
    assert evidence["identity_ok"] is True


class _AccountReadPage:
    async def evaluate(self, _script, _arg):
        return (
            f"<p>{canary.EXPECTED_CODE} {canary.EXPECTED_LINK} "
            f"{canary.EXPECTED_REWARD}</p>"
        )

    async def wait_for_timeout(self, _milliseconds):
        return None


def test_account_read_pins_source_and_idempotent_ckeditor_form(monkeypatch):
    source = (
        f"<p>{canary.EXPECTED_CODE} {canary.EXPECTED_LINK} "
        f"{canary.EXPECTED_REWARD}</p>"
    )
    reads = iter([source, source + "\n", source + "\n"])
    monkeypatch.setattr(canary, "_ck_ready", lambda _page: asyncio.sleep(0, result=True))
    monkeypatch.setattr(canary, "_ck_get", lambda _page: asyncio.sleep(0, result=next(reads)))
    monkeypatch.setattr(
        canary, "_ck_set", lambda *_args: asyncio.sleep(0, result={"ok": True})
    )

    body, evidence = asyncio.run(
        canary._read_account(_AccountReadPage(), "before_account", "ABSENT_MARKER")
    )

    assert body == source
    assert evidence["body_len"] == len(source)
    assert evidence["normalized_body_len"] == len(source) + 1
    assert evidence["normalization_added_terminal_lf"] is True
    assert evidence["normalization_idempotent"] is True


def test_account_read_fails_closed_on_unstable_ckeditor_normalization(monkeypatch):
    source = (
        f"<p>{canary.EXPECTED_CODE} {canary.EXPECTED_LINK} "
        f"{canary.EXPECTED_REWARD}</p>"
    )
    reads = iter([source, source + "\n", source + "\n\n"])
    monkeypatch.setattr(canary, "_ck_ready", lambda _page: asyncio.sleep(0, result=True))
    monkeypatch.setattr(canary, "_ck_get", lambda _page: asyncio.sleep(0, result=next(reads)))
    monkeypatch.setattr(
        canary, "_ck_set", lambda *_args: asyncio.sleep(0, result={"ok": True})
    )

    with pytest.raises(RuntimeError, match="CKEDITOR_NORMALIZATION_UNSTABLE"):
        asyncio.run(
            canary._read_account(
                _AccountReadPage(), "before_account", "ABSENT_MARKER"
            )
        )


def test_run_probe_executes_canary_then_exact_rollback(monkeypatch):
    original = (
        f"<p>{canary.EXPECTED_CODE} {canary.EXPECTED_LINK} "
        f"{canary.EXPECTED_REWARD}</p>"
    )
    marked = original + "<p>AUTOFRESH_1P_HEADLESS_CANARY_42</p>"
    plan = SimpleNamespace(
        structure_preserved=True,
        changed_fields={},
        variables={
            "personal_code": canary.EXPECTED_CODE,
            "personal_link": canary.EXPECTED_LINK,
            "referee_reward": canary.EXPECTED_REWARD,
        },
        edit_url="https://www.1parrainage.com/espace_parrain/parrainages/edit/2541207/",
        announcement_url="https://www.1parrainage.com/list#id=100408",
        platform_offer_id="100408",
    )
    fake_pw = _FakePlaywright()
    reads = iter(
        [
            (original, _stable_account_evidence("before_account", original, "AUTOFRESH_1P_HEADLESS_CANARY_42")),
            (marked, _stable_account_evidence("canary_account", marked, "AUTOFRESH_1P_HEADLESS_CANARY_42")),
            (marked, _stable_account_evidence("pre_rollback_account", marked, "AUTOFRESH_1P_HEADLESS_CANARY_42")),
            (original, _stable_account_evidence("rollback_account", original, "AUTOFRESH_1P_HEADLESS_CANARY_42")),
        ]
    )
    public = iter(
        [
            {"identity_ok": True, "marker_present": False},
            {"identity_ok": True, "marker_present": True},
            {"identity_ok": True, "marker_present": False},
        ]
    )
    set_bodies = []
    clicks = []

    monkeypatch.setattr(canary, "build_write_plan", lambda *_args: plan)
    monkeypatch.setattr(
        canary,
        "guard_live_evidence_probe",
        lambda *_args, **_kwargs: {"ok": True, "lock": True},
    )
    monkeypatch.setattr(canary, "_login", lambda *_args: asyncio.sleep(0))
    monkeypatch.setattr(canary, "_resolve_edit_url", lambda *_args: asyncio.sleep(0, result=plan.edit_url))
    monkeypatch.setattr(canary, "_detect_challenge", lambda *_args: asyncio.sleep(0))
    monkeypatch.setattr(canary, "_read_account", lambda *_args: asyncio.sleep(0, result=next(reads)))
    monkeypatch.setattr(canary, "_poll_public", lambda *_args, **_kwargs: asyncio.sleep(0, result=next(public)))

    async def fake_set(_page, body, *_args, **_kwargs):
        set_bodies.append(body)
        return {"identity_ok": True, "marker_present": "AUTOFRESH_1P_HEADLESS_CANARY_42" in body}

    async def fake_click(_page):
        clicks.append(True)
        return {"clicked": True, "label": "Envoyer"}

    monkeypatch.setattr(canary, "_set_body_without_save", fake_set)
    monkeypatch.setattr(canary, "_click_save_once", fake_click)
    monkeypatch.setattr(canary._bumper(), "new_context", lambda _browser: asyncio.sleep(0, result=fake_pw.browser.context))

    import playwright.async_api

    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: fake_pw)
    report = {"gh_run_id": "42"}

    assert asyncio.run(canary._run_probe(report)) is True
    assert len(clicks) == 2
    assert set_bodies == [
        original + '\n<p data-autofresh-canary="1parrainage-headless">AUTOFRESH_1P_HEADLESS_CANARY_42</p>',
        original,
    ]
    assert report["canary_ok"] is True
    assert report["rollback_ok"] is True
    assert fake_pw.browser.context.page.viewport_sizes == [
        {"width": 1280, "height": 720}
    ]


def test_canary_click_failure_still_attempts_rollback_and_never_proves(monkeypatch):
    original = (
        f"<p>{canary.EXPECTED_CODE} {canary.EXPECTED_LINK} "
        f"{canary.EXPECTED_REWARD}</p>"
    )
    marked = original + "<p>AUTOFRESH_1P_HEADLESS_CANARY_43</p>"
    plan = SimpleNamespace(
        structure_preserved=True,
        changed_fields={},
        variables={
            "personal_code": canary.EXPECTED_CODE,
            "personal_link": canary.EXPECTED_LINK,
            "referee_reward": canary.EXPECTED_REWARD,
        },
        edit_url="https://www.1parrainage.com/espace_parrain/parrainages/edit/2541207/",
        announcement_url="https://www.1parrainage.com/list#id=100408",
        platform_offer_id="100408",
    )
    fake_pw = _FakePlaywright()
    reads = iter(
        [
            (original, _stable_account_evidence("before_account", original, "AUTOFRESH_1P_HEADLESS_CANARY_43")),
            (marked, _stable_account_evidence("pre_rollback_account", marked, "AUTOFRESH_1P_HEADLESS_CANARY_43")),
            (original, _stable_account_evidence("rollback_account", original, "AUTOFRESH_1P_HEADLESS_CANARY_43")),
        ]
    )
    public = iter(
        [
            {"identity_ok": True, "marker_present": False},
            {"identity_ok": True, "marker_present": False},
        ]
    )
    clicks = 0

    monkeypatch.setattr(canary, "build_write_plan", lambda *_args: plan)
    monkeypatch.setattr(canary, "guard_live_evidence_probe", lambda *_args, **_kwargs: {"ok": True, "lock": True})
    monkeypatch.setattr(canary, "_login", lambda *_args: asyncio.sleep(0))
    monkeypatch.setattr(canary, "_resolve_edit_url", lambda *_args: asyncio.sleep(0, result=plan.edit_url))
    monkeypatch.setattr(canary, "_detect_challenge", lambda *_args: asyncio.sleep(0))
    monkeypatch.setattr(canary, "_read_account", lambda *_args: asyncio.sleep(0, result=next(reads)))
    monkeypatch.setattr(canary, "_poll_public", lambda *_args, **_kwargs: asyncio.sleep(0, result=next(public)))
    monkeypatch.setattr(canary, "_set_body_without_save", lambda *_args, **_kwargs: asyncio.sleep(0, result={"identity_ok": True}))

    async def fail_then_rollback(_page):
        nonlocal clicks
        clicks += 1
        if clicks == 1:
            raise RuntimeError("submit transport failed")
        return {"clicked": True, "label": "Envoyer"}

    monkeypatch.setattr(canary, "_click_save_once", fail_then_rollback)
    monkeypatch.setattr(canary._bumper(), "new_context", lambda _browser: asyncio.sleep(0, result=fake_pw.browser.context))

    import playwright.async_api

    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: fake_pw)
    report = {"gh_run_id": "43"}

    assert asyncio.run(canary._run_probe(report)) is False
    assert clicks == 2
    assert report["rollback_attempted"] is True
    assert report["rollback_ok"] is True
    assert report["canary_ok"] is False


def test_static_preflight_refuses_business_diff_and_identity_drift():
    good = SimpleNamespace(
        structure_preserved=True,
        changed_fields={},
        variables={
            "personal_code": canary.EXPECTED_CODE,
            "personal_link": canary.EXPECTED_LINK,
            "referee_reward": canary.EXPECTED_REWARD,
        },
        edit_url="https://www.1parrainage.com/espace_parrain/parrainages/edit/2541207/",
        announcement_url="https://www.1parrainage.com/list#id=100408",
        platform_offer_id="100408",
    )
    canary._static_preflight(good)

    with_diff = SimpleNamespace(**{**vars(good), "changed_fields": {"personal_link": {}}})
    try:
        canary._static_preflight(with_diff)
    except RuntimeError as exc:
        assert "REAL_SAFE_DIFF_PRESENT" in str(exc)
    else:
        raise AssertionError("business diff must abort the evidence probe")

    drift = SimpleNamespace(
        **{
            **vars(good),
            "variables": {**good.variables, "personal_link": "https://wrong.invalid"},
        }
    )
    try:
        canary._static_preflight(drift)
    except RuntimeError as exc:
        assert "canonical values drifted" in str(exc)
    else:
        raise AssertionError("identity drift must abort the evidence probe")


def test_failed_attempt_keeps_not_run_and_success_only_adds_headless_proof(
    tmp_path, monkeypatch
):
    status = tmp_path / "platform-write-status.json"
    _status(status)
    monkeypatch.setattr(ws, "STATUS_PATH", status)
    report = {
        "gh_run_id": "123",
        "finished_at": "2026-08-20T00:00:00+00:00",
        "canary_ok": False,
        "rollback_ok": True,
        "save_attempts": 2,
        "error": "canary verify failed",
        "checks": {},
        "phases": {},
    }

    canary._record_status(report, False)
    failed = json.loads(status.read_text(encoding="utf-8"))["platforms"]["1parrainage"]
    assert failed["status"] == "WRITE_VERIFIED"
    assert failed["gh_headless_save"] == "NOT_RUN"

    report.update(
        {
            "canary_ok": True,
            "rollback_ok": True,
            "error": None,
            "checks": {"complete": True},
            "phases": {
                "before_account": {"body_sha256": "same"},
                "rollback_account": {"body_sha256": "same"},
            },
        }
    )
    canary._record_status(report, True)
    proven = json.loads(status.read_text(encoding="utf-8"))["platforms"]["1parrainage"]
    assert proven["status"] == "WRITE_VERIFIED"
    assert proven["gh_headless_save"] == "PROVEN"
    assert proven["headless_evidence"]["account_before_sha256"] == "same"


def test_missing_runtime_authorization_never_touches_status(tmp_path, monkeypatch):
    status = tmp_path / "platform-write-status.json"
    report_path = tmp_path / "report.json"
    _status(status)
    before = status.read_bytes()
    monkeypatch.setattr(ws, "STATUS_PATH", status)
    monkeypatch.setattr(canary, "REPORT_PATH", report_path)
    monkeypatch.setattr(sys, "argv", ["canary_write_1parrainage.py"])

    assert canary.main() == 1
    assert status.read_bytes() == before
    assert json.loads(report_path.read_text(encoding="utf-8"))["live_write_authorized"] is False


def test_workflow_is_manual_confirmed_serialized_and_persists_failure_evidence():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "WRITE_1P_CANARY_ROLLBACK" in workflow
    assert "CONFIRMATION: ${{ github.event.inputs.confirmation }}" in workflow
    assert 'if [ "${{ github.event.inputs.confirmation }}"' not in workflow
    assert "cancel-in-progress: false" in workflow
    assert "group: parrainage-bumper-super" in workflow
    assert "tools/canary_write_1parrainage.py --execute --force" in workflow
    assert "if: always()" in workflow
    assert "gh_headless_save remains NOT_RUN" in workflow
    assert 'UNSTAGED="$(git diff --name-only)"' in workflow
    assert 'if [ "$UNSTAGED" != "data/audit/events.jsonl" ]' in workflow
    assert "git stash push" in workflow
    assert "git pull --rebase origin main" in workflow
