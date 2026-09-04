import { test } from "node:test";
import assert from "node:assert/strict";
import {
  timingSafeEqual,
  computeSlackSignature,
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
import worker, { IDEMPOTENCY_TTL_SECONDS } from "./worker.js";

test("Worker signed request path: read/preview stays unarmed; confirmation dispatches once", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  const seen = new Map();
  const env = {
    SLACK_SIGNING_SECRET: "test-signing-secret", SLACK_ALLOWED_USERS: "U_TEST",
    GH_DISPATCH_TOKEN: "test-dispatch-token", GITHUB_REPO: "example/test",
    IDEMPOTENCY: {
      get: async (key) => seen.get(key),
      put: async (key, value, options) => {
        assert.ok(options.expirationTtl >= 60);
        seen.set(key, value);
      },
    },
  };
  async function request(path, fields, validSignature = true) {
    const body = new URLSearchParams(fields).toString();
    const timestamp = String(Math.floor(Date.now() / 1000));
    const signature = await computeSlackSignature(env.SLACK_SIGNING_SECRET, timestamp, body);
    return worker.fetch(new Request(`https://example.invalid${path}`, {
      method: "POST", body, headers: {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": validSignature ? signature : "v0=invalid",
      },
    }), env, { waitUntil: () => {} });
  }
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "https://api.github.com/repos/example/test/actions/workflows/hermes_operator.yml/dispatches");
    calls.push(JSON.parse(options.body));
    return new Response(null, { status: 204 });
  };
  try {
    const slash = { user_id: "U_TEST", channel_id: "C_TEST", trigger_id: "read-1", text: "Kraken statut" };
    assert.equal((await request("/slack/commands", slash, false)).status, 401);
    await request("/slack/commands", { ...slash, user_id: "U_OTHER" });
    assert.equal(calls.length, 0);
    await request("/slack/commands", slash);
    await request("/slack/commands", slash);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].inputs.run_writers, "false");
    assert.equal(calls[0].inputs.reply_channel, "C_TEST");
    await request("/slack/commands", { ...slash, trigger_id: "preview-1", text: "Kraken code TESTCODE" });
    assert.equal(calls[1].inputs.run_writers, "false");
    const payload = JSON.stringify({ user: { id: "U_TEST" }, channel: { id: "C_TEST" },
      actions: [{ value: JSON.stringify({ command: "Kraken code TESTCODE", correlation_id: "confirm-1" }) }] });
    await request("/slack/interactivity", { payload });
    await request("/slack/interactivity", { payload });
    assert.equal(calls.length, 3);
    assert.equal(calls[2].inputs.run_writers, "true");
    assert.equal(calls[2].inputs.correlation_id, "confirm-1");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("IDEMPOTENCY_TTL_SECONDS respects Cloudflare KV's hard minimum of 60s", () => {
  // Regression guard for the 2026-08-31 incident: a value below 60 makes
  // env.IDEMPOTENCY.put() throw on every dispatching slash command
  // (verified live: "Invalid expiration_ttl of 30. Expiration TTL must be
  // at least 60."), which broke /autofresh for every command except the
  // short-circuited "aide" path.
  assert.ok(IDEMPOTENCY_TTL_SECONDS >= 60, `IDEMPOTENCY_TTL_SECONDS=${IDEMPOTENCY_TTL_SECONDS} is below Cloudflare KV's minimum of 60`);
});

test("timingSafeEqual: equal strings", () => {
  assert.equal(timingSafeEqual("abc", "abc"), true);
});

test("timingSafeEqual: different strings same length", () => {
  assert.equal(timingSafeEqual("abc", "abd"), false);
});

test("timingSafeEqual: different lengths", () => {
  assert.equal(timingSafeEqual("abc", "abcd"), false);
});

test("timingSafeEqual: non-string inputs never throw", () => {
  assert.equal(timingSafeEqual(null, "abc"), false);
  assert.equal(timingSafeEqual(undefined, undefined), false);
});

test("computeSlackSignature matches Slack's documented worked example", async () => {
  // From Slack's own "Verifying requests from Slack" docs.
  const signingSecret = "8f742231b10e8888abcd99yyyzzz85a5";
  const timestamp = "1531420618";
  const rawBody =
    "token=xyzz0WbapA4vBCDEFasx0q6G&team_id=T1DC2JH3J&team_domain=testteamnow&channel_id=G8PSS9T3V&channel_name=foobar&user_id=U2CERLKJA&user_name=roadrunner&command=%2Fwebhook-collect&text=&response_url=https%3A%2F%2Fhooks.slack.com%2Fcommands%2FT1DC2JH3J%2F397700885554%2F96rGlfmibIGlgcZRskXaIFfN&trigger_id=398738663015.47445629121.803a0bc887a14d10d2c447fce8b6703c";
  const sig = await computeSlackSignature(signingSecret, timestamp, rawBody);
  assert.equal(sig, "v0=a2114d57b48eac39b9ad189dd8316235a7b4a8d21a10bd27519666489c69b503");
});

