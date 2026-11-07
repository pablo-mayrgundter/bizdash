import test from "node:test";
import assert from "node:assert/strict";
import { src } from "./helpers.mjs";
const { createGA, directTransport, makeLimiter } = await import(src("ga/client.js"));

const ok = body => ({ status: 200, ok: true, text: JSON.stringify(body) });

test("identical requests share one round trip and are cached", async () => {
  let calls = 0;
  const ga = createGA({ transport: { post: async () => { calls++; return ok({ rows: [] }); } } });
  await Promise.all([ga.runReport({ a: 1, b: 2 }), ga.runReport({ b: 2, a: 1 })]);
  await ga.runReport({ a: 1, b: 2 });
  assert.equal(calls, 1);
  await ga.runReport({ a: 1 }, { ttl: 0 });
  await ga.runReport({ a: 1 }, { ttl: 0 });
  assert.equal(calls, 3);
});

test("the concurrency 429 is retried; quota 429s and failures are not cached", async () => {
  const seq = [{ status: 429, ok: false, text: "Exhausted concurrent requests quota" }, ok({ x: 1 })];
  const ga = createGA({ transport: { post: async () => seq.shift() }, sleep: async () => {} });
  assert.deepEqual(await ga.runReport({}), { x: 1 });
  let n = 0;
  const ga2 = createGA({ transport: { post: async () => { n++; return { status: 429, ok: false, text: '{"error":{"message":"Exhausted property tokens per hour"}}' }; } },
                         sleep: async () => {} });
  await assert.rejects(ga2.runReport({}), /tokens per hour/);
  await assert.rejects(ga2.runReport({}));
  assert.equal(n, 2);
});

test("parts: each request its own failure", async () => {
  const ga = createGA({ transport: { post: async (m, b) => (b.bad ? { status: 400, ok: false, text: '{"error":{"message":"nope"}}' } : ok({ fine: true })) } });
  const p = await ga.parts({ a: {}, b: { bad: true } });
  assert.deepEqual(p.a, { fine: true });
  assert.match(p.b.error, /nope/);
});

test("propertyQuota is remembered", async () => {
  const ga = createGA({ transport: { post: async () => ok({ propertyQuota: { tokensPerHour: { remaining: 7 } } }) } });
  await ga.runReport({});
  assert.equal(ga.quota.tokensPerHour.remaining, 7);
});

test("the limiter never exceeds its cap", async () => {
  const limit = makeLimiter(2);
  let live = 0, peak = 0;
  await Promise.all(Array.from({ length: 7 }, () => limit(async () => {
    live++; peak = Math.max(peak, live); await new Promise(r => setTimeout(r, 5)); live--;
  })));
  assert.equal(peak, 2);
});

test("direct: a 401 drops the token and retries once; a second 401 asks for sign-in", async () => {
  const tokens = { token: "T1", ensureToken: async () => {}, dropped: [], dropToken(t) { this.dropped.push(t); } };
  let expired = 0;
  const statuses = [401, 200];
  const t = directTransport({ tokens, propertyId: "1", onSessionExpired: () => expired++,
    fetchFn: async () => ({ status: statuses[0], ok: statuses[0] === 200, text: async () => (statuses.shift(), "{}") }) });
  assert.equal((await t.post("runReport", {})).status, 200);
  assert.deepEqual(tokens.dropped, ["T1"]);
  const t2 = directTransport({ tokens, propertyId: "1", onSessionExpired: () => expired++,
    fetchFn: async () => ({ status: 401, ok: false, text: async () => "{}" }) });
  assert.equal((await t2.post("runReport", {})).status, 401);
  assert.equal(expired, 1);
});

test("direct: a 403 for insufficient scopes refuses the token and asks for sign-in, without retrying", async () => {
  const tokens = { token: "T1", ensureToken: async () => {}, refused: [], refuseScopes(t) { this.refused.push(t); } };
  const why = [];
  let calls = 0;
  const body = JSON.stringify({ error: { code: 403, message: "Request had insufficient authentication scopes.",
    details: [{ reason: "ACCESS_TOKEN_SCOPE_INSUFFICIENT" }] } });
  const t = directTransport({ tokens, propertyId: "1", onSessionExpired: w => why.push(w),
    fetchFn: async () => { calls++; return { status: 403, ok: false, text: async () => body }; } });
  assert.equal((await t.post("runReport", {})).status, 403);
  assert.equal(calls, 1, "no retry: the same token can't succeed");
  assert.deepEqual(tokens.refused, ["T1"]);
  assert.deepEqual(why, [{ missingScope: true }]);
});

test("direct: other 403s (no access to the property) are passed through untouched", async () => {
  const tokens = { token: "T1", ensureToken: async () => {}, refuseScopes() { throw new Error("must not refuse"); } };
  let expired = 0;
  const t = directTransport({ tokens, propertyId: "1", onSessionExpired: () => expired++,
    fetchFn: async () => ({ status: 403, ok: false, text: async () => '{"error":{"message":"User does not have sufficient permissions for this property."}}' }) });
  assert.equal((await t.post("runReport", {})).status, 403);
  assert.equal(expired, 0);
});
