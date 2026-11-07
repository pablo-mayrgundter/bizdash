import test from "node:test";
import assert from "node:assert/strict";
import { src } from "./helpers.mjs";
const R = await import(src("rules.js"));
const { gaMatch } = await import(src("ga/filter.js"));

const RULES = [
  R.hostRule(["www.example.com", "example.com"]),
  { all: [{ dim: "country", in: ["Atlantis"] }, { not: { dim: "firstUserDefaultChannelGroup", in: ["Paid Search"] } }] },
  { any: [{ dim: "hostName", notIn: ["www.example.com"] }, { dim: "screenResolution", in: ["800x600"] }] },
];
const ROWS = [
  { hostName: "www.example.com", country: "Atlantis", firstUserDefaultChannelGroup: "Direct", screenResolution: "800x600" },
  { hostName: "localhost", country: "Atlantis", firstUserDefaultChannelGroup: "Paid Search", screenResolution: "1920x1080" },
  { hostName: "WWW.EXAMPLE.COM", country: "Utopia", firstUserDefaultChannelGroup: "Organic Search", screenResolution: "1920x1080" },
  { hostName: "(not set)", country: "Atlantis", firstUserDefaultChannelGroup: "Direct", screenResolution: "390x844" },
];

test("the compiled FilterExpression and the in-page evaluator agree on every row", () => {
  for (const rule of RULES) for (const row of ROWS) {
    const viaGA = gaMatch(R.trafficFilterExpression(rule), name => row[name]);
    assert.equal(R.isNonstandard(row, rule), viaGA, JSON.stringify({ rule, row }));
  }
});

test("a row missing a dimension the rule reads is unknown, not standard", () => {
  assert.equal(R.isNonstandard({ country: "Atlantis" }, RULES[0]), null);
  // ...unless the known part decides it (Kleene logic)
  assert.equal(R.isNonstandard({ screenResolution: "800x600" }, RULES[2]), true);
  assert.equal(R.isNonstandard({ country: "Utopia" }, RULES[1]), false);
});

test("a host rule has no realtime form; a country rule does", () => {
  assert.equal(R.realtimeRule(RULES[0]), null);
  const rt = R.realtimeRule(RULES[1]);
  assert.equal(rt.partial, true);                 // the channel leaf was dropped
  assert.deepEqual(rt.match, { all: [{ dim: "country", in: ["Atlantis"] }] });
});

test("three comparisons, all matching everything", () => {
  const c = R.trafficComparisons(RULES[0]);
  assert.deepEqual(c.map(x => x.name), ["all", "nonstandard", "standard"]);
  assert.deepEqual(c[2].dimensionFilter, { notExpression: c[1].dimensionFilter });
  assert.equal(R.classifyHosts(["localhost", "www.example.com"], RULES[0]), "both");
  assert.equal(R.classifyHosts([], RULES[0]), null);
});
