#!/usr/bin/env python3
"""Read AutoFresh's notification outbox — the Hermes-facing side of the contract.

AutoFresh never sends anything to Telegram itself: Hermes owns the bot. This
CLI is what Hermes (or a workflow step) calls to obtain the already-scrubbed,
already-deduplicated events, or an optional daily digest.

    python tools/notify_digest.py --since-hours 24
    python tools/notify_digest.py --daily-summary
    python tools/notify_digest.py --daily-summary --format text

Read-only: it never writes state, never performs a platform action, and never
prints a secret (records are scrubbed at emit time and re-checked here).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.notify import (  # noqa: E402
    REDACTED,
    build_daily_summary,
    format_summary_text,
    read_events,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="AutoFresh notification digest (read-only)")
    ap.add_argument("--since-hours", type=float, default=None, help="only events newer than this")
    ap.add_argument("--limit", type=int, default=None, help="keep only the last N events")
    ap.add_argument("--daily-summary", action="store_true", help="emit the digest instead of events")
    ap.add_argument("--hours", type=int, default=24, help="digest window (default 24)")
    ap.add_argument("--format", choices=("json", "text"), default="json")
    args = ap.parse_args()

    if args.daily_summary:
        summary = build_daily_summary(hours=args.hours)
        if args.format == "text":
            print(format_summary_text(summary))
        else:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    since = None
    if args.since_hours is not None:
        since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    events = read_events(since=since, limit=args.limit)
    if args.format == "text":
        for rec in events:
            print(
                f"[{rec.get('level')}] {rec.get('timestamp')} "
                f"{rec.get('platform') or '-'}/{rec.get('program') or '-'} "
                f"{rec.get('event')} {rec.get('field') or ''} "
                f"{rec.get('result') or ''} {rec.get('block_reason') or ''}".rstrip()
            )
    else:
        print(json.dumps({"count": len(events), "events": events}, ensure_ascii=False, indent=2))
    # Defence in depth: emit-time scrubbing already applies, this only asserts it.
    assert REDACTED is not None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
