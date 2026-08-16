# AGENTS.md — parrainage-bumper (Autofresh)

Autofresh is a **GitHub-Actions-hosted backend** for referral-code listings across 7
platforms. It has no Telegram bot of its own — **Hermes is the operator interface**.
This file is what Hermes should load to know how to route a Telegram message here.

## When a message is an Autofresh command

Pattern: `<program> <verb> [value]`, optionally `<program> <platform> <verb> [value]`.

- `<program>`: an offer slug/name known to Autofresh (e.g. `Kraken`, `Revolut`). If
  unsure whether a name is a known program, it is safe to dispatch a `status`/
  `overrides` query anyway — those are read-only and Autofresh returns
  `unknown_program` cleanly if it doesn't recognize it. Never guess-invent a value
  for a `set` verb.
- `<verb>`: `status`/`statut` | `overrides` | `divergences` | `plateformes` |
  `code` | `lien`/`link` | `gain filleul`/`récompense filleul` |
  `gain parrain`/`récompense parrain` | `conditions` | `dépôt minimum` |
  `dépense minimum` | `minimum de trade` | `nombre de transactions` | `délai` |
  `expiration` | `type de récompense` | `titre` |
  `supprimer`/`retirer`/`effacer override <champ>`.
- `<platform>` (optional): `Super-Parrain` | `Parrainage.co` | `Code-Parrainage` |
  `1Parrainage` | `ReferralCodes` | `ReferralCode.tv` | `ReferralDrop`. Omitted =
  global (all platforms), unless a platform-specific override exists (it always wins).

**Positive examples** (→ Autofresh): `Kraken status`, `Kraken statut`,
`Kraken overrides`, `Kraken divergences`, `Kraken plateformes`,
`Kraken code ABC123`, `Kraken gain filleul 20 €`, `Kraken Super-Parrain gain filleul
25 €`, `Kraken supprimer override gain filleul`.

**Negative examples** (→ normal conversation, do NOT dispatch): `Quel est le cours de
Kraken ?`, `C'est quoi Kraken ?`, any message about the Kraken *exchange* with no
verb from the list above. The verb is what discriminates — a bare program/brand
name alone is never enough.

## Global French UX meta-commands (no program token)

These are read-only, dispatch through the exact same recipe below (same command
string, verbatim, as `command`), never persist anything, and never invoke a
writer regardless of `run_writers`:

- `Autofresh` / `Autofresh aide` / `Aide Autofresh` / `Autofresh commandes` →
  the full French command menu (`human_summary` is the menu text — relay it
  as-is, do not paraphrase).
- `Autofresh exemples` → a short list of concrete example commands.
- `Autofresh plateformes` → the real per-platform capability table (WRITE_VERIFIED
  / CANARY_READY / etc., translated to French), generated live from backend
  state, not a static doc — always current.

If a message names a known program plus a bare ambiguous reward word (e.g.
`Kraken récompense`, no `filleul`/`parrain` qualifier), Autofresh replies with a
French clarifying question (not a technical `unknown_field` error) — relay
`errors[0].detail` verbatim as the Telegram reply in that case.

## Dispatch recipe (exact — verified against the live workflow file)

```
repository = rararerezeze-cyber/parrainage-bumper
workflow   = hermes_operator.yml
ref        = main   ← NEVER an old feature branch (e.g. autofresh/phase2b-kraken-capture
                       still exists on the remote but is stale/pre-hardening; dispatching
                       against it silently skips every security fix on main)
inputs:
  command:        the raw message text, verbatim (e.g. "Kraken status")
  requester:      an identity string for you, e.g. "hermes"
  correlation_id: a fresh unique id per Telegram update (e.g. the update_id)
  run_writers:    "false" by default — ALWAYS, unless the human operator explicitly
                   confirmed a live platform write in this same exchange. status/
                   overrides/list commands never consult this flag regardless.
```

`POST /repos/{repository}/actions/workflows/{workflow}/dispatches` with a token that
has `actions:write` on this repo. That call returns 204 with **no run id** — list
runs for the workflow shortly after and match by timing/inputs (the workflow has
`concurrency: cancel-in-progress: false`, so concurrent dispatches queue, not race).

## Reading the result

Download artifact `hermes-autofresh-result` from the completed run → read
`hermes-last-result.json` inside it. Use `result.human_summary` as the Telegram
reply (or `result.errors` if `result.ok` is false). Never fabricate a reply from
assumed values — always come from that JSON.

## Security (non-negotiable)

- Never print, log, or echo `AUTOFRESH_OPERATOR_TOKEN` / `HERMES_SHARED_TOKEN`
  anywhere Telegram-visible or in your own logs — value only ever goes into the
  dispatch call's Authorization header / `requester.token` field.
- `run_writers` defaults to `false`; only flip it on explicit human confirmation.
- If the GitHub dispatch or run fails, tell the user plainly what failed (timeout,
  auth, run failure) — never claim a result you didn't actually read back from JSON.

Full request/response schema: `docs/hermes-autofresh-interface.md` in this repo.
