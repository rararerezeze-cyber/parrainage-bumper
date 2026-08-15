"""hermes_operator.yml hardening -- regression tests.

Plain text checks on purpose, not a YAML parser: PyYAML is not a declared
dependency in requirements.txt, and this suite must not depend on a package
that might be absent wherever CI actually runs it (see
tests/test_workflow_persistence.py for the same reasoning).

Checks work on "code lines" (comment lines stripped) where possible, since
the fix itself is documented with explanatory comments in the workflow that
necessarily *mention* the old vulnerable patterns and can otherwise trip a
naive substring search against this test's own intent.

Original vulnerabilities, all present on the version of this workflow
audited on 2026-08-15, fixed here:
  1. workflow_dispatch inputs (command/requester/correlation_id/run_writers)
     were spliced directly into the `run:` block via a `${{ github.event.
     inputs.* }}` expression, both as bash `VAR='${{ ... }}'` (single-quote
     breakout) and, worse, inside a Python heredoc as a Python triple-quoted
     string wrapped directly around that same expression (triple-quote
     breakout / Python code injection), using an UNQUOTED heredoc delimiter
     (`<<PY`), which additionally let bash expand any literal $(...) /
     backtick command substitution in the input before Python ever ran.
  2. The assembled request dict (which embeds AUTOFRESH_OPERATOR_TOKEN /
     HERMES_SHARED_TOKEN) was printed via `print(json.dumps(req, ...))`.
  3. `run_writers` defaulted to "true".
  4. `permissions: actions: write` was granted but unused.
  5. `git pull --rebase ... || true` / `git push || true` silently
     swallowed persistence failures -- a result JSON could claim a
     mutation was applied when it was never actually pushed.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "hermes_operator.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")


def _slice_between(start_marker: str, end_marker: str | None) -> str:
    i = TEXT.index(start_marker)
    j = TEXT.index(end_marker, i) if end_marker else len(TEXT)
    return TEXT[i:j]


def _code_only(text: str) -> str:
    """Drop full-line `#` comments so checks target actual code, not the
    prose explaining what the code deliberately does NOT do anymore."""
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


RUN_STEP = _slice_between(
    "- name: Run Hermes Autofresh command", "- name: Upload Hermes JSON result"
)
COMMIT_STEP = _slice_between("- name: Commit overrides + status", None)
RUN_STEP_CODE = _code_only(RUN_STEP)
COMMIT_STEP_CODE = _code_only(COMMIT_STEP)


def test_no_raw_input_interpolation_in_run_or_python_source():
    """The four user-controlled inputs must never appear as `${{ github.event.
    inputs.X }}` inside a `run:` script body -- only inside an `env:` mapping,
    which GitHub Actions passes as an opaque process-environment string
    rather than splicing into script text.
    """
    for name in ("command", "requester", "correlation_id", "run_writers"):
        pattern = f"github.event.inputs.{name}"
        for line in TEXT.splitlines():
            if pattern not in line or line.strip().startswith("#"):
                continue
            assert ": ${{" in line and line.strip().startswith("HERMES_INPUT_"), (
                f"found raw interpolation of {pattern!r} outside an env: "
                f"mapping: {line!r}"
            )


def test_run_step_reads_inputs_only_via_env():
    required_env_vars = (
        "HERMES_INPUT_COMMAND: ${{ github.event.inputs.command }}",
        "HERMES_INPUT_REQUESTER: ${{ github.event.inputs.requester }}",
        "HERMES_INPUT_CORRELATION_ID: ${{ github.event.inputs.correlation_id }}",
        "HERMES_INPUT_RUN_WRITERS: ${{ github.event.inputs.run_writers }}",
    )
    for var in required_env_vars:
        assert var in RUN_STEP, f"missing env passthrough: {var}"

    assert 'os.environ.get("HERMES_INPUT_COMMAND"' in RUN_STEP_CODE
    assert 'os.environ.get("HERMES_INPUT_REQUESTER")' in RUN_STEP_CODE
    assert 'os.environ.get("HERMES_INPUT_CORRELATION_ID")' in RUN_STEP_CODE
    assert 'os.environ.get("HERMES_INPUT_RUN_WRITERS")' in RUN_STEP_CODE
    # No code line (env: mapping lines excepted -- checked above) may embed
    # the raw input expression outside of that mapping.
    for line in RUN_STEP_CODE.splitlines():
        if "${{ github.event.inputs." in line:
            assert line.strip().startswith("HERMES_INPUT_"), (
                f"raw input expression outside the env: mapping: {line!r}"
            )


def test_python_heredoc_uses_quoted_delimiter():
    """Unquoted `<<PY` lets bash expand $(...) / `...` inside the heredoc
    body before Python runs. Must be the quoted form so the body is passed
    through completely literally.
    """
    assert "<<'PY'" in RUN_STEP
    assert "<<PY" not in RUN_STEP.replace("<<'PY'", "")


def test_token_is_never_printed_unsanitized():
    assert 'safe["requester"]["token"]' in RUN_STEP_CODE, (
        "expected a sanitized copy of the request dict before printing"
    )
    assert "print(json.dumps(req," not in RUN_STEP_CODE
    assert "print(json.dumps(safe," in RUN_STEP_CODE


def test_run_writers_defaults_to_false():
    inputs_block = _slice_between("run_writers:", "permissions:")
    assert 'default: "false"' in inputs_block
    assert "run_writers_raw.strip().lower()" in RUN_STEP_CODE


def test_permissions_are_minimal():
    perms_code = _code_only(_slice_between("permissions:", "concurrency:"))
    assert "contents: write" in perms_code
    assert "actions: write" not in perms_code


def test_commit_step_never_silently_swallows_a_failed_push():
    assert "git push || true" not in COMMIT_STEP_CODE
    assert "git pull --rebase" in COMMIT_STEP_CODE
    after_commit = COMMIT_STEP_CODE.split("git commit -m", 1)[-1]
    assert "|| true" not in after_commit, (
        "a failure after the commit (pull/push) must not be swallowed by || true"
    )
    assert "if ! git pull --rebase origin" in COMMIT_STEP_CODE
    assert "if ! git push; then" in COMMIT_STEP_CODE
    assert "exit 1" in after_commit
    assert 'echo "git_persisted=true"' in COMMIT_STEP_CODE


def test_no_retry_storm_added():
    """Exactly one pull attempt and one push attempt -- no loop."""
    assert COMMIT_STEP_CODE.count("git pull --rebase origin") == 1
    assert COMMIT_STEP_CODE.count("if ! git push; then") == 1
