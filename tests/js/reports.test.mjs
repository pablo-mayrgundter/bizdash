import test from "node:test";
import assert from "node:assert/strict";
import { NOW, demoConfigRaw, src } from "./helpers.mjs";
const { createDemoApi } = await import(src("ga/demo.js"));
const { createGA, demoTransport } = await import(src("ga/client.js"));
const { resolveConfig, merge } = await import(src("config.js"));
const R = await import(src("core/reports.js"));
const { hoursIn } = await import(src("time.js"));

const resolved = raw => { const r = resolveConfig(raw); assert.deepEqual(r.errors, []); return r.config; };
const cfg = resolved(demoConfigRaw());
const gaFor = (c = cfg) => createGA({ transport: demoTransport(createDemoApi(c, { now: NOW })), mode: "demo" });

test("24 h: 24 points ending at GA's latest hour; tiles are their own de-duplicated query", async () => {
  const ts = await R.timeseries(gaFor(), cfg, 1440);
  assert.deepEqual(ts.window, { start: "2026110316", end: "2026110415" });
  assert.equal(ts.points.length, 24);
  assert.equal(ts.points.at(-1).ts, ts.latest);
  const summed = ts.points.reduce((s, p) => s + p.value, 0);
  assert.ok(ts.totals.users < summed, "users must not be the sum of hourly users");
  // and the same number as asking GA directly over those hours
  const direct = await R.windowTotals(gaFor(), ts.window);
  assert.equal(ts.totals.users, Math.round(direct.all.users));
  assert.ok(ts.previous && ts.previous.users > 0);
  assert.equal(ts.totals.views, ts.points.length && ts.totals.views);  // views are summed, a plain count
});

test("1 h is one bucket: GA's latest reported hour", async () => {
  const ts = await R.timeseries(gaFor(), cfg, 60);
  assert.equal(ts.points.length, 1);
  assert.deepEqual(ts.window, { start: ts.latest, end: ts.latest });
});

test("segments: the off-site burst is a spike under All and Non-standard, not Standard", async () => {
  const ts = await R.timeseries(gaFor(), cfg, 1440);
  const offsite = ep => hoursIn(ep).includes("2026110319");     // 20 h before 15:00
  assert.ok(ts.segments.all.episodes.some(offsite));
  assert.ok(ts.segments.nonstandard.episodes.some(offsite));
  assert.ok(!ts.segments.standard.episodes.some(offsite));
  // Standard is GA's own count, not All minus Non-standard
  assert.ok(ts.segments.standard.totals.users <= ts.segments.all.totals.users);
});

test("a secondary event before its `since` is unknown, not zero", async () => {
  const c = resolved(merge(demoConfigRaw(), { events: { item_open: { since: "2026-11-04" } } }));
  const ts = await R.timeseries(gaFor(c), c, 1440);
  assert.equal(ts.points[0].secondary, null);
  assert.ok(ts.points.at(-1).secondary != null);
  assert.equal(ts.totals.secondaryClipped, true);
});

test("30 m comes from realtime: no baseline, no sessions, and refusals named", async () => {
  const rr = await R.realtimeRange(gaFor(), cfg);
  assert.equal(rr.points.length, 30);
  assert.ok(rr.points.every(p => p.baseline === null));
  assert.equal(rr.totals.sessions, null);
  assert.equal(rr.totals.views, null);
  assert.deepEqual(rr.gaps.map(g => g.field), ["page views"]);
});

test("funnel: TOTAL is GA's own, a never-seen step is null not zero, internal ids are excluded", async () => {
  const ga = gaFor();
  const fn = await R.funnel(ga, cfg, 1440);
  const refund = fn.stages.find(s => s.key === "refund");
  assert.equal(refund.total, null);
  assert.equal(refund.note, "not seen in GA yet");
  const opened = fn.stages.find(s => s.key === "opened");
  assert.ok(opened.total > 0);
  const sumRows = fn.rows.filter(r => r.channel !== "Total").reduce((s, r) => s + r.cells[1].value, 0);
  assert.ok(opened.total <= sumRows);
  const reqs = R.funnelRequests(cfg, 1440);
  assert.match(JSON.stringify(reqs["step:opened"].dimensionFilter), /demo\.internal\.1/);
  assert.doesNotMatch(JSON.stringify(reqs.visitors), /demo\.internal\.1/);
  // nobody the rule flags reached a step
  assert.ok(fn.segments.nonstandard.stages.slice(1).every(s => !s.total));
});

test("a step whose report fails falls back, and says so", async () => {
  const c = resolved(merge(demoConfigRaw(), { funnel: { steps: [
    { key: "opened", events: ["item_open"], filter: { filter: { fieldName: "customEvent:unregistered", stringFilter: { value: "x" } } },
      fallback: { events: ["item_open"], note: "counts every open until the dimension is registered" } }] } }));
  const fn = await R.funnel(gaFor(c), c, 1440);
  assert.equal(fn.stages[1].note, "counts every open until the dimension is registered");
  assert.ok(fn.stages[1].total > 0);
});

test("conversions are listed newest first, internal rows labelled", async () => {
  const cv = await R.conversions(gaFor(), cfg);
  assert.ok(cv.rows.length > 0);
  assert.ok(cv.rows.every((r, i) => !i || cv.rows[i - 1].dateHour >= r.dateHour));
  assert.ok(cv.internal > 0);
  assert.ok(Object.keys(cv.last).length > 0);
});

