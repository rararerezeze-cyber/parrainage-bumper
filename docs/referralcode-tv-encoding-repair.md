# ReferralCode.tv character repair

The live Unibet announcement (eid 23040) and authenticated edit form contain
UTF-8 bytes interpreted as Windows-1252, including undefined bytes retained as
C1 controls. The UTF-8 page footer renders correctly. Historical read-only
captures `data/captures/rctv-headed-eid.json` contain correct Unicode on
2026-08-13. They do not identify when or how the later corruption occurred.

Repair must use the current owned edit form, not a historical template or a
whole-page capture. `platforms.referralcode_tv.encoding.TextRepair.prepare`
produces a reversible title/body correction and checks the exact source before
filling. It preserves valid Unicode, ASCII, whitespace and markup. Replacement
characters are irrecoverable and require manual review. Do not reconstruct
missing text from guessed offers, prices or referral codes.

1. Inventory all live listings from the authenticated account, including both
   pages. Associate each public `__sid` with its observed edit `eid`.
2. Capture only the title, description and business form fields; never persist
   cookies, passwords, nonces or CAPTCHA tokens.
3. Prepare the correction from that exact title/description. Check that codes,
   referral links, amounts, keywords, categories, country and media are unchanged.
4. Fill only title/description. The human completes CAPTCHA and Save. No API
   replay, injected requests, CAPTCHA solver or unattended save is permitted.
5. Reload the public announcement and its edit form. Require exact corrected
   title/body and unchanged personal/custom fields before moving to another ad.
   If the site corrupts a correctly saved value again, stop and record the site
   blocker instead of repeatedly saving or changing business terms.

The normal writer remains `HUMAN_SAVE_REQUIRED`. Any corrupted rendered templates
are now reported as `encoding_corrupt` / `BLOCKED` rather than prepared as safe
content. This guard does not change scheduler, boost or other platform routes.
Historical templates and captures are left intact as evidence.
