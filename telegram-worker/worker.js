/**
 * Cloudflare Worker — Telegram webhook serverless (pas de VPS).
 *
 * Secrets (wrangler secret put) — never commit values:
 *   TELEGRAM_BOT_TOKEN
 *   TELEGRAM_ALLOWED_USER_ID
 *   GITHUB_TOKEN   (fine-grained: actions:write on this repo only)
 *   GITHUB_REPO    (owner/repo)
 *   GITHUB_REF     (branch) e.g. autofresh/phase2b-kraken-capture
 *
 * Endpoint: POST /telegram  (Telegram setWebhook)
 *
 * Flow:
 *   message → allowlist → safe command → GH workflow_dispatch telegram_sync
 *   → override + plan (+ live write only if platform WRITE_VERIFIED)
 *   → GH Actions replies via TELEGRAM_BOT_TOKEN + chat_id
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({
        ok: true,
        service: "autofresh-telegram",
        commands: "operator_control_v2",
      });
    }
    if (request.method !== "POST" || url.pathname !== "/telegram") {
      return new Response("Not found", { status: 404 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return json({ ok: false, error: "invalid_json" }, 400);
    }

    const msg = update.message || update.edited_message;
    if (!msg || !msg.text) {
      return json({ ok: true, ignored: true });
    }

    const userId = String(msg.from?.id || "");
    const allowed = String(env.TELEGRAM_ALLOWED_USER_ID || "");
    if (!allowed || userId !== allowed) {
      // Do not leak whether bot works to strangers beyond short reject
      await tg(env, msg.chat.id, "Non autorise.");
      return json({ ok: false, error: "forbidden" }, 403);
    }

    const text = String(msg.text || "").trim();
    if (text === "/start" || text === "/help") {
      await tg(
        env,
        msg.chat.id,
        helpText()
      );
      return json({ ok: true, help: true });
    }

    if (!isSafeCommand(text)) {
      await tg(env, msg.chat.id, helpText());
      return json({ ok: true, rejected: true });
    }

    await tg(env, msg.chat.id, `Recu. Lancement Autofresh…\n« ${clip(text, 200)} »`);

    const repo = env.GITHUB_REPO;
    const token = env.GITHUB_TOKEN;
    if (!repo || !token) {
      await tg(env, msg.chat.id, "Config incomplete (GITHUB_REPO / GITHUB_TOKEN).");
      return json({ ok: false, error: "missing_github_config" }, 500);
    }

    try {
      const res = await fetch(
        `https://api.github.com/repos/${repo}/actions/workflows/telegram_sync.yml/dispatches`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/vnd.github+json",
            "User-Agent": "autofresh-telegram-worker",
            "X-GitHub-Api-Version": "2022-11-28",
          },
          body: JSON.stringify({
            ref: env.GITHUB_REF || "autofresh/phase2b-kraken-capture",
            inputs: {
              message: text,
              chat_id: String(msg.chat.id),
            },
          }),
        }
      );
      if (!res.ok) {
        const body = await res.text();
        await tg(
          env,
          msg.chat.id,
          `Echec declenchement workflow (${res.status}). Verifie GITHUB_TOKEN (actions:write).`
        );
        return json(
          { ok: false, github_status: res.status, body: body.slice(0, 200) },
          502
        );
      }
    } catch (e) {
      await tg(env, msg.chat.id, "Erreur reseau GitHub.");
      return json({ ok: false, error: "github_network" }, 502);
    }

    // Final detailed reply is sent by GitHub Actions (report + chat_id)
    await tg(
      env,
      msg.chat.id,
      "Workflow demarre. Rapport detaille sous peu (override + plan multi-sites)."
    );
    return json({ ok: true, dispatched: true });
  },
};

function helpText() {
  return (
    "Autofresh — commandes operateur\n" +
    "• Kraken code ABC123\n" +
    "• Kraken lien https://...\n" +
    "• Kraken gain filleul 20 €\n" +
    "• Kraken Super-Parrain gain filleul 25 €\n" +
    "• Kraken depot minimum 100 €\n" +
    "• Kraken delai 15 jours\n" +
    "• Kraken conditions ...\n" +
    "• Kraken status\n" +
    "• Kraken overrides\n" +
    "• Kraken supprimer override gain filleul\n" +
    "Live writes uniquement sur plateformes WRITE_VERIFIED."
  );
}

/**
 * Allow full operator-control language; block shell metacharacters.
 * Must stay in sync with tools/telegram_update.py capabilities.
 */
function isSafeCommand(text) {
  if (!text || text.length > 4000) return false;
  if (/[;&|`$<>\\]/.test(text)) return false;
  // Reject obvious injection payloads
  if (/\b(curl|wget|bash|powershell|cmd\.exe)\b/i.test(text)) return false;

  const t = text.toLowerCase();
  // status / overrides / remove
  if (/\b(status|overrides)\b/.test(t)) return /[a-z]{2,}/.test(t);
  if (/\bsupprimer\s+override\b/.test(t)) return true;

  // field keywords (FR/EN)
  const field =
    /\b(code|lien|link|gain|filleul|parrain|reward|conditions?|cond|depot|dépôt|délai|delai|jours|date\s*fin|expiry|title|titre|minimum|spend|trade|transaction)\b/i.test(
      text
    );
  // program-ish token present
  const hasWord = /[A-Za-z]{2,}/.test(text);
  return field && hasWord;
}

function clip(s, n) {
  s = String(s || "");
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}

async function tg(env, chatId, text) {
  if (!env.TELEGRAM_BOT_TOKEN) return;
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text: String(text).slice(0, 3500) }),
    });
  } catch {
    // ignore notify failures
  }
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
