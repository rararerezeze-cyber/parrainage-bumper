"""Persist only a completed bump's ledger entry from a clean, current checkout.

The bumper checkout may contain auth/audit evidence. Never discard, stash or
commit those unrelated files just to publish the ledger. A rejected push fails
the job; it never causes another platform bump or a blind retry.
"""
from __future__ import annotations

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


def persist_slot(root: Path, sid: str) -> None:
    if not sid or "\n" in sid or "\r" in sid:
        raise ValueError("invalid slot id")
    _git(root, "fetch", "origin", "main")
    # Worktree uses the runner's existing Git credentials, not a copied token.
    with tempfile.TemporaryDirectory(prefix="autofresh-ledger-") as tmp:
        checkout = Path(tmp) / "checkout"
        _git(root, "worktree", "add", "--detach", str(checkout), "origin/main")
        old_path = schedule.LEDGER_PATH
        try:
            schedule.LEDGER_PATH = checkout / "data/bump-autres-dispatch-ledger.json"
            if schedule.is_slot_already_processed(sid):
                return
            schedule.record_slot_processed(sid)
            _git(checkout, "add", "data/bump-autres-dispatch-ledger.json")
            _git(checkout, "-c", "user.name=autofresh-bot", "-c",
                 "user.email=autofresh-bot@users.noreply.github.com", "commit",
                 "-m", "chore(bump): record completed slot")
            _git(checkout, "push", "origin", "HEAD:main")
            _git(checkout, "fetch", "origin", "main")
            import json
            durable = json.loads(_git(checkout, "show",
                                     "origin/main:data/bump-autres-dispatch-ledger.json"))
            if sid not in durable["dispatched_slot_ids"]:
                raise RuntimeError("ledger post-push verification failed")
        finally:
            schedule.LEDGER_PATH = old_path
            _git(root, "worktree", "remove", str(checkout))


if __name__ == "__main__":
    try:
        persist_slot(Path(__file__).resolve().parents[1], os.environ["SLOT_ID"])
    except Exception as exc:
        from lib.notify import emit
        emit("ERROR", "workflow_error", platform="bump-autres",
             action="persist_ledger", result="UNVERIFIED",
             block_reason="Ledger persistence failed; review site logs before any replay.")
        print(f"::error::Ledger persistence failed ({type(exc).__name__}); do not replay the bump.")
        raise SystemExit(1)
    print("ledger_persisted_and_verified=true")
