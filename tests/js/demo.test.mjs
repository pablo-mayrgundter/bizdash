import test from "node:test";
import assert from "node:assert/strict";
import { NOW, demoConfigRaw, src } from "./helpers.mjs";
const { createDemoApi } = await import(src("ga/demo.js"));
const { resolveConfig } = await import(src("config.js"));
const { trafficComparisons } = await import(src("rules.js"));

const cfg = resolveConfig(demoConfigRaw()).config;
const api = createDemoApi(cfg, { now: NOW });
const ask = async (method, body) => { const r = await api.post(method, body); return { status: r.status, body: JSON.parse(r.text) }; };
const today = [{ startDate: "today", endDate: "today" }];
const num = (p, i = 0) => +p.rows[0].metricValues[i].value;

test("users over a window are GA-style distinct, not the sum of hourly users", async () => {
  const day = await ask("runReport", { dateRanges: [{ startDate: "6daysAgo", endDate: "today" }], metrics: [{ name: "activeUsers" }] });
  const hours = await ask("runReport", { dateRanges: [{ startDate: "6daysAgo", endDate: "today" }],
    dimensions: [{ name: "dateHour" }], metrics: [{ name: "activeUsers" }], limit: 100000 });
  const sum = hours.body.rows.reduce((s, r) => s + +r.metricValues[0].value, 0);
  assert.ok(num(day.body) < sum, `${num(day.body)} should be below ${sum}`);
});

test("the current hour is still filling and nothing after now exists", async () => {
  const p = (await ask("runReport", { dateRanges: today, dimensions: [{ name: "dateHour" }], metrics: [{ name: "sessions" }] })).body;
  assert.equal(p.rows[p.rows.length - 1].dimensionValues[0].value, "2026110415");
});

test("comparisons: the column comes last, and a dimensionless report answers in totals alone", async () => {
  const cmp = trafficComparisons(cfg.traffic.rule);
  const p = (await ask("runReport", { dateRanges: today, dimensions: [{ name: "country" }], metrics: [{ name: "activeUsers" }], comparisons: cmp })).body;
  assert.equal(p.dimensionHeaders.at(-1).name, "comparison");
  const t = (await ask("runReport", { dateRanges: [{ startDate: "1daysAgo", endDate: "today" }], metrics: [{ name: "activeUsers" }], comparisons: cmp, metricAggregations: ["TOTAL"] })).body;
  assert.equal(t.rows, undefined);
  assert.deepEqual(t.totals.map(x => x.dimensionValues[0].value).sort(), ["all", "nonstandard", "standard"]);
});

test("refusals: an unregistered custom dimension, realtime page views and hostName", async () => {
  assert.equal((await ask("runReport", { dateRanges: today, dimensions: [{ name: "customEvent:nope" }], metrics: [{ name: "eventCount" }] })).status, 400);
  assert.equal((await ask("runReport", { dateRanges: today, dimensions: [{ name: "customEvent:client_id" }], metrics: [{ name: "eventCount" }] })).status, 200);
  assert.equal((await ask("runRealtimeReport", { metrics: [{ name: "screenPageViews" }] })).status, 400);
  assert.equal((await ask("runRealtimeReport", { dimensions: [{ name: "hostName" }], metrics: [{ name: "activeUsers" }] })).status, 400);
});

test("realtime headlines are de-duplicated: five minutes is not a slice of thirty", async () => {
  const users = async mins => num((await ask("runRealtimeReport", { metrics: [{ name: "activeUsers" }],
    minuteRanges: [{ startMinutesAgo: mins - 1, endMinutesAgo: 0 }] })).body);
  const per = (await ask("runRealtimeReport", { dimensions: [{ name: "minutesAgo" }], metrics: [{ name: "activeUsers" }] })).body;
  const sum = per.rows.reduce((s, r) => s + +r.metricValues[0].value, 0);
  const u30 = await users(30);
  assert.ok(u30 < sum && (await users(5)) < u30);
});

test("deterministic for a given now", async () => {
  const again = createDemoApi(cfg, { now: NOW });
  const body = { dateRanges: [{ startDate: "29daysAgo", endDate: "today" }], dimensions: [{ name: "eventName" }], metrics: [{ name: "totalUsers" }] };
  assert.equal((await again.post("runReport", body)).text, (await api.post("runReport", body)).text);
});
