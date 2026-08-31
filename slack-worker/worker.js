/**
 * Cloudflare Worker — Autofresh Slack operator interface (serverless, no
 * VPS, no local Hermes process required).
 *
 * Secrets (wrangler secret put) — never commit values:
 *   SLACK_SIGNING_SECRET   Slack app "Basic Information" -> Signing Secret
 *   GH_DISPATCH_TOKEN      fine-grained PAT / equivalent, actions:write on
 *                            this repo only (reused from the existing
 *                            AUTOFRESH_GH_DISPATCH_TOKEN credential)
 *   SLACK_ALLOWED_USERS    comma-separated Slack user IDs authorized to
 *                            operate Autofresh (reused from the existing
 *                            Hermes Slack allowlist — same workspace)
 *
 * Vars (wrangler.toml [vars] / dashboard):
 *   GITHUB_REPO   owner/repo, e.g. "rararerezeze-cyber/parrainage-bumper"
 *   GITHUB_REF    branch to dispatch against, always "main" in production
 *
 * KV binding:
 *   IDEMPOTENCY   short-TTL dedupe store for Slack retries / double-clicks
 *
 * Endpoints:
 *   GET  /health
 *   POST /slack/commands       Slash command (/autofresh <command>)
 *   POST /slack/interactivity  Block Kit button clicks (write confirmation)
 *
 * Flow (mirrors telegram-worker's proven shape, adapted for Slack):
 *   slash command → signature + allowlist → GH workflow_dispatch
 *     (hermes_operator.yml, run_writers=false, reply_channel=<channel>)
 *     → workflow itself posts the full Block Kit result back to Slack
 *   button click  → signature + allowlist → GH workflow_dispatch
 *     (same command, run_writers=true) → workflow posts the write result
 *
 * Hermes is never in this path. Nothing here depends on any local process.
 */
import {
  verifySlackRequest,
  parseAllowedUsers,
  isUserAllowed,
  helpText,
  buildDispatchBody,
  dispatchUrl,
  parseConfirmValue,
  isSafeCommandText,
  clip,
} from "./lib.js";

const WORKFLOW_FILE = "hermes_operator.yml";
const IDEMPOTENCY_TTL_SECONDS = 30;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "autofresh-slack" });
    }

    if (request.method === "POST" && url.pathname === "/slack/commands") {
      return handleSlashCommand(request, env, ctx);
    }

    if (request.method === "POST" && url.pathname === "/slack/interactivity") {
      return handleInteractivity(request, env, ctx);
    }

    return new Response("Not found", { status: 404 });
  },
};

async function handleSlashCommand(request, env, ctx) {
  const rawBody = await request.text();
  const verify = await verifySlackRequest({
    signingSecret: env.SLACK_SIGNING_SECRET,
    timestamp: request.headers.get("X-Slack-Request-Timestamp"),
    signature: request.headers.get("X-Slack-Signature"),
    rawBody,
  });
  if (!verify.ok) {
    return json({ ok: false, error: verify.reason }, 401);
  }

  const form = new URLSearchParams(rawBody);
  const userId = form.get("user_id") || "";
  const channelId = form.get("channel_id") || "";
  const triggerId = form.get("trigger_id") || "";
  const text = (form.get("text") || "").trim();

  const allowed = parseAllowedUsers(env.SLACK_ALLOWED_USERS);
  if (!isUserAllowed(userId, allowed)) {
    return ephemeral("Non autorisé.");
  }

  if (!text || /^(aide|help)$/i.test(text)) {
    return ephemeral(helpText());
  }

  if (!isSafeCommandText(text)) {
    return ephemeral("Commande rejetée (caractères non autorisés). " + helpText());
  }

  const dedupeKey = `slash:${userId}:${triggerId}`;
  if (await seenRecently(env, dedupeKey)) {
    return ephemeral("Déjà reçu — en cours de traitement.");
  }

  const correlationId = crypto.randomUUID();
  const dispatched = await dispatchWorkflow(env, {
    command: text,
    requester: `slack:${userId}`,
    correlationId,
    runWriters: false,
    replyChannel: channelId,
  });

  if (!dispatched.ok) {
    return ephemeral(`Échec du déclenchement (${dispatched.status}). Vérifie GH_DISPATCH_TOKEN.`);
  }

  return ephemeral(
    `🔄 Reçu — « ${clip(text, 200)} »\ncorrelation_id: \`${correlationId}\`\nRésultat détaillé sous peu dans ce salon.`
  );
}

