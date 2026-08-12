/**
 * Cloudflare Worker — Telegram webhook serverless (pas de VPS).
 *
 * Secrets (wrangler secret put):
 *   TELEGRAM_BOT_TOKEN
 *   TELEGRAM_ALLOWED_USER_ID
 *   GITHUB_TOKEN   (repo scope: actions:write)
 *   GITHUB_REPO    (owner/repo) e.g. rararerezeze-cyber/parrainage-bumper
 *   GITHUB_REF     (branch) e.g. autofresh/phase2b-kraken-capture
 *
 * Endpoint: POST /telegram  (Telegram setWebhook)
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "autofresh-telegram" });
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
      await tg(env, msg.chat.id, "Non autorise.");
      return json({ ok: false, error: "forbidden" }, 403);
    }

    const text = String(msg.text || "").trim();
    // Only allow safe natural-language update patterns (no shell)
    if (!isSafeCommand(text)) {
      await tg(
        env,
        msg.chat.id,
        "Commande non reconnue.\nExemples:\n• Kraken code ABC123\n• Kraken lien https://...\n• Le nouveau code Kraken est ABC123"
      );
      return json({ ok: true, rejected: true });
    }

    await tg(env, msg.chat.id, `Recu. Lancement sync…\n« ${text} »`);

    try {
      const res = await fetch(
        `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/telegram_sync.yml/dispatches`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${env.GITHUB_TOKEN}`,
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
        await tg(env, msg.chat.id, `Echec declenchement workflow (${res.status}).`);
        return json({ ok: false, github_status: res.status, body: body.slice(0, 200) }, 502);
      }
    } catch (e) {
      await tg(env, msg.chat.id, "Erreur reseau GitHub.");
      return json({ ok: false, error: "github_network" }, 502);
    }

    return json({ ok: true });
  },
};

function isSafeCommand(text) {
  if (text.length > 400) return false;
  if (/[;&|`$<>\\]/.test(text)) return false;
  // Allow natural phrases for code/link updates only
  return (
    /\bcode\b/i.test(text) ||
    /\blien\b/i.test(text) ||
    /\blink\b/i.test(text)
  ) && /[A-Za-z]{2,}/.test(text);
}

async function tg(env, chatId, text) {
  if (!env.TELEGRAM_BOT_TOKEN) return;
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
