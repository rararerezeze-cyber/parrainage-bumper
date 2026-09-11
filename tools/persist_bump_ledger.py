"""Persist only a completed bump's ledger entry from a clean, current checkout.

The bumper checkout may contain auth/audit evidence. Never discard, stash or
commit those unrelated files just to publish the ledger. A rejected push fails
the job; it never causes another platform bump or a blind retry.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import bump_autres_schedule as schedule


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, encoding="utf-8",
        stderr=subprocess.STDOUT,
    ).strip()


def _persist_state(
    root: Path,
    sid: str,
    *,
    completed_sites: list[str],
    retryable_sites: list[str],
) -> None:
    if not sid or "\n" in sid or "\r" in sid:
        raise ValueError("invalid slot id")
    _git(root, "fetch", "origin", "main")
    with tempfile.TemporaryDirectory(prefix="autofresh-ledger-") as tmp:
        checkout = Path(tmp) / "checkout"
        _git(root, "worktree", "add", "--detach", str(checkout), "origin/main")
        old_path = schedule.LEDGER_PATH
        try:
            schedule.LEDGER_PATH = checkout / "data/bump-autres-dispatch-ledger.json"
            before = schedule._load_ledger()
            schedule.record_site_outcomes(
                sid,
                completed_sites=completed_sites,
                retryable_sites=retryable_sites,
            )
            after = schedule._load_ledger()
            if after == before:
                return
            _git(checkout, "add", "data/bump-autres-dispatch-ledger.json")
            _git(
                checkout,
                "-c", "user.name=autofresh-bot",
                "-c", "user.email=autofresh-bot@users.noreply.github.com",
                "commit", "-m", "chore(bump): record per-site slot outcome",
            )
            _git(checkout, "push", "origin", "HEAD:main")
            _git(checkout, "fetch", "origin", "main")
            durable = json.loads(
                _git(checkout, "show", "origin/main:data/bump-autres-dispatch-ledger.json")
            )
            explicit_completed = set((durable.get("site_completions") or {}).get(sid) or [])
            explicit_retryable = set((durable.get("retryable_sites") or {}).get(sid) or [])
            for site in completed_sites:
                if site not in explicit_completed:
                    raise RuntimeError("ledger post-push completion verification failed")
            if set(retryable_sites) != explicit_retryable:
                raise RuntimeError("ledger post-push retryability verification failed")
        finally:
            schedule.LEDGER_PATH = old_path
            _git(root, "worktree", "remove", str(checkout))


def persist_slot(root: Path, sid: str) -> None:
    _persist_state(
        root,
        sid,
        completed_sites=list(schedule.TARGET_SITES),
        retryable_sites=[],
    )


def persist_outcome(root: Path, sid: str, outcome_file: Path) -> None:
    payload = json.loads(outcome_file.read_text(encoding="utf-8"))
    sites = payload.get("sites") or {}
    completed = []
    retryable = []
    for site in schedule.TARGET_SITES:
        row = sites.get(site) or {}
        if row.get("status") == "success":
            completed.append(site)
        elif row.get("status") == "failed" and row.get("safe_to_retry") is True:
            retryable.append(site)
    _persist_state(
        root,
        sid,
        completed_sites=completed,
        retryable_sites=retryable,
    )


if __name__ == "__main__":
    try:
        root = Path(__file__).resolve().parents[1]
        outcome = os.environ.get("OUTCOME_FILE", "").strip()
        if outcome:
            persist_outcome(root, os.environ["SLOT_ID"], Path(outcome))
        else:
            persist_slot(root, os.environ["SLOT_ID"])
    except Exception as exc:
        from lib.notify import emit
        emit("ERROR", "workflow_error", platform="bump-autres",
             action="persist_ledger", result="UNVERIFIED",
             block_reason="Ledger persistence failed; review site logs before any replay.")
        print(f"::error::Ledger persistence failed ({type(exc).__name__}); do not replay the bump.")
        raise SystemExit(1)
    print("ledger_persisted_and_verified=true")
