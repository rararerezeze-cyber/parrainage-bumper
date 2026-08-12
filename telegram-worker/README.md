# Autofresh Telegram Worker (Cloudflare)

Serverless ingress: **no PC, no VPS**.

## Flow

```
Telegram message
  → Worker /telegram
  → allowlist TELEGRAM_ALLOWED_USER_ID
  → safe command check
  → GitHub workflow_dispatch telegram_sync.yml
  → override + multi-platform plan
  → live write only if platform WRITE_VERIFIED
  → reply via TELEGRAM_BOT_TOKEN + chat_id
```

## Secrets (wrangler secret put)

| Secret | Purpose |
|--------|---------|
| `TELEGRAM_BOT_TOKEN` | Bot API |
| `TELEGRAM_ALLOWED_USER_ID` | Single allowed Telegram user id |
| `GITHUB_TOKEN` | Fine-grained PAT: `actions:write` on this repo only |
| `GITHUB_REPO` | `owner/repo` |
| `GITHUB_REF` | Branch (e.g. `autofresh/phase2b-kraken-capture`) |

Never commit secret values.

## Deploy

```bash
cd telegram-worker
wrangler secret put TELEGRAM_BOT_TOKEN
wrangler secret put TELEGRAM_ALLOWED_USER_ID
wrangler secret put GITHUB_TOKEN
wrangler secret put GITHUB_REPO
wrangler secret put GITHUB_REF
wrangler deploy
```

## Webhook

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=https://<worker>.workers.dev/telegram"
```

## E2E checklist (no live platform write)

1. Message from allowed user → « Recu. Lancement… »
2. Message from other user → « Non autorise. »
3. GH Actions run appears for `telegram_sync`
4. `data/operator-overrides.json` updated on branch
5. Detailed Telegram reply with plan (`PLAN_ONLY` until WRITE_VERIFIED)

## Live writes

Only platforms with `status: WRITE_VERIFIED` in `data/platform-write-status.json`
receive automatic content updates from Telegram. Others stay **PLAN_ONLY**.
