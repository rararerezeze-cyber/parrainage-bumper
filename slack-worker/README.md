# autofresh-slack (Cloudflare Worker)

Serverless Slack operator front-end for Autofresh. Runs on Cloudflare's
edge — no VPS, no local process, no dependency on Hermes or on this
machine being on. Mirrors the proven shape of `telegram-worker/` (kept as
optional/legacy), adapted for Slack's request model.

## Why this exists

Historically the only operator interface was Telegram → Hermes (a local
agent process) → `hermes_operator.yml`. This worker lets Slack talk to the
same GitHub Actions backend directly, so Autofresh is fully controllable
from Slack with the PC off and Hermes never running. See
`docs/autofresh-slack-interface.md` for the full architecture.

## Endpoints

- `GET /health`
- `POST /slack/commands` — Slash command, e.g. `/autofresh Kraken statut`
- `POST /slack/interactivity` — Block Kit button clicks (write confirmation)

## Required Cloudflare secrets (`wrangler secret put NAME`)

| Secret | Purpose | Source |
|---|---|---|
| `SLACK_SIGNING_SECRET` | Verifies every incoming Slack request (HMAC + timestamp, replay-protected) | Slack app → Basic Information → App Credentials → Signing Secret |
| `GH_DISPATCH_TOKEN` | Calls GitHub's `workflow_dispatch` API | Same credential already used as `AUTOFRESH_GH_DISPATCH_TOKEN` |
| `SLACK_ALLOWED_USERS` | Comma-separated Slack user IDs allowed to operate Autofresh | Same allowlist already used by Hermes's Slack integration |

## Vars (`wrangler.toml [vars]`)

`GITHUB_REPO`, `GITHUB_REF` — already set to this repo / `main`.

## KV namespace

`IDEMPOTENCY` — short-TTL (30s) dedupe store so a Slack retry or a
double-click on the confirm button never dispatches twice. Create once:

```
wrangler kv namespace create IDEMPOTENCY
```

then paste the returned id into `wrangler.toml`.

## Slack app configuration (manual, dashboard-only — no API for this)

In the SAME Slack app already used for Hermes (reuse — do not create a
second app/bot for the same workspace):

1. **Slash Commands** → create `/autofresh` → Request URL:
   `https://<worker-subdomain>.workers.dev/slack/commands`
2. **Interactivity & Shortcuts** → turn on → Request URL:
   `https://<worker-subdomain>.workers.dev/slack/interactivity`

No new OAuth scopes are required — the worker never calls the Slack Web
API itself (the GitHub Actions workflow does, using the existing
`SLACK_BOT_TOKEN` GitHub secret, for `chat.postMessage`); the worker only
verifies inbound signatures and dispatches to GitHub.

## Deploy

```
cd slack-worker
wrangler deploy
```

## Test

```
node --test test.js
```

Pure-function tests only (signature verification against Slack's own
documented worked example, allowlist parsing, dispatch-body shape,
confirm-button payload parsing, command safety filter). No live Slack or
GitHub calls.
