import test from "node:test";
import assert from "node:assert/strict";
import { demoConfigRaw, src } from "./helpers.mjs";
const C = await import(src("config.js"));

test("the packaged demo config resolves cleanly", () => {
  const { config, errors, warnings } = C.resolveConfig(demoConfigRaw());
  assert.deepEqual(errors, []);
  assert.deepEqual(warnings, []);
  assert.equal(config.chart.secondary.event, "item_open");
  assert.equal(config.funnel.channelDimension, "firstUserDefaultChannelGroup");
  assert.match(config.traffic.rev, /^r[0-9a-f]+$/);
  assert.deepEqual(config.traffic.rule, { any: [{ dim: "hostName", notIn: ["www.example.com", "example.com"] }] });
});

test("an empty config is a valid, minimal dashboard", () => {
  const { config, errors } = C.resolveConfig({});
  assert.deepEqual(errors, []);
  assert.equal(config.funnel, null);
  assert.equal(config.traffic, null);
  assert.equal(config.breakdowns.length, 3);
});

test("errors are collected, unknown keys are warnings", () => {
  const { errors, warnings } = C.resolveConfig({
    propertyId: "G-123", defaultRange: 5, funnel: { steps: [{ key: "visitors", events: [] }] },
    internalUsers: { ids: { a: "x" } }, plugins: ["https://evil.example/x.js", "../up.js", "/abs.js", "cards/ok.js"],
    tracking: true,
  });
  assert.ok(errors.some(e => e.includes("numeric GA4 property id")));
  assert.ok(errors.some(e => e.includes("defaultRange")));
  assert.ok(errors.some(e => e.includes("unique key")));
  assert.ok(errors.some(e => e.includes("events must list")));
  assert.ok(errors.some(e => e.includes("internalUsers.dimension")));
  assert.equal(errors.filter(e => e.startsWith("plugin")).length, 3);
  assert.deepEqual(warnings, ['unknown key "tracking" ignored']);
});

test("?client_id= and ?property= override and are remembered", () => {
  const store = new Map();
  const storage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  const base = C.resolveConfig({}).config;
  let cfg = C.applyOverrides(base, { params: new URLSearchParams("client_id=abc&property=42"), storage });
  assert.equal(cfg.clientId, "abc"); assert.equal(cfg.propertyId, "42");
  cfg = C.applyOverrides(base, { params: new URLSearchParams(""), storage });
  assert.equal(cfg.clientId, "abc");
});

test("?config= accepts only same-site .json paths", () => {
  assert.equal(C.isSafeConfigPath("previews/a.config.json"), true);
  for (const bad of ["https://x/c.json", "//x/c.json", "../c.json", "/c.json", "c.js", "javascript:alert(1).json"])
    assert.equal(C.isSafeConfigPath(bad), false, bad);
});

test("card storage is scoped by mode, so demo data never lands under a real property", async () => {
  const { storageScope } = await import(src("dashboard.js"));
  assert.equal(storageScope("demo", "123456"), "demo");          // a demo page may carry a configured property
  assert.equal(storageScope("direct", "123456"), "direct:123456");
  assert.equal(storageScope("server", "123456"), "server:123456");
  assert.equal(storageScope("server", ""), "server");
  assert.notEqual(storageScope("direct", "123456"), storageScope("server", "123456"));
});
