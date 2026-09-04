# Autofresh ↔ Slack (Hermes-independent operator interface)

Slack talks to the same GitHub Actions backend Hermes always used —
directly, over HTTPS, via a serverless Cloudflare Worker. No local process,
no Hermes runtime dependency, works with the PC off.

```
Slack (/autofresh <command>, or a "Confirmer l'écriture" button click)
  → Cloudflare Worker (slack-worker/) — signature verification, allowlist
  → GitHub Actions workflow_dispatch (hermes_operator.yml)
  → lib/hermes_interface.py (same backend Hermes/Telegram always used)
  → hermes_operator.yml's "Post Slack reply" step
  → chat.postMessage back into the same Slack channel
```

Hermes/Telegram's own path (`docs/hermes-autofresh-interface.md`) is
completely unchanged and still works — this is an additional front door
onto the same backend, not a replacement of the interface contract.

## Components

| Component | Where | Role |
|---|---|---|
| `slack-worker/` | Cloudflare Worker | Verifies Slack requests, dispatches `workflow_dispatch` |
| `hermes_operator.yml` `reply_channel` input | GitHub Actions | Opt-in: when set, posts the result back to that Slack channel |
| `lib/slack_format.py` | Python | Renders the result JSON as Slack Block Kit (text/blocks separated) |
| `lib/hermes_interface.py` | Python | Unchanged operator logic (parsing, overrides, plan, writers) |

## Usage

- `/autofresh Kraken statut` — read-only status, replies in the same channel.
- `/autofresh Kraken gain filleul 20 €` — persists the override immediately
  (as it always has); if any platform becomes writer-eligible
  (`can_auto_write`, real pending diff), the Slack reply includes a
  **Confirmer l'écriture** button.
- Clicking that button re-dispatches the identical command with
  `run_writers=true` — this is the only way a real platform write happens
  from Slack. No write ever happens from the slash command alone.
- `/autofresh aide` (or empty text) — command help, no dispatch.

Works identically in any channel the Slack app is invited to — nothing is
hardcoded to a specific channel name or id.

## Security

- Every inbound request (slash command, button click) is verified against
  `SLACK_SIGNING_SECRET` (HMAC, 5-minute replay window) before anything
  else runs.
- Only Slack user IDs in `SLACK_ALLOWED_USERS` (the same allowlist already
  used by Hermes — one shared source of truth) may dispatch anything.
- `run_writers` defaults to `false` on every slash-command dispatch;
  `true` only ever comes from the signed, allowlisted button-click path.
- A short-TTL (60s) KV idempotency store prevents a Slack retry or a
  double-click from dispatching twice.
- The Worker never sees `SLACK_BOT_TOKEN` — only the GitHub Actions step
  (server-side, using an existing GitHub secret) calls the Slack Web API.
- No new Slack OAuth scopes: the reused app already has `chat:write`.

## Deploying / redeploying the Worker

```
cd slack-worker
npx wrangler deploy
```

Required one-time setup (see `slack-worker/README.md` for the full list):
create the `IDEMPOTENCY` KV namespace, set the three Worker secrets, add
the `SLACK_BOT_TOKEN` GitHub Actions secret, and register the Worker's two
endpoint URLs as the Slack app's Slash Command and Interactivity Request
URLs (dashboard-only — Slack has no API for this).

## Failure behavior

- Wrong/missing Slack signature → `401`, nothing dispatched.
- Not on the allowlist → rejected before any GitHub call.
- GitHub dispatch fails → Slack sees an immediate ephemeral error; no
  silent success is ever reported.
- `SLACK_BOT_TOKEN` missing or the Slack API call fails → the workflow
  step logs a `::warning::` and exits `0` (never fails the whole run over
  a notification-only concern) — the mutation itself (if any) already
  succeeded or failed independently, and its true outcome is always in the
  workflow's own logs/artifact.

## Production notifications and closure validation (2026-09-04)

Scheduled workflows now deliver the sanitized outbox directly to Slack through
`tools/notify_slack.py`. Configure the repository variable
`AUTOFRESH_SLACK_CHANNEL` only for the operator's confirmed channel; reuse the
existing `SLACK_BOT_TOKEN`. Artifacts and per-workflow deduplication are retained.
This is separate from command replies, whose destination is `reply_channel`.

Tests cover a signed HTTP request through the actual Worker handler, unauthorized
and invalid-signature rejection, an unarmed preview, and a single confirmed
dispatch with duplicate suppression. GitHub and Slack are mocked in those tests:
they are not live E2E proof. A real read-only `/autofresh Kraken statut` and its
matching GitHub run/Slack response are the final operator-control check. Real
platform writing still requires a genuine SAFE_DIFF and explicit confirmation.
