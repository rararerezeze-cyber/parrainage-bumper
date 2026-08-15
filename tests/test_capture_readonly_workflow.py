"""capture_readonly.yml must persist data/mapping-candidates.json across
ephemeral runners, or the append-only candidate-observation history (see
lib/mapping_candidates.py) only ever exists for the lifetime of a single
runner: X curated -> observation Y -> runner destroyed -> Y lost, and
every subsequent run starts from a blank slate.

Plain text checks on purpose (not a YAML parser) -- consistent with
tests/test_workflow_persistence.py and tests/test_hermes_operator_workflow.py.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "capture_readonly.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")


def _slice_between(start_marker: str, end_marker: str | None) -> str:
    i = TEXT.index(start_marker)
    j = TEXT.index(end_marker, i) if end_marker else len(TEXT)
    return TEXT[i:j]


UPLOAD_STEP = _slice_between(
    "- name: Upload capture artifacts", "- name: Commit capture results on branch"
)
COMMIT_STEP = _slice_between("- name: Commit capture results on branch", None)


def test_mapping_candidates_included_in_artifact_upload():
    assert "data/mapping-candidates.json" in UPLOAD_STEP


def test_mapping_candidates_included_in_git_add():
    assert "data/mapping-candidates.json" in COMMIT_STEP
    # Specifically inside the `git add` invocation, not just anywhere
    # (e.g. a comment) in the step.
    add_line = next(
        line for line in COMMIT_STEP.splitlines() if line.strip().startswith("git add ")
    )
    assert "data/mapping-candidates.json" in add_line


def test_commit_is_still_conditional_on_a_real_diff():
    """The persistence fix must not bypass the existing "only commit if
    something actually changed" gate -- no unconditional commit, no
    artificial noise when nothing diverged this run."""
    assert "git diff --cached --quiet" in COMMIT_STEP
    assert "No capture files to commit" in COMMIT_STEP
