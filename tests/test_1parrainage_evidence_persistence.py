from __future__ import annotations

from pathlib import Path

import pytest

from tools import check_1parrainage_evidence_paths as policy


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "canary_write_1parrainage.yml"


def test_circuit_breaker_is_durable_evidence_and_audit_is_transient():
    assert "data/circuit-breakers.json" in policy.PERSISTED_PATHS
    assert "data/audit/events.jsonl" not in policy.PERSISTED_PATHS
    assert policy.TRANSIENT_PATHS == ("data/audit/events.jsonl",)


def test_exact_transient_path_is_accepted():
    assert policy.validate_unstaged_paths([])["unexpected"] == []
    result = policy.validate_unstaged_paths(["data/audit/events.jsonl"])
    assert result["transient"] == ["data/audit/events.jsonl"]


def test_third_unexpected_tracked_path_is_refused():
    with pytest.raises(ValueError, match="data/pending_writes.json"):
        policy.validate_unstaged_paths(
            ["data/audit/events.jsonl", "data/pending_writes.json"]
        )


def test_workflow_stages_all_durable_paths_then_validates_before_commit():
    source = WORKFLOW.read_text(encoding="utf-8")
    staging = source[source.index("for p in") : source.index("if git diff --cached")]
    assert "data/captures/canary-1parrainage-kraken.json" in staging
    assert "data/platform-write-status.json" in staging
    assert "data/circuit-breakers.json" in staging
    assert "tools/check_1parrainage_evidence_paths.py" in staging
    assert "data/audit/events.jsonl" in staging
    assert source.index("tools/check_1parrainage_evidence_paths.py") < source.index(
        'git commit -m "chore(canary): 1parrainage headless rollback evidence"'
    )