test("verifySlackRequest: accepts a freshly-signed request", async () => {
  const signingSecret = "test-secret";
  const rawBody = "text=hello";
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = await computeSlackSignature(signingSecret, timestamp, rawBody);
  const result = await verifySlackRequest({ signingSecret, timestamp, signature, rawBody });
  assert.equal(result.ok, true);
});

test("verifySlackRequest: rejects a tampered body", async () => {
  const signingSecret = "test-secret";
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = await computeSlackSignature(signingSecret, timestamp, "text=hello");
  const result = await verifySlackRequest({ signingSecret, timestamp, signature, rawBody: "text=goodbye" });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "bad_signature");
});

test("verifySlackRequest: rejects a stale timestamp (replay protection)", async () => {
  const signingSecret = "test-secret";
  const rawBody = "text=hello";
  const staleTimestamp = String(Math.floor(Date.now() / 1000) - 3600);
  const signature = await computeSlackSignature(signingSecret, staleTimestamp, rawBody);
  const result = await verifySlackRequest({ signingSecret, timestamp: staleTimestamp, signature, rawBody });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "stale_timestamp");
});

test("verifySlackRequest: rejects missing headers", async () => {
  const result = await verifySlackRequest({ signingSecret: "x", timestamp: null, signature: null, rawBody: "" });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "missing_headers");
});

test("verifySlackRequest: rejects wrong secret", async () => {
  const rawBody = "text=hello";
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = await computeSlackSignature("correct-secret", timestamp, rawBody);
  const result = await verifySlackRequest({ signingSecret: "wrong-secret", timestamp, signature, rawBody });
  assert.equal(result.ok, false);
});

test("parseAllowedUsers / isUserAllowed", () => {
  const set = parseAllowedUsers("U111, U222 ,,U333");
  assert.deepEqual([...set].sort(), ["U111", "U222", "U333"]);
  assert.equal(isUserAllowed("U222", set), true);
  assert.equal(isUserAllowed("U999", set), false);
});

test("isUserAllowed: empty allowlist fails closed", () => {
  assert.equal(isUserAllowed("U111", parseAllowedUsers("")), false);
  assert.equal(isUserAllowed("U111", parseAllowedUsers(undefined)), false);
});

test("helpText mentions every documented AGENTS.md command shape", () => {
  const text = helpText();
  assert.match(text, /statut/);
  assert.match(text, /overrides/);
  assert.match(text, /divergences/);
  assert.match(text, /plateformes/);
  assert.match(text, /gain filleul/);
});

test("buildDispatchBody: coerces runWriters to the literal strings the workflow expects", () => {
  const body = buildDispatchBody({
    ref: "main",
    command: "Kraken statut",
    requester: "slack:U1",
    correlationId: "corr-1",
    runWriters: false,
    replyChannel: "C123",
  });
  assert.equal(body.ref, "main");
  assert.equal(body.inputs.command, "Kraken statut");
  assert.equal(body.inputs.run_writers, "false");
  assert.equal(body.inputs.reply_channel, "C123");

  const body2 = buildDispatchBody({ command: "x", runWriters: true });
  assert.equal(body2.inputs.run_writers, "true");
  assert.equal(body2.ref, "main"); // defaults when omitted
});

test("dispatchUrl builds the exact documented endpoint shape", () => {
  assert.equal(
    dispatchUrl("rararerezeze-cyber/parrainage-bumper", "hermes_operator.yml"),
    "https://api.github.com/repos/rararerezeze-cyber/parrainage-bumper/actions/workflows/hermes_operator.yml/dispatches"
  );
});

test("parseConfirmValue: valid JSON with a command survives", () => {
  const v = parseConfirmValue(JSON.stringify({ command: "Kraken gain filleul 20 €", correlation_id: "c1" }));
  assert.equal(v.command, "Kraken gain filleul 20 €");
});

test("parseConfirmValue: malformed JSON never throws, returns null", () => {
  assert.equal(parseConfirmValue("not json"), null);
  assert.equal(parseConfirmValue(""), null);
  assert.equal(parseConfirmValue(undefined), null);
});

test("parseConfirmValue: JSON without a usable command is rejected", () => {
  assert.equal(parseConfirmValue(JSON.stringify({ correlation_id: "c1" })), null);
  assert.equal(parseConfirmValue(JSON.stringify({ command: "" })), null);
  assert.equal(parseConfirmValue(JSON.stringify({ command: "   " })), null);
});

test("isSafeCommandText rejects shell metacharacters and injection payloads", () => {
  assert.equal(isSafeCommandText("Kraken statut"), true);
  assert.equal(isSafeCommandText("Kraken; rm -rf /"), false);
  assert.equal(isSafeCommandText("Kraken `id`"), false);
  assert.equal(isSafeCommandText("Kraken $(whoami)"), false);
  assert.equal(isSafeCommandText("curl http://evil"), false);
  assert.equal(isSafeCommandText(""), false);
  assert.equal(isSafeCommandText("x".repeat(4001)), false);
});

test("clip truncates on a character boundary with an ellipsis", () => {
  assert.equal(clip("hello world", 5), "hell…");
  assert.equal(clip("hi", 10), "hi");
});
