# AutoFresh handoff

Current repository handoff for `rararerezeze-cyber/parrainage-bumper` on `main`.
Do not reconstruct current state from older run artifacts. The authority order is:

1. `data/platform-write-status.json` for platform capability and proof status;
2. `data/autofresh-phase.json` for runtime phase and authorization flags;
3. `data/pending_writes.json` for unresolved write lifecycle;
4. the most recent named capture referenced by the status record.

## Current platform state

| Platform | Status | Durable constraint |
|---|---|---|
| Super-Parrain | `WRITE_VERIFIED` | Latest real fused cycle is GitHub run `32369146793`: 39/39 saves and `POST_VERIFY=PASS`; Kraken `post_match`, `exact_body_match`, and `immutable_ok` are true. The already-authenticated-session retry branch was not exercised and remains not live-proven. |
| Parrainage.co | `WRITE_VERIFIED` | GitHub headless canary and rollback proven. |
| Code-Parrainage | `WRITE_VERIFIED` | GitHub headless canary and rollback proven. |
| 1Parrainage | `WRITE_VERIFIED` | Headed save proof is authoritative. GitHub headless login/edit are proven; the complete unattended save chain remains `NOT_RUN`. Run `32416840267` made zero actual Save clicks: the old harness counted two requested phases and triggered a false rollback. Read-only run `32419280860` proved the server had exhausted its two-edits-per-day quota and omitted Save from the edit form. |
| ReferralCodes | `CANARY_READY` | `NEVER_AUTO_COMMIT`. Validate-only evidence exists; Agent Import is not a documented update path for an existing referral. |
| ReferralCode.tv | `WRITE_VERIFIED` | `HUMAN_SAVE_REQUIRED` because save requires CAPTCHA. No bypass. |
| ReferralDrop | `AUTH_BLOCKED_MANUAL` | No official public write path. No auth bypass. |

The current count is 5/7 `WRITE_VERIFIED`. This count does not mean all five are
eligible for unattended mutation; platform-specific constraints above still apply.

## 1Parrainage headless evidence gap

The repository contains a dedicated manual workflow:
`.github/workflows/canary_write_1parrainage.yml`.

It is intentionally separate from the normal canary promotion ladder because
1Parrainage is already `WRITE_VERIFIED`; the missing fact is only
`gh_headless_save`. Before a browser starts, it requires all of the following:

- manual `workflow_dispatch` confirmation `WRITE_1P_CANARY_ROLLBACK`;
- current platform status still `WRITE_VERIFIED`;
- `gh_headless_save` still exactly `NOT_RUN`;
- closed circuit and exclusive canary lock;
- no business `SAFE_DIFF` and exact Kraken identity invariants.

If authorized, the workflow uses one browser session and at most two actual Save
clicks: append a unique body-only marker, verify it in the account and public
full-detail `#desc_detail` view, restore the exact original CKEditor HTML, then
verify both the fresh server-backed source hash and the deterministic
CKEditor-normalized hash plus public marker removal. Any incomplete chain keeps
`gh_headless_save=NOT_RUN`. A hard runner termination can interrupt cleanup, so
the persisted capture, never the workflow attempt alone, is the authority.

The harness records requested phases, resolved controls, click starts, and click
completions separately. A missing or ambiguous control is zero actual Save clicks
and cannot trigger rollback. Once a click invocation starts, persistence is
possible and rollback becomes mandatory. The Save selector remains scoped to the
single edit form and exact `Envoyer`/`Valider` controls. The proven daily-quota
message is classified as `prewrite_blocked: daily_edit_quota_exhausted`; it is not
treated as selector drift and does not justify broadening the selector.

Run `32410815698` reached both real Saves and restored the public Kraken content,
but the old verifier read only the explicitly truncated list excerpt and the
immediate CKEditor reread differed by one byte. Read-only diagnostic run
`32414266066` proved the full public body is available at
`/detail_parrain.php?par=98906&offre=100408#desc_detail`. It also proved that the
fresh account body is still exactly 1062 bytes with SHA-256 `ad2a57ac...`, while
`CKEDITOR.setData(current)->getData()` adds only one terminal LF and is then
byte-idempotent at 1063 bytes / `48d1ad78...`. The canary verifier uses both
exact representations; semantic equivalence is not substituted for exactness.

Do not dispatch this workflow without explicit same-exchange operator approval:
it performs two real platform saves even though the final business content is
restored.

## ReferralCodes conclusion

GitHub run `32265735051` performed Validate only and never clicked Commit.
Official documentation now closes the useful read-only uncertainty:

- Agent Import is presented as bulk upload followed by Validate and Commit;
- existing referrals are changed with the account Edit action;
- only one referral per shop/app is allowed, duplicates are rejected, and
  repeated duplicate submissions may be treated as spam.

Therefore Commit must not be used speculatively as an update mechanism. Keep the
existing native Kraken `$200` listing and `NEVER_AUTO_COMMIT`. Evidence and source
URLs are stored in `data/captures/referralcodes-commit-semantics.json`.

## Runtime ownership and pauses

Hermes owns the Telegram operator interface. This repository exposes the
AutoFresh command workflow and result contract but does not contain the Hermes
plugin runtime. `/autofresh_valeur` remains an operator pause / known issue in
Hermes and must not be resumed or represented as locally guarded here.

Do not change `monitor_auto_accept`. Monitor decisions remain observation-only
unless the existing explicit operator flow authorizes otherwise.

## Remaining gaps

1. 1Parrainage: wait for the platform's daily edit quota to reset, then obtain a
   fresh explicit authorization for one final GitHub headless marker/full-detail-
   post-verify/exact-rollback workflow. Until its persisted proof passes,
   unattended save remains `NOT_RUN`.
2. ReferralCodes: durable product limitation, not an invitation to test Commit.
   Revisit only if an official existing-referral Edit path or update API becomes
   available.
3. ReferralCode.tv: human CAPTCHA save remains required.
4. ReferralDrop: manual authentication/write-path blocker remains.
5. Telegram mutation bug: separate Hermes-owned chantier after 1Parrainage.

## Validation

Run the complete repository suite with:

```powershell
python -m pytest tests -q
```

No cancelled, skipped, dry-run, or attempted workflow is proof of a platform
write. Status promotion requires the persisted post-verification invariants.
