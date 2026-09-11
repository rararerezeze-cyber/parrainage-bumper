# AutoFresh ↔ Slack

Slack talks directly to the GitHub Actions backend over a serverless
Cloudflare Worker. No local process and no Hermes runtime dependency are
required; normal operation works with the PC off.

```
Slack (/autofresh <command>, or a "Confirmer l'écriture" button click)
  → Cloudflare Worker (slack-worker/) — signature verification, allowlist
  → GitHub Actions workflow_dispatch (hermes_operator.yml; historical filename)
  → lib/hermes_interface.py
  → workflow "Post Slack reply" step
  → chat.postMessage back into the same Slack channel
```

The old Hermes/Telegram integration is legacy/optional. It is not part of
the production Slack control path.

## Components

| Component | Where | Role |
|---|---|---|
| `slack-worker/` | Cloudflare Worker | Verifies Slack requests, dispatches `workflow_dispatch` |
| `hermes_operator.yml` `reply_channel` input | GitHub Actions | Opt-in: when set, posts the result back to that Slack channel |
| `lib/slack_format.py` | Python | Renders the result JSON as Slack Block Kit (text/blocks separated) |
| `lib/hermes_interface.py` | Python | Unchanged operator logic (parsing, overrides, plan, writers) |

## Usage

- `/autofresh aide` — complete user-facing command menu.
- `/autofresh Kraken statut` — read-only status, replies in the same channel.
- `/autofresh Kraken valeurs` — current personalized values/overrides.
- `/autofresh bump` — read-only status of randomized bump schedules.
- `/autofresh plateformes` — current capability/status of the seven platforms.
- `/autofresh Kraken gain filleul 20 €` — persists the personalized value;
  if a compatible platform has a real pending difference, the Slack reply
  includes a **Confirmer l'écriture** button.
- Clicking that button re-dispatches the identical command with
  `run_writers=true` — this is the only way a real platform write happens
  from Slack. No write ever happens from the slash command alone.
The visible command vocabulary is French and avoids backend terms such as
`pending_update`, `SAFE_DIFF`, `writer` or snake_case field names.

The current production channel is `#autofresh`; the backend remains
channel-agnostic and replies to the channel id received from Slack.

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
dispatch with duplicate suppression. Production Slack delivery and command
round-trips have been observed live; real platform writing still requires a
genuine SAFE_DIFF, explicit confirmation, and backend post-verification.


## Bump scheduler reliability

Cloudflare Cron wakes `bump_autres_scheduler.yml` at minutes 03/18/33/48.
This is only a reliable poll/wake-up layer. It does not replace or alter
the persisted five random daily slots and cannot turn actual site access
into a fixed schedule. The duplicate native GitHub cron was removed on
2026-09-11; Cloudflare is the sole routine wake-up source.
