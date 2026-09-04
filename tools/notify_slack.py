"""Deliver the sanitized runtime outbox to Slack without a local relay.

No retries: a timeout may mean Slack accepted the message. The artifact remains
the evidence source. Delivery errors are explicit but never undo business work.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.notify import read_events, build_event, should_notify


def build_payload(events: list[dict], channel: str) -> dict | None:
    lines = []
    for raw in events:
        level, event = raw.get("level", ""), raw.get("event", "")
        if not should_notify(event, level):
            continue
        safe = build_event(level, event, **{
            k: v for k, v in raw.items() if k not in {"level", "event"}
        })
        line = " | ".join(str(safe.get(k) or "") for k in (
            "level", "platform", "program", "event", "result", "block_reason"
        )).strip(" |")
        # Plain text only: no user mentions or automatic links.
        lines.append(line[:500])
    if not lines:
        return None
    text = "AutoFresh — événements\n" + "\n".join(lines[:40])
    if len(lines) > 40:
        text += f"\n+{len(lines) - 40} événements dans l’archive du workflow."
    return {"channel": channel, "text": text, "mrkdwn": False,
            "unfurl_links": False, "unfurl_media": False}


def deliver(payload: dict, token: str) -> bool:
    request = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            accepted = json.loads(response.read().decode("utf-8")).get("ok") is True
    except Exception:
        accepted = False
    print("slack_notifications_delivered=" + str(accepted).lower())
    if not accepted:
        print("::warning::Slack delivery unverified; inspect the notification artifact. No automatic retry.")
    return accepted


def main() -> int:
    events = read_events()
    if not events:
        print("notification_outbox_empty=true")
        return 0
    channel = os.environ.get("AUTOFRESH_SLACK_CHANNEL", "").strip()
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not channel or not token:
        print("::warning::Slack notification configuration missing; delivery NOT VERIFIED.")
        return 1
    payload = build_payload(events, channel)
    return 0 if payload is None or deliver(payload, token) else 1


if __name__ == "__main__":
    raise SystemExit(main())
