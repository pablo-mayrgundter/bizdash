import test from "node:test";
import assert from "node:assert/strict";
import { src } from "./helpers.mjs";
const S = await import(src("spikes.js"));

const flat = (days, v = 20) => Array.from({ length: days * 24 }, (_, i) => ({ ts: String(2026010100 + i), value: v + (i % 3) }));

test("a flat series has no spikes; a burst over the same hours is one episode", () => {
  const series = flat(16);
  assert.equal(S.findEpisodes(S.robustScores(series)).length, 0);
  for (const i of [370, 371, 372]) series[i].value = 200;
  const eps = S.findEpisodes(S.robustScores(series));
  assert.equal(eps.length, 1);
  assert.equal(eps[0].hours.length, 3);
  assert.equal(eps[0].peak, 200);
  assert.ok(eps[0].extra > 500);
});

test("thresholds are configurable", () => {
  const series = flat(16);
  series[370].value = 40;                         // +20: a spike only for a sensitive config
  assert.equal(S.findEpisodes(S.robustScores(series)).length, 1);
  assert.equal(S.findEpisodes(S.robustScores(series), { minExtra: 30 }).length, 0);
});

test("movers compare the window with the baseline hours", () => {
  const m = S.spikeMovers(["h1"], { sources: [{ ts: "h1", name: "a", users: 10 }, { ts: "h0", name: "a", users: 2 },
                                               { ts: "h1", name: "b", users: 1 }] });
  assert.deepEqual(m.sources[0], { name: "a", spike: 10, baseline: 2, delta: 8 });
});