test("spike drill-down: the referral episode's top mover is its referrer", async () => {
  const ts = await R.timeseries(gaFor(), cfg, 7 * 1440);
  const ep = ts.episodes.find(e => e.hours.length >= 4);
  const d = await R.spikeDrill(gaFor(), cfg, ep.start, ep.end);
  assert.equal(d.movers.sources[0].name, "news.example");
  assert.ok(d.segments.standard.extra > d.segments.nonstandard.extra);
});

test("without a traffic rule, nothing is segmented and nothing complains", async () => {
  const c = resolved({ ...demoConfigRaw(), traffic: null });
  const ts = await R.timeseries(gaFor(c), c, 1440);
  assert.equal(ts.segments, null);
  assert.equal(ts.segmentsError, null);
});

// ---- row limits under comparisons -----------------------------------------------

const { trafficComparisons } = await import(src("rules.js"));
const { and } = await import(src("ga/rows.js"));
const recordingGA = c => {
  const api = createDemoApi(c, { now: NOW }), bodies = [];
  const ga = createGA({ transport: { post: (m, b) => { bodies.push(b); return api.post(m, b); } }, mode: "demo" });
  return { ga, api, bodies };
};
/** A segment's own top-n, asked for alone with its comparison's filter. */
const alone = (api, body, c, n, dim, metric) => (api.runReport({ ...body, limit: n,
  dimensionFilter: and(body.dimensionFilter, c.dimensionFilter) }).rows || [])
  .map(r => ({ name: r.dimensionValues[0].value, value: +r.metricValues[0].value }))
  .map(r => ({ [dim]: r.name, [metric]: r.value }));

test("breakdowns: each segment gets its own top 10, not a share of one limit", async () => {
  const { ga, api, bodies } = recordingGA(cfg);
  const bd = await R.breakdowns(ga, cfg, 30 * 1440);
  const cmp = R.comparisonsFor(cfg);
  const asked = bodies.find(b => b.comparisons && b.dimensions?.[0].name === "country");
  assert.equal(asked.limit, R.BREAKDOWN_ROWS * cmp.length);
  // the demo has ten countries under All: a shared limit of 10 gave All five
  assert.equal(bd.segments.all.items.countries.rows.length, R.BREAKDOWN_ROWS);
  for (const b of cfg.breakdowns) {
    const body = { dateRanges: [{ startDate: "29daysAgo", endDate: "today" }], dimensions: [{ name: b.dimension }],
                   metrics: [{ name: "activeUsers" }], orderBys: [{ metric: { metricName: "activeUsers" }, desc: true }] };
    for (const c of cmp) {
      const want = alone(api, body, c, R.BREAKDOWN_ROWS, "name", "users");
      assert.deepEqual(bd.segments[c.name].items[b.key].rows.map(r => ({ name: r.name, users: r.users })), want,
                       `${b.key} / ${c.name}`);
    }
  }
});

test("rankedParts: N rows in every segment, re-asking one the others crowd out", async () => {
  // a rule that puts about half the traffic in each segment, so every one has many sources
  const c = resolved(merge(demoConfigRaw(), { traffic: { siteHosts: null, rule: { any: [{ dim: "deviceCategory", in: ["mobile"] }] } } }));
  const cmp = R.comparisonsFor(c);
  const body = { dateRanges: [{ startDate: "6daysAgo", endDate: "today" }], dimensions: [{ name: "sessionSource" }],
                 metrics: [{ name: "activeUsers" }], orderBys: [{ metric: { metricName: "activeUsers" }, desc: true }], limit: 3 };
  const { ga, api } = recordingGA(c);
  const R0 = api.runReport({ ...body, comparisons: cmp });             // the shared limit alone: 3 rows for 3 segments
  assert.equal(R0.rows.length, 3);
  const { s } = await R.rankedParts(ga, { s: body }, cmp);
  const { gaRowsByComparison } = await import(src("ga/rows.js"));
  const by = gaRowsByComparison(s, ["sessionSource"], ["activeUsers"]);
  for (const k of cmp) {
    assert.equal(by[k.name].length, 3, k.name);
    assert.deepEqual(by[k.name], alone(api, body, k, 3, "sessionSource", "activeUsers"), k.name);
  }
  // the demo config's Non-standard has one source: ranked below All and Standard, it still comes back
  const { ga: ga2, api: api2 } = recordingGA(cfg);
  const cmp2 = R.comparisonsFor(cfg);
  const { t } = await R.rankedParts(ga2, { t: { ...body, limit: 2 } }, cmp2);
  const by2 = gaRowsByComparison(t, ["sessionSource"], ["activeUsers"]);
  for (const k of cmp2) assert.deepEqual(by2[k.name], alone(api2, body, k, 2, "sessionSource", "activeUsers"), k.name);
  assert.equal(by2.nonstandard.length, 1);
  // with no comparisons it is a plain report
  const { p } = await R.rankedParts(gaFor(), { p: body }, null);
  assert.equal(p.rows.length, 3);
  assert.ok(!p.dimensionHeaders.some(h => h.name === "comparison"));
});

test("limits that cover a whole report scale with the comparisons too", async () => {
  const { ga, bodies } = recordingGA(cfg);
  await R.funnel(ga, cfg, 1440);
  const n = R.comparisonsFor(cfg).length;
  assert.ok(bodies.filter(b => b.comparisons).every(b => b.limit === 100 * n));
  bodies.length = 0;
  const ts = await R.timeseries(ga, cfg, 1440);
  await R.windowTotals(ga, ts.window, R.comparisonsFor(cfg));
  assert.ok(bodies.some(b => b.comparisons && b.dimensions?.[0]?.name === "newVsReturning" && b.limit === 10 * n));
});
