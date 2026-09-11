/**
 * Pure functions for the Autofresh Slack Cloudflare Worker.
 *
 * No network, no KV, no env access here on purpose -- worker.js wires
 * these into the actual fetch handler; this file is what test.js exercises
 * directly under `node --test`.
 */

/** Constant-time-ish string compare (Slack's own reference examples use
 * the same length-check-then-XOR-fold pattern; the length check itself is
 * not hidden, only the content comparison is). */
export function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}

/** Slack request signature: v0=HMAC_SHA256(signing_secret, "v0:{ts}:{rawBody}"). */
export async function computeSlackSignature(signingSecret, timestamp, rawBody) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(signingSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sigBase = `v0:${timestamp}:${rawBody}`;
  const sigBuffer = await crypto.subtle.sign("HMAC", key, enc.encode(sigBase));
  const hex = [...new Uint8Array(sigBuffer)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `v0=${hex}`;
}

export async function verifySlackRequest({ signingSecret, timestamp, signature, rawBody, nowSeconds }) {
  if (!signingSecret || !timestamp || !signature) return { ok: false, reason: "missing_headers" };
  const ts = Number(timestamp);
  if (!Number.isFinite(ts)) return { ok: false, reason: "invalid_timestamp" };
  const now = nowSeconds ?? Math.floor(Date.now() / 1000);
  if (Math.abs(now - ts) > 300) return { ok: false, reason: "stale_timestamp" };
  const expected = await computeSlackSignature(signingSecret, timestamp, rawBody);
  if (!timingSafeEqual(expected, signature)) return { ok: false, reason: "bad_signature" };
  return { ok: true };
}

/** "U123,U456" -> Set{"U123","U456"}. Tolerant of spaces/empty entries. */
export function parseAllowedUsers(csv) {
  return new Set(
    String(csv || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
  );
}

export function isUserAllowed(userId, allowedSet) {
  if (!allowedSet || allowedSet.size === 0) return false;
  return allowedSet.has(String(userId || ""));
}

export function clip(s, n) {
  s = String(s ?? "");
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}

export function helpText() {
  return (
    "*AutoFresh — aide*\n" +
    "\n*Consultation*\n" +
    "• `/autofresh Kraken statut`\n" +
    "• `/autofresh Kraken valeurs`\n" +
    "• `/autofresh Kraken divergences`\n" +
    "• `/autofresh Kraken plateformes`\n" +
    "• `/autofresh plateformes`\n" +
    "• `/autofresh bump`\n" +
    "• `/autofresh exemples`\n" +
    "\n*Modifier une valeur*\n" +
    "• `/autofresh Kraken code ABC123`\n" +
    "• `/autofresh Kraken lien https://…`\n" +
    "• `/autofresh Kraken gain filleul 20 €`\n" +
    "• `/autofresh Kraken gain parrain 20 €`\n" +
    "• `/autofresh Kraken conditions <texte>`\n" +
    "• `/autofresh Kraken dépôt minimum <valeur>`\n" +
    "• `/autofresh Kraken dépense minimum <valeur>`\n" +
    "• `/autofresh Kraken minimum de trade <valeur>`\n" +
    "• `/autofresh Kraken nombre de transactions <valeur>`\n" +
    "• `/autofresh Kraken délai <valeur>`\n" +
    "• `/autofresh Kraken expiration <valeur>`\n" +
    "• `/autofresh Kraken type de récompense <valeur>`\n" +
    "• `/autofresh Kraken titre <valeur>`\n" +
    "\n*Cibler une plateforme*\n" +
    "• `/autofresh Kraken Super-Parrain gain filleul 25 €`\n" +
    "• `/autofresh Kraken Parrainage.co code ABC123`\n" +
    "\n*Supprimer une valeur personnalisée*\n" +
    "• `/autofresh Kraken supprimer code`\n" +
    "• `/autofresh Kraken supprimer lien`\n" +
    "• `/autofresh Kraken supprimer gain filleul`\n" +
    "• `/autofresh Kraken supprimer conditions`\n" +
    "• `/autofresh Kraken Super-Parrain supprimer gain filleul`\n" +
    "\nVariantes acceptées : `statut/status/état`, `valeurs/overrides/modifications`, " +
    "`supprimer/retirer/effacer`, `lien/link`.\n" +
    "\nToute mise à jour réelle d'un site compatible nécessite ensuite le bouton *Confirmer l'écriture*."
  );
}


export function normalizeSlashCommandText(text) {
  const raw = String(text || "").trim();
  const folded = raw.toLocaleLowerCase("fr-FR");
  if (folded === "plateformes" || folded === "plateforme") return "Autofresh plateformes";
  if (folded === "exemples" || folded === "exemple") return "Autofresh exemples";
  if (folded === "bump" || folded === "bumps") return "Autofresh bump";
  return raw;
}


/** Build the GitHub workflow_dispatch request body for hermes_operator.yml.
 * `runWriters` is always boolean-coerced to the literal strings "true"/
 * "false" the workflow's own inputs expect. */
export function buildDispatchBody({ ref, command, requester, correlationId, runWriters, replyChannel }) {
  return {
    ref: ref || "main",
    inputs: {
      command: String(command || ""),
      requester: String(requester || "slack"),
      correlation_id: String(correlationId || ""),
      run_writers: runWriters ? "true" : "false",
      reply_channel: String(replyChannel || ""),
    },
  };
}

export function dispatchUrl(repo, workflow) {
  return `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`;
}

/** Parse a button's `value` field (JSON produced by lib.slack_format's
 * _confirm_button_block on the Python side). Never throws -- returns null
 * on anything malformed so the caller can fail closed. */
export function parseConfirmValue(raw) {
  try {
    const v = JSON.parse(raw);
    if (!v || typeof v !== "object" || typeof v.command !== "string" || !v.command.trim()) {
      return null;
    }
    return v;
  } catch {
    return null;
  }
}

/** Slash-command text safety pre-filter, mirroring telegram-worker's
 * isSafeCommand -- shell metacharacters / injection payloads are rejected
 * before ever reaching GitHub Actions (defense in depth; the workflow's
 * own env-only input passthrough is the primary defense). */
export function isSafeCommandText(text) {
  if (!text || text.length > 4000) return false;
  if (/[;&|`$<>\\]/.test(text)) return false;
  if (/\b(curl|wget|bash|powershell|cmd\.exe)\b/i.test(text)) return false;
  return true;
}
