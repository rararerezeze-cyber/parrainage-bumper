"""Cancel the identified, restored 2026-08-31 Slack test pending; never a write proof.

Dry-run by default. Refuse any drift from the exact incident and preserve a
byte-for-byte backup before the sole local state mutation.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def reconcile(root: Path, *, apply: bool = False) -> dict:
    def read(relative):
        return json.loads((root / relative).read_text(encoding="utf-8"))
    def code(data):
        found = [item for item in data["overrides"] if item.get("id") == "global:kraken:personal_code"]
        if len(found) != 1:
            raise ValueError("ambiguous override evidence")
        return found[0]["value"]
    before = code(read("data/snapshots/20260831T104609Z/operator-overrides.json"))
    during = code(read("data/snapshots/20260831T104808Z/operator-overrides.json"))
    current = code(read("data/operator-overrides.json"))
    if not (before == current and during == "TESTE2E999" and during != before):
        raise ValueError("override restoration evidence does not match")
    from platforms.super_parrain.writer import build_write_plan
    plan = build_write_plan(program="kraken")
    if plan.changed_fields or not plan.structure_preserved:
        raise ValueError("current native write plan is not unchanged")
    path = root / "data/pending_writes.json"
    data = read("data/pending_writes.json")
    items = [i for i in data["items"] if i.get("key") == "super-parrain:kraken:fr"
             and i.get("status") == "pending"]
    if len(items) != 1:
        raise ValueError("expected exactly one incident pending")
    item = items[0]
    if (item.get("reason") != "hermes_personal_code"
            or item.get("created_at") != "2026-08-31T10:46:09.633496+00:00"
            or item.get("updated_at") != "2026-08-31T10:48:08.059102+00:00"):
        raise ValueError("pending changed since incident; refusing cancellation")
    backup = root / "data/snapshots/closure-20260904/pending_writes.json"
    report = {"action": "cancel_restored_test_pending", "applied": apply,
              "platform_write_verified": False, "key": item["key"],
              "reason": "test override restored; no native field diff; not a successful write"}
    if apply:
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open("xb") as fh:
            fh.write(path.read_bytes())
        now = datetime.now(timezone.utc).isoformat()
        item.update(status="cancelled", cancelled_at=now,
                    cancellation_reason=report["reason"],
                    cancellation_evidence="data/snapshots/closure-20260904/pending_writes.json")
        data["updated_at"] = now
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if read("data/pending_writes.json") != data:
            raise RuntimeError("pending reread mismatch")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(reconcile(Path(__file__).resolve().parents[1], apply=args.apply)))
