"""Deduplication must survive a fresh runner filesystem.

data/notifications/ is gitignored, so every GitHub Actions run starts with an
empty workspace. Without restored state the 24 h TTL on a ReferralCode.tv
external_blocker would reset on every run and a 5-hourly cron would report the
same known gate five times a day.

These tests prove the two halves of the mechanism:

* the dedup state is a *portable file* — a brand new, otherwise empty workspace
  that receives only dedup.json refuses the duplicate, and releases it once the
  TTL has elapsed (runs A / B / C below);
* the production workflows actually restore that file before the job and save it
  afterwards, with a rolling key.

What these tests do NOT claim: they do not execute GitHub Actions. The cache
round-trip itself is asserted at the workflow-definition level, not observed on
real runners.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from lib import notify

WORKFLOW_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
REGISTRY = json.loads(
    (Path(__file__).resolve().parents[1] / "data" / "workflow-registry.json").read_text(
        encoding="utf-8"
    )
)

RCTV_EVENT = dict(
    platform="referralcode-tv",
    block_reason="cloudflare_turnstile_challenge",
    pc_required=True,
)


class _Runner:
    """A fresh, isolated workspace — exactly what a new GitHub runner gets."""

    def __init__(self, tmp_path: Path, name: str, monkeypatch):
        self.root = tmp_path / name / "data" / "notifications"
        self.root.mkdir(parents=True)
        self.monkeypatch = monkeypatch

    def __enter__(self):
        self.monkeypatch.setattr(notify, "NOTIFY_DIR", self.root)
        self.monkeypatch.setattr(notify, "OUTBOX_PATH", self.root / "outbox.jsonl")
        self.monkeypatch.setattr(notify, "DEDUP_PATH", self.root / "dedup.json")
        return self

    def __exit__(self, *_exc):
        return False

    # --- what actions/cache moves between runs ---
    def export_dedup(self) -> str | None:
        p = self.root / "dedup.json"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def restore_dedup(self, blob: str | None) -> None:
        if blob is not None:
            (self.root / "dedup.json").write_text(blob, encoding="utf-8")

    def emit_blocker(self):
        return notify.emit("HUMAN_REQUIRED", notify.EVENT_EXTERNAL_BLOCKER, **RCTV_EVENT)

    def outbox_len(self) -> int:
        p = self.root / "outbox.jsonl"
        if not p.exists():
            return 0
        return len([l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()])


def _age_dedup(blob: str, seconds: float) -> str:
    """Rewind every dedup timestamp, simulating the passage of time."""
    state = json.loads(blob)
    shifted = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return json.dumps({k: shifted.isoformat() for k in state})


def test_run_a_emits_then_run_b_on_a_new_runner_refuses_the_duplicate(tmp_path, monkeypatch):
    """A: emitted. B: brand new workspace, <24 h, same event → refused."""
    with _Runner(tmp_path, "run_a", monkeypatch) as a:
        assert a.emit_blocker() is not None, "run A must emit the first time"
        assert a.outbox_len() == 1
        carried = a.export_dedup()
    assert carried, "run A must leave dedup state for the cache to carry"

    with _Runner(tmp_path, "run_b", monkeypatch) as b:
        assert b.outbox_len() == 0, "run B starts on an empty filesystem"
        b.restore_dedup(carried)  # what actions/cache/restore does
        assert b.emit_blocker() is None, "run B must refuse the duplicate inside the TTL"
        assert b.outbox_len() == 0


def test_run_c_after_the_ttl_is_allowed_again(tmp_path, monkeypatch):
    """C: brand new workspace, >24 h later → the event is allowed through."""
    with _Runner(tmp_path, "run_a2", monkeypatch) as a:
        a.emit_blocker()
        carried = a.export_dedup()

    ttl = notify.dedup_ttl_seconds(notify.EVENT_EXTERNAL_BLOCKER)
    aged = _age_dedup(carried, ttl + 60)

    with _Runner(tmp_path, "run_c", monkeypatch) as c:
        c.restore_dedup(aged)
        assert c.emit_blocker() is not None, "past the TTL the event must be reportable again"
        assert c.outbox_len() == 1


def test_without_restored_state_a_new_runner_would_re_emit(tmp_path, monkeypatch):
    """The regression this fixes: no restore → the TTL resets every run."""
    with _Runner(tmp_path, "cold_a", monkeypatch) as a:
        assert a.emit_blocker() is not None
    with _Runner(tmp_path, "cold_b", monkeypatch) as b:
        assert b.emit_blocker() is not None, (
            "documents WHY the cache is required: a cold runner has no memory"
        )


def test_a_five_hourly_cron_reports_a_known_blocker_once_a_day(tmp_path, monkeypatch):
    """Five runs across 24 h, carrying state like the cache does → one report."""
    carried = None
    emitted = 0
    for i in range(5):  # 0h, 5h, 10h, 15h, 20h
        with _Runner(tmp_path, f"tick_{i}", monkeypatch) as r:
            r.restore_dedup(carried)
            if r.emit_blocker() is not None:
                emitted += 1
            carried = r.export_dedup()
    assert emitted == 1, f"expected exactly one report per day, got {emitted}"


def test_dedup_state_never_contains_a_secret(tmp_path, monkeypatch):
    """The cache is shared state: it must hold hashes, never payloads."""
    with _Runner(tmp_path, "scrub", monkeypatch) as r:
        notify.emit(
            "ERROR",
            notify.EVENT_WORKFLOW_ERROR,
            platform="x",
            block_reason="password=hunter2 token=abcdef",
        )
        blob = r.export_dedup() or ""
    assert "hunter2" not in blob
    assert "abcdef" not in blob
    for key in json.loads(blob):
        assert len(key) == 32 and all(c in "0123456789abcdef" for c in key)


# -- the workflows actually move that file between runs ------------------------
PRODUCTION = sorted(
    name for name, meta in REGISTRY["workflows"].items() if meta["class"].startswith("PRODUCTION")
)


def _steps(name: str) -> list[dict]:
    data = yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))
    return [s for job in data["jobs"].values() for s in (job.get("steps") or [])]


KEY_TEMPLATE = "autofresh-notify-dedup-${{ github.workflow }}-${{ github.run_id }}"
RESTORE_PREFIX = "autofresh-notify-dedup-${{ github.workflow }}-"

# A single brace is NOT a GitHub Actions expression: `${ github.run_id }` is
# emitted literally. A save key built that way is identical on every run and can
# never match the restore prefix of the next one, so the cross-runner dedup is
# silently not wired at all. This exact bug shipped in the first version of these
# workflows -- Python's str.format() collapsed `{{` into `{` in the save/upload
# template -- and the original test missed it because it only inspected the
# RESTORE key's run_id.
MALFORMED = ("${ github.workflow }", "${ github.run_id }", "${ github.")


@pytest.mark.parametrize("name", PRODUCTION)
def test_production_workflow_restores_and_saves_dedup_state(name):
    steps = _steps(name)
    restore = [s for s in steps if str(s.get("uses", "")).startswith("actions/cache/restore")]
    save = [s for s in steps if str(s.get("uses", "")).startswith("actions/cache/save")]
    assert restore, f"{name} never restores dedup state — its TTL would reset every run"
    assert save, f"{name} never saves dedup state"

    r_with = restore[0].get("with") or {}
    s_with = save[0].get("with") or {}
    assert r_with.get("path") == "data/notifications/dedup.json"
    assert s_with.get("path") == "data/notifications/dedup.json"
    # Must still run on failure. A workflow may additionally guard on the file
    # existing (hashFiles) so a cycle that legitimately produced no event does
    # not log a recurring "path does not exist" warning -- that is a noise fix,
    # not a weakening: the condition still starts with always().
    save_if = str(save[0].get("if") or "")
    assert save_if.startswith("always()"), (
        f"{name} must save dedup state even on failure (if: {save_if!r})"
    )


@pytest.mark.parametrize("name", PRODUCTION)
def test_restore_key_is_the_exact_rolling_template(name):
    r_with = (
        [s for s in _steps(name) if str(s.get("uses", "")).startswith("actions/cache/restore")][0]
        .get("with")
        or {}
    )
    key = r_with["key"]
    assert "${{ github.workflow }}" in key, f"{name}: restore key lacks the workflow expression"
    assert "${{ github.run_id }}" in key, f"{name}: restore key lacks the run_id expression"
    assert key == KEY_TEMPLATE, f"{name}: restore key is {key!r}"


@pytest.mark.parametrize("name", PRODUCTION)
def test_save_key_is_the_exact_rolling_template(name):
    """The half that was broken: a malformed save key silently disables dedup."""
    s_with = (
        [s for s in _steps(name) if str(s.get("uses", "")).startswith("actions/cache/save")][0]
        .get("with")
        or {}
    )
    key = s_with["key"]
    assert "${{ github.workflow }}" in key, f"{name}: save key lacks the workflow expression"
    assert "${{ github.run_id }}" in key, f"{name}: save key lacks the run_id expression"
    assert key == KEY_TEMPLATE, f"{name}: save key is {key!r}"


@pytest.mark.parametrize("name", PRODUCTION)
def test_save_key_equals_restore_key(name):
    steps = _steps(name)
    r = [s for s in steps if str(s.get("uses", "")).startswith("actions/cache/restore")][0]
    sv = [s for s in steps if str(s.get("uses", "")).startswith("actions/cache/save")][0]
    assert (sv.get("with") or {})["key"] == (r.get("with") or {})["key"], (
        f"{name}: save and restore keys diverge, so a run can never reload its own entry"
    )


@pytest.mark.parametrize("name", PRODUCTION)
def test_restore_keys_is_a_coherent_prefix_of_the_key(name):
    r_with = (
        [s for s in _steps(name) if str(s.get("uses", "")).startswith("actions/cache/restore")][0]
        .get("with")
        or {}
    )
    prefix = r_with["restore-keys"].strip()
    assert prefix == RESTORE_PREFIX, f"{name}: restore-keys is {prefix!r}"
    assert r_with["key"].startswith(prefix), (
        f"{name}: restore-keys is not a prefix of key — the fallback can never match"
    )
    assert prefix.endswith("-")


@pytest.mark.parametrize("name", sorted(p.name for p in WORKFLOW_DIR.glob("*.yml")))
def test_no_workflow_contains_a_malformed_github_expression(name):
    """`${ github.x }` is a literal string, not an expression — in ANY workflow."""
    raw = (WORKFLOW_DIR / name).read_text(encoding="utf-8")
    for bad in MALFORMED:
        assert bad not in raw, f"{name} contains the malformed expression {bad!r}"


@pytest.mark.parametrize("name", PRODUCTION)
def test_cache_keys_carry_no_single_brace_expression(name):
    for step in _steps(name):
        if not str(step.get("uses", "")).startswith("actions/cache/"):
            continue
        for field in ("key", "restore-keys"):
            value = (step.get("with") or {}).get(field)
            if not value:
                continue
            for bad in MALFORMED:
                assert bad not in value, f"{name}.{field} contains {bad!r}"


@pytest.mark.parametrize("name", PRODUCTION)
def test_dedup_cache_steps_are_fail_open(name):
    """BEST_EFFORT: a cache miss or outage must never fail the business job."""
    for s in _steps(name):
        if str(s.get("uses", "")).startswith("actions/cache/"):
            assert s.get("continue-on-error") is True, f"{name}: {s.get('name')}"


@pytest.mark.parametrize("name", PRODUCTION)
def test_dedup_restore_happens_before_the_job_does_any_work(name):
    steps = _steps(name)
    labels = [s.get("name") or s.get("uses") for s in steps]
    restore = labels.index("Restore notification dedup state")
    save = labels.index("Save notification dedup state")
    assert restore < save
    # Nothing that could emit an event may run before the state is restored.
    assert all("python" not in str(s.get("run", "")) for s in steps[:restore]), name


def test_dedup_cache_scope_is_per_workflow_and_documented():
    """Scope is deliberately per-workflow; the race is documented as fail-safe."""
    raw = (WORKFLOW_DIR / "bump_referralcode_tv.yml").read_text(encoding="utf-8")
    assert "autofresh-notify-dedup-${{ github.workflow }}-" in raw
    assert "Scope: per workflow, not global" in raw
    assert "never a suppressed one" in raw