async function handleInteractivity(request, env, ctx) {
  const rawBody = await request.text();
  const verify = await verifySlackRequest({
    signingSecret: env.SLACK_SIGNING_SECRET,
    timestamp: request.headers.get("X-Slack-Request-Timestamp"),
    signature: request.headers.get("X-Slack-Signature"),
    rawBody,
  });
  if (!verify.ok) {
    return json({ ok: false, error: verify.reason }, 401);
  }

  const form = new URLSearchParams(rawBody);
  let payload;
  try {
    payload = JSON.parse(form.get("payload") || "{}");
  } catch {
    return json({ ok: false, error: "invalid_payload" }, 400);
  }

  const userId = payload?.user?.id || "";
  const responseUrl = payload?.response_url || "";
  const channelId = payload?.channel?.id || "";

  const allowed = parseAllowedUsers(env.SLACK_ALLOWED_USERS);
  if (!isUserAllowed(userId, allowed)) {
    if (responseUrl) {
      ctx.waitUntil(postResponseUrl(responseUrl, { text: "Non autorisé.", replace_original: false }));
    }
    return new Response(null, { status: 200 });
  }

  const action = (payload?.actions || [])[0];
  const confirm = action ? parseConfirmValue(action.value) : null;
  if (!confirm) {
    if (responseUrl) {
      ctx.waitUntil(
        postResponseUrl(responseUrl, { text: "Bouton invalide ou expiré.", replace_original: false })
      );
    }
    return new Response(null, { status: 200 });
  }

  const dedupeKey = `confirm:${confirm.correlation_id || confirm.command}`;
  if (await seenRecently(env, dedupeKey)) {
    if (responseUrl) {
      ctx.waitUntil(
        postResponseUrl(responseUrl, { text: "Déjà confirmé — écriture en cours.", replace_original: false })
      );
    }
    return new Response(null, { status: 200 });
  }

  const dispatched = await dispatchWorkflow(env, {
    command: confirm.command,
    requester: `slack-confirm:${userId}`,
    correlationId: confirm.correlation_id || crypto.randomUUID(),
    runWriters: true,
    replyChannel: channelId,
  });

  if (responseUrl) {
    ctx.waitUntil(
      postResponseUrl(responseUrl, {
        text: dispatched.ok
          ? "⏳ Écriture confirmée — exécution en cours. Résultat sous peu dans ce salon."
          : `Échec du déclenchement de l'écriture (${dispatched.status}).`,
        replace_original: false,
      })
    );
  }

  return new Response(null, { status: 200 });
}

async function dispatchWorkflow(env, { command, requester, correlationId, runWriters, replyChannel }) {
  const repo = env.GITHUB_REPO;
  const token = env.GH_DISPATCH_TOKEN;
  if (!repo || !token) {
    return { ok: false, status: 0, error: "missing_github_config" };
  }
  const body = buildDispatchBody({
    ref: env.GITHUB_REF || "main",
    command,
    requester,
    correlationId,
    runWriters,
    replyChannel,
  });
  try {
    const res = await fetch(dispatchUrl(repo, WORKFLOW_FILE), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "autofresh-slack-worker",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify(body),
    });
    return { ok: res.ok, status: res.status };
  } catch {
    return { ok: false, status: 0, error: "network_error" };
  }
}

async function seenRecently(env, key) {
  if (!env.IDEMPOTENCY) return false; // fail open on missing binding rather than fail closed on ops
  const existing = await env.IDEMPOTENCY.get(key);
  if (existing) return true;
  await env.IDEMPOTENCY.put(key, "1", { expirationTtl: IDEMPOTENCY_TTL_SECONDS });
  return false;
}

async function postResponseUrl(responseUrl, body) {
  try {
    await fetch(responseUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    // best-effort only
  }
}

function ephemeral(text) {
  return json({ response_type: "ephemeral", text });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
