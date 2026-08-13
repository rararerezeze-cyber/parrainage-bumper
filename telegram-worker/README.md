# telegram-worker (OPTIONAL / TEST ONLY)

**Not part of the product architecture.**

Autofresh is a **backend for Hermes**. Hermes owns the user-facing Telegram bot.

```
Telegram → Hermes → Autofresh (hermes_operator / hermes_autofresh) → JSON → Hermes → Telegram
```

This Cloudflare worker is kept only as an optional experiment/test ingress.
Do **not** deploy it as the production Autofresh bot.
Do **not** configure BotFather webhooks for Autofresh in production.

## Product interface

See:

- `docs/hermes-autofresh-interface.md`
- `tools/hermes_autofresh.py`
- `.github/workflows/hermes_operator.yml`

## If you still want this worker for isolated tests

Secrets and deploy steps are local/test only. Prefer Hermes integration for anything user-facing.
