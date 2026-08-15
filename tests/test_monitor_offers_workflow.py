"""monitor_offers.yml's business/status commit step must not silently
swallow a persistence failure, and must not silently stage nothing due to
an absent optional pathspec.

Plain text checks on purpose (not a YAML parser) -- consistent with
tests/test_workflow_persistence.py, tests/test_hermes_operator_workflow.py
and tests/test_capture_readonly_workflow.py.

Two real, distinct issues fixed here:
1. `git pull --rebase ... || true` / `git push || true` masked a failed
   persistence attempt -- the workflow could report success (and the
   commit step itself would not go red) even though the commit never
   reached the remote. Same class of bug already fixed in
   controlled_write.yml and hermes_operator.yml this session.
2. `git add pathA pathB ... 2>/dev/null || true` included
   data/monitor/accepted-fields.json and data/monitor/accepted-history.jsonl,
   neither of which exists yet in this repo. `git add` aborts the ENTIRE
   invocation (stages nothing at all, not even the paths that do exist and
   did change) the instant one pathspec matches nothing -- verified
   empirically in an isolated throwaway repo. `2>/dev/null || true` masked
   the resulting `fatal: pathspec ... did not match any files`, so this
   step likely staged nothing on every run, ever. Same class of bug just
   fixed in capture_readonly.yml.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "monitor_offers.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")

COMMIT_STEP = TEXT[TEXT.index("- name: Commit only on business/status change") :]


def test_commit_step_never_silently_swallows_a_failed_push():
    assert "git push || true" not in COMMIT_STEP
    assert "git pull --rebase" in COMMIT_STEP
    after_commit = COMMIT_STEP.split("git commit -m", 1)[-1]
    assert "|| true" not in after_commit, (
        "a failure after the commit (pull/push) must not be swallowed by || true"
    )
    assert "if ! git pull --rebase origin" in COMMIT_STEP
    assert "if ! git push; then" in COMMIT_STEP
    assert "exit 1" in after_commit


def test_no_retry_storm_added():
    assert COMMIT_STEP.count("git pull --rebase origin") == 1
    assert COMMIT_STEP.count("if ! git push; then") == 1


def test_git_add_no_longer_masks_a_missing_pathspec():
    """The old single `git add ... 2>/dev/null || true` line is gone --
    replaced by a per-file existence check, so a genuinely absent optional
    file can never abort staging of the files that do exist."""
    code_lines = [
        line
        for line in COMMIT_STEP.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any("2>/dev/null || true" in line for line in code_lines), (
        "the masked unconditional git add must be gone from the executable "
        "code, not merely absent from comments"
    )
    assert 'if [ -f "$f" ]; then' in COMMIT_STEP
    assert "git add \"$f\"" in COMMIT_STEP


def test_currently_absent_paths_are_covered_by_the_conditional_add():
    """Empirical, not assumed: these two paths do not exist in the repo
    right now, which is exactly the condition that broke the old
    unconditional git add."""
    for missing in ("data/monitor/accepted-fields.json", "data/monitor/accepted-history.jsonl"):
        assert not (ROOT / missing).exists(), (
            f"{missing} now exists -- if this is expected, the regression "
            "this test guards against may no longer be reproducible as "
            "written, double check the fix is still exercised"
        )
        assert missing in COMMIT_STEP
