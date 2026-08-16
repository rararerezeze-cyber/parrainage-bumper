"""bump_super_parrain.yml's commit step must not silently mask a failed
persistence, and must not silently stage nothing due to an absent
optional pathspec/glob.

Real incident this file guards against: the historical bumper stopped
running on 2026-08-12 (WRITE_VERIFIED but bumper authorization never
explicitly granted -- by design, see lib.super_parrain_schedule) and this
went unnoticed until 2026-08-16 when the operator checked the live site.
Two independent, unrelated bugs were found and fixed alongside restoring
authorization:
  1. `git add ... 2>/dev/null || true` included paths only written on an
     actual cycle run (super-parrain-last-cycle.json,
     write-super-parrain-kraken.json) and a glob
     (super-parrain.*.json) -- on a skip/CANARY_PENDING run those are
     legitimately absent, and one absent pathspec aborts the ENTIRE `git
     add` invocation, staging nothing at all (same class of bug already
     fixed in capture_readonly.yml, monitor_offers.yml,
     hermes_operator.yml this session).
  2. `git pull --rebase origin main || true` / `git push origin HEAD:main
     || true` masked a failed persistence -- a run could finish green
     while its results never reached the remote.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "bump_super_parrain.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


COMMIT_STEP = TEXT[TEXT.index("- name: Commit results") :]
COMMIT_STEP_CODE = _code_only(COMMIT_STEP)
DECIDE_STEP = TEXT[TEXT.index("- name: Decide") : TEXT.index("- name: Super-Parrain action")]


def test_git_add_no_longer_masks_a_missing_pathspec_or_glob():
    assert not any(
        "2>/dev/null || true" in line for line in COMMIT_STEP_CODE.splitlines()
    ), "the masked unconditional git add must be gone from the executable code"
    assert 'if [ -f "$p" ]; then' in COMMIT_STEP_CODE
    assert 'git add "$p"' in COMMIT_STEP_CODE


def test_glob_paths_use_a_loop_with_existence_check_not_bare_expansion():
    """A bare unmatched glob (e.g. super-parrain.*.json with zero matches)
    passed directly to `git add` is a literal string that also aborts the
    whole invocation -- must be handled via a loop with [ -e ]."""
    assert "for f in data/platform-mappings/super-parrain.*.json; do" in COMMIT_STEP_CODE
    assert "[ -e \"$f\" ] && git add \"$f\"" in COMMIT_STEP_CODE


def test_optional_cycle_only_paths_are_conditional():
    for p in ("data/captures/super-parrain-last-cycle.json", "data/captures/write-super-parrain-kraken.json"):
        assert p in COMMIT_STEP_CODE
    # Confirm they are inside the `for p in ...; do if [ -f "$p" ]` loop,
    # not a bare unconditional git add line.
    always_add_lines = [
        line for line in COMMIT_STEP_CODE.splitlines() if line.strip().startswith("git add ")
    ]
    for line in always_add_lines:
        assert "super-parrain-last-cycle.json" not in line
        assert "write-super-parrain-kraken.json" not in line


def test_commit_step_never_silently_swallows_a_failed_push():
    after_commit = COMMIT_STEP_CODE.split("git commit -m", 1)[-1]
    assert "|| true" not in after_commit, (
        "a failure after the commit (pull/push) must not be swallowed by || true"
    )
    assert "if ! git pull --rebase origin main; then" in COMMIT_STEP_CODE
    assert "if ! git push origin HEAD:main; then" in COMMIT_STEP_CODE
    assert "exit 1" in after_commit


def test_no_retry_storm_added():
    assert COMMIT_STEP_CODE.count("git pull --rebase origin main") == 1
    assert COMMIT_STEP_CODE.count("if ! git push origin HEAD:main; then") == 1


def test_last_super_run_still_never_committed_from_canary_pending_path():
    """Pre-existing safety invariant must survive the refactor unchanged."""
    assert 'runtime_mode }}" != "CANARY_PENDING"' in COMMIT_STEP_CODE
    idx = COMMIT_STEP_CODE.index('runtime_mode }}" != "CANARY_PENDING"')
    tail = COMMIT_STEP_CODE[idx:]
    assert "last_super_run.txt" in tail


def test_bumper_suspended_emits_a_visible_warning_annotation():
    """The suspended state must surface where an operator will actually
    see it (Actions run annotations), not only in a JSON artifact field
    someone has to think to go read."""
    assert "::warning::" in DECIDE_STEP
    assert "WRITE_VERIFIED_BUMPER_SUSPENDED" in DECIDE_STEP
    assert "authorize_historical_bumper" in DECIDE_STEP
