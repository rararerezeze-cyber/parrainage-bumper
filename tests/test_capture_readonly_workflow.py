"""capture_readonly.yml must persist data/mapping-candidates.json across
ephemeral runners, or the append-only candidate-observation history (see
lib/mapping_candidates.py) only ever exists for the lifetime of a single
runner: X curated -> observation Y -> runner destroyed -> Y lost, and
every subsequent run starts from a blank slate.

Real incident this file guards against: the first version of this fix put
data/mapping-candidates.json in the *same* `git add` invocation as the
always-present capture paths. `git add` aborts the ENTIRE command (exit
128) the instant any one pathspec matches nothing -- verified empirically
in an isolated throwaway repo, not assumed -- and stages nothing at all,
not even the paths that did exist. Since mapping-candidates.json is
legitimately absent on every run with no observed divergence (the common
case), that `|| true`-masked failure meant the commit step silently staged
NOTHING on essentially every run, including genuine capture changes to
data/platform-mappings etc. Caught before any real capture ran.
"""
from __future__ import annotations

import os
import shutil
import subprocess
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


def test_mapping_candidates_added_separately_and_conditionally():
    assert "data/mapping-candidates.json" in COMMIT_STEP
    # The `git add` invocation for the always-present paths (identified by
    # mentioning data/platform-mappings, always present post-checkout)
    # must NOT also mention data/mapping-candidates.json -- that's exactly
    # the bug: one absent pathspec aborts the whole invocation and stages
    # nothing, even for the other paths, and mapping-candidates.json is
    # legitimately absent on any run with no observed divergence.
    always_present_add = next(
        line for line in COMMIT_STEP.splitlines() if "git add data/platform-mappings" in line
    )
    assert "data/mapping-candidates.json" not in always_present_add
    # It must instead be behind an explicit existence check.
    assert "if [ -f data/mapping-candidates.json ]; then" in COMMIT_STEP


def test_commit_is_still_conditional_on_a_real_diff():
    """The persistence fix must not bypass the existing "only commit if
    something actually changed" gate -- no unconditional commit, no
    artificial noise when nothing diverged this run."""
    assert "git diff --cached --quiet" in COMMIT_STEP
    assert "No capture files to commit" in COMMIT_STEP


def _extract_bash_body(step_text: str) -> str:
    """Pull the `run: |` block scalar body out of a step, de-indenting it
    to the block's own baseline so it can be executed directly as a bash
    script -- the real embedded logic, not a reimplementation of it."""
    lines = step_text.splitlines()
    run_idx = next(i for i, line in enumerate(lines) if line.strip() == "run: |")
    body_lines = lines[run_idx + 1 :]
    indents = [len(line) - len(line.lstrip(" ")) for line in body_lines if line.strip()]
    baseline = min(indents)
    return "\n".join(line[baseline:] if line.strip() else "" for line in body_lines)


def test_real_scenario_capture_file_changed_candidates_absent_still_staged(tmp_path):
    """The exact scenario requested: data/mapping-candidates.json is
    absent, another capture file genuinely changed, and that change must
    still end up staged and committed -- proven by actually executing the
    real bash logic extracted from the workflow file, not a rewritten copy
    of it.
    """
    if shutil.which("bash") is None:
        import pytest

        pytest.skip("bash not available in this environment")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)

    # Seed the always-present capture paths with initial, committed content.
    (repo / "data" / "platform-mappings").mkdir(parents=True)
    (repo / "data" / "platform-templates").mkdir(parents=True)
    (repo / "data" / "captures").mkdir(parents=True)
    (repo / "data" / "orphans").mkdir(parents=True)
    (repo / "data" / "platform-mappings" / "x.json").write_text('{"v": 1}', encoding="utf-8")
    (repo / "data" / "needs_canonical_data.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)

    # A real capture file changed this run.
    (repo / "data" / "platform-mappings" / "x.json").write_text('{"v": 2}', encoding="utf-8")

    # data/mapping-candidates.json is legitimately absent (no divergence
    # observed this run) -- the exact condition that broke the old logic.
    assert not (repo / "data" / "mapping-candidates.json").exists()

    script = _extract_bash_body(COMMIT_STEP)
    # GITHUB_REF_NAME-dependent pull/push at the end will harmlessly fail
    # (no real remote configured) -- irrelevant to what this test checks:
    # whether the change got staged and committed at all.
    env = os.environ.copy()
    env["GITHUB_REF_NAME"] = "main"
    subprocess.run(
        ["bash", "-c", script],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert "chore(capture): AUTH READ-ONLY account capture artifacts" in log, (
        "the genuinely changed capture file must have been committed despite "
        "mapping-candidates.json being absent"
    )
    committed = subprocess.run(
        ["git", "show", "--stat", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert "x.json" in committed


def test_real_scenario_candidates_file_present_is_also_staged(tmp_path):
    """Symmetric check: when data/mapping-candidates.json *does* exist and
    changed, it is picked up too (the conditional add is not a no-op)."""
    if shutil.which("bash") is None:
        import pytest

        pytest.skip("bash not available in this environment")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)

    (repo / "data" / "platform-mappings").mkdir(parents=True)
    (repo / "data" / "platform-templates").mkdir(parents=True)
    (repo / "data" / "captures").mkdir(parents=True)
    (repo / "data" / "orphans").mkdir(parents=True)
    (repo / "data" / "needs_canonical_data.json").write_text("{}", encoding="utf-8")
    (repo / "data" / "mapping-candidates.json").write_text('{"entries": {}}', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)

    (repo / "data" / "mapping-candidates.json").write_text(
        '{"entries": {"p:prog:fr:edit_url": {}}}', encoding="utf-8"
    )

    script = _extract_bash_body(COMMIT_STEP)
    env = os.environ.copy()
    env["GITHUB_REF_NAME"] = "main"
    subprocess.run(
        ["bash", "-c", script],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    committed = subprocess.run(
        ["git", "show", "--stat", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert "mapping-candidates.json" in committed
