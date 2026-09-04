import json
import subprocess
from pathlib import Path

import pytest

from tools import persist_bump_ledger as persistence
from tools import notify_slack
from lib.slack_format import guard_workflow_result, render_result


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def test_ledger_persists_with_dirty_checkout_and_new_remote_commit(tmp_path):
    remote, checkout, other = [tmp_path / x for x in ("remote.git", "checkout", "other")]
    remote.mkdir()
    git(remote, "init", "--bare", "--initial-branch=main")
    git(tmp_path, "clone", str(remote), str(checkout))
    git(checkout, "config", "user.name", "Test")
    git(checkout, "config", "user.email", "test@example.invalid")
    (checkout / "data").mkdir()
    (checkout / "data/bump-autres-dispatch-ledger.json").write_text(
        json.dumps({"version": 1, "dispatched_slot_ids": ["2026-09-03:0"]}))
    audit = checkout / "audit.txt"
    audit.write_text("original")
    git(checkout, "add", ".")
    git(checkout, "commit", "-m", "seed")
    git(checkout, "push", "origin", "main")
    git(tmp_path, "clone", str(remote), str(other))
    (other / "new.txt").write_text("parallel remote state")
    git(other, "add", ".")
    git(other, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "parallel")
    git(other, "push", "origin", "main")
    audit.write_text("uncommitted evidence")
    persistence.persist_slot(checkout, "2026-09-03:1")
    assert audit.read_text() == "uncommitted evidence"
    assert git(remote, "show", "main:audit.txt") == "original"
    assert git(remote, "show", "main:new.txt") == "parallel remote state"
    ledger = json.loads(git(remote, "show", "main:data/bump-autres-dispatch-ledger.json"))
    assert ledger["dispatched_slot_ids"] == ["2026-09-03:0", "2026-09-03:1"]
    head = git(remote, "rev-parse", "main")
    persistence.persist_slot(checkout, "2026-09-03:1")
    assert git(remote, "rev-parse", "main") == head


@pytest.mark.parametrize("state", ["failure", "cancelled", "unknown"])
def test_failed_workflow_cannot_display_success_or_confirmation(state):
    original = {"ok": True, "persist_confirmed": True, "platforms": [
        {"platform": "code-parrainage", "can_auto_write": True, "changed_fields": {"body": {}}}
    ]}
    result = guard_workflow_result(original, state)
    assert result["ok"] is False and result["persist_confirmed"] is False
    assert original["ok"] is True
    assert not any(b.get("type") == "actions" for b in render_result(result)["blocks"])


def test_notifications_filter_routine_and_scrub_credentials():
    assert notify_slack.build_payload([{"level": "INFO", "event": "no_change"}], "C1") is None
    payload = notify_slack.build_payload([
        {"level": "ERROR", "event": "workflow_error", "block_reason": "password=private"}
    ], "C1")
    assert payload and "private" not in payload["text"]
    assert payload["mrkdwn"] is False


@pytest.mark.parametrize("response, expected", [({"ok": True}, True), ({"ok": False, "error": "invalid_auth"}, False)])
def test_slack_http_200_is_not_delivery_proof(monkeypatch, response, expected):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return json.dumps(response).encode()
    monkeypatch.setattr(notify_slack.urllib.request, "urlopen", lambda *_a, **_k: Response())
    assert notify_slack.deliver({"channel": "C1", "text": "test"}, "test-token") is expected


