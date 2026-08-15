"""Any workflow able to promote a platform to WRITE_VERIFIED must commit the
full write-status/cooldown state, or the promotion is silently discarded when
the ephemeral GitHub Actions runner is torn down.

Real incident this guards against: GH run 31724917509 (controlled_write.yml,
Poulpeo, 2026-08-13) genuinely executed login -> fill -> save -> reread_account
-> reread_public -> post_match=true and called mark_write_verified() in
process (proven by the run's own log line
"WRITE_VERIFIED super-parrain registry={'ok': True, ...}" and by
data/captures/write-super-parrain-poulpeo.json). But the workflow's commit
step only staged data/captures/write-*.json, data/platform-mappings and
data/platform-templates/super-parrain -- it never added
data/platform-write-status.json, last_super_run.txt, data/pending_writes.json
or data/autofresh-phase.json. The promotion was correct on disk on the
runner and then vanished the moment the runner exited, so every later
canary run re-read a stale CANARY_READY status from git.

This is a plain-text check (not a YAML parser) on purpose: it must not
depend on PyYAML being present in every environment that runs this test
suite (it is not a declared dependency in requirements.txt).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"

# Anything mark_write_verified() / record_super_action_now() can durably
# change and that a later workflow run depends on reading back correctly.
REQUIRED_STATE_FILES = (
    "data/platform-write-status.json",
    "last_super_run.txt",
    "data/pending_writes.json",
    "data/autofresh-phase.json",
)

# workflow file -> exact "- name: ..." marker of its commit step. The commit
# step is the last step in both files today; if that ever changes, slicing
# to end-of-file would silently stop covering the real commit step, which is
# why each marker is asserted present first.
WORKFLOWS_WITH_SUPER_PARRAIN_WRITE_PROMOTION = {
    "controlled_write.yml": "- name: Commit post-write golden if success",
    "activation_canary.yml": "- name: Commit status if verified",
}


def _commit_step_text(workflow_name: str, marker: str) -> str:
    text = (WORKFLOWS_DIR / workflow_name).read_text(encoding="utf-8")
    idx = text.find(marker)
    assert idx != -1, f"{workflow_name}: expected commit step {marker!r} not found"
    return text[idx:]


def test_super_parrain_write_workflows_persist_full_write_status():
    for workflow_name, marker in WORKFLOWS_WITH_SUPER_PARRAIN_WRITE_PROMOTION.items():
        step = _commit_step_text(workflow_name, marker)
        for required in REQUIRED_STATE_FILES:
            assert required in step, (
                f"{workflow_name}: commit step is missing '{required}' -- a real "
                "WRITE_VERIFIED promotion or cooldown update computed in-process "
                "by controlled_write_super_parrain.py would be silently discarded "
                "when this runner exits (see write-super-parrain-poulpeo.json "
                "incident, run 31724917509)."
            )