def test_slack_network_error_does_not_retry(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("private request details")
    monkeypatch.setattr(notify_slack.urllib.request, "urlopen", fail)
    assert notify_slack.deliver({"channel": "C1", "text": "test"}, "test-token") is False
    assert calls == [1]


def test_native_super_fields_not_missing_when_absent_from_body_golden():
    from lib.operator_plan import plan_program_impact
    from platforms.super_parrain.writer import build_write_plan
    plan = plan_program_impact("kraken", platform_filter="super-parrain")
    row = plan["platforms"][0]
    native = build_write_plan(program="kraken")
    assert row["changed_fields"] == native.changed_fields


@pytest.mark.parametrize("status, changed, expected", [
    ("in_sync", {}, 0), ("error", {}, 0),
    ("pending_update", {"personal_code": {"old": "a", "new": "b"}}, 1),
])
def test_operator_pending_requires_real_plan(monkeypatch, tmp_path, status, changed, expected):
    from lib import hermes_interface as interface
    from lib import super_parrain_schedule as schedule
    from lib import operator_overrides
    monkeypatch.setenv("AUTOFRESH_ALLOW_LOCAL_OPERATOR", "1")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("AUTOFRESH_OPERATOR_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_SHARED_TOKEN", raising=False)
    overrides = tmp_path / "overrides.json"
    overrides.write_text('{"version":1,"overrides":[]}')
    monkeypatch.setattr(interface, "OPERATOR_OVERRIDES_PATH", overrides)
    monkeypatch.setattr(operator_overrides, "OPERATOR_OVERRIDES_PATH", overrides)
    monkeypatch.setattr(interface, "RESULT_PATH", tmp_path / "result.json")
    monkeypatch.setattr(interface, "plan_program_impact", lambda *_a, **_k: {
        "platforms": [{"platform": "super-parrain", "status": status,
                       "write_mode": "WRITE_VERIFIED", "changed_fields": changed}]
    })
    calls = []
    monkeypatch.setattr(schedule, "enqueue_pending", lambda *a, **k: calls.append(a))
    result = interface.run_autofresh_command("Kraken code TESTCLOSURE",
        requester={"source": "local"}, run_writers=False)
    assert result["ok"] is True
    assert len(calls) == expected


@pytest.mark.parametrize("drift", [False, True])
def test_pending_reconciliation_backups_and_refuses_drift(tmp_path, monkeypatch, drift):
    import shutil
    from types import SimpleNamespace
    from tools.reconcile_test_pending import reconcile
    root = Path(__file__).resolve().parents[1]
    paths = ["data/snapshots/20260831T104609Z/operator-overrides.json",
             "data/snapshots/20260831T104808Z/operator-overrides.json",
             "data/operator-overrides.json"]
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    target = tmp_path / "data/pending_writes.json"
    target.write_bytes((root / "data/snapshots/closure-20260904/pending_writes.json").read_bytes())
    if drift:
        data = json.loads(target.read_text())
        data["items"][1]["updated_at"] = "later-change"
        target.write_text(json.dumps(data))
    original = target.read_bytes()
    monkeypatch.setattr("platforms.super_parrain.writer.build_write_plan",
                        lambda **_: SimpleNamespace(changed_fields={}, structure_preserved=True))
    if drift:
        with pytest.raises(ValueError, match="pending changed"):
            reconcile(tmp_path, apply=True)
        assert target.read_bytes() == original
        assert not (tmp_path / "data/snapshots/closure-20260904").exists()
    else:
        report = reconcile(tmp_path, apply=True)
        assert report["platform_write_verified"] is False
        assert (tmp_path / "data/snapshots/closure-20260904/pending_writes.json").read_bytes() == original
        data = json.loads(target.read_text())
        assert data["items"][0] == json.loads(original)["items"][0]
        assert data["items"][1]["status"] == "cancelled"


def test_explicit_transport_test_uses_same_delivery_and_no_outbox(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-only")
    monkeypatch.setenv("AUTOFRESH_SLACK_CHANNEL", "C_TEST")
    def forbidden():
        raise AssertionError("transport test must not read or modify business events")
    monkeypatch.setattr(notify_slack, "read_events", forbidden)
    calls = []
    monkeypatch.setattr(notify_slack, "deliver", lambda payload, token: calls.append(payload) or True)
    assert notify_slack.main(["--test"]) == 0
    assert len(calls) == 1 and calls[0]["channel"] == "C_TEST"
    assert "test de livraison" in calls[0]["text"]


def test_transport_test_is_opt_in_and_read_only():
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/hermes_operator.yml").read_text(encoding="utf-8")
    header = text.split("notification_test:", 1)[1].split("permissions:", 1)[0]
    assert 'default: "false"' in header
    step = text.split("- name: Test Slack notification transport", 1)[1].split("- name: Post Slack reply", 1)[0]
    assert "run_writers == 'false'" in step
    assert "command == 'Kraken statut'" in step
    assert "notification_test == 'true'" in step
