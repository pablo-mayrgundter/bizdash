// The token-renewal state machine (src/ga/auth.js), driven ordering by ordering
// through a fake Google Identity Services. Every failure mode here is an
// ordering between concurrent requests that clicking around would never
// reproduce reliably.
import test from "node:test";
import assert from "node:assert/strict";
import { src } from "./helpers.mjs";
const { createTokenManager } = await import(src("ga/auth.js"));

const tick = (ms = 5) => new Promise(r => setTimeout(r, ms));

function harness(opts = {}) {
  const store = new Map(opts.store || []);
  const storage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) };
  const gis = { clients: [], popups: 0 };
  const win = { google: { accounts: { oauth2: { initTokenClient: cfg => {
    const c = { cfg, asked: [], requestAccessToken(o) { gis.popups++; c.asked.push(o || {}); } };
    gis.clients.push(c);
    return c;
  } } } } };
  const emails = opts.emails || {};
  const fetchFn = async (url, o) => {
    const auth = o.headers.Authorization;
    const tok = Object.keys(emails).find(t => auth.endsWith(` ${t}`));
    if (opts.userinfoDelay?.[tok]) await tick(opts.userinfoDelay[tok]);
    return { ok: true, json: async () => (tok ? { email: emails[tok] } : {}) };
  };
  const events = { signin: 0, recovered: 0 };
  const tm = createTokenManager({ clientId: "test", storage, win, doc: opts.doc, fetchFn, requestTimeoutMs: opts.timeout || 1000,
    forceAccountSelect: !!opts.forceAccountSelect,
    onSignInNeeded: () => events.signin++, onRecovered: () => events.recovered++ });
  const grant = (i, token, expiresIn = 3600) => gis.clients[i].cfg.callback({ access_token: token, expires_in: expiresIn });
  const deny = (i, type = "popup_closed") => gis.clients[i].cfg.error_callback({ type });
  return { tm, gis, store, grant, deny, events };
}

test("the GIS loader is memoized: concurrent requests share one script", async () => {
  const appended = [];
  const doc = { createElement: () => ({}), head: { appendChild: el => { appended.push(el); setTimeout(() => el.onload(), 1); } } };
  const { tm } = harness({ doc });
  tm.state.gisPromise = null;
  const winless = createTokenManager({ clientId: "x", win: {}, doc, storage: null });
  const a = winless.loadGIS(), b = winless.loadGIS();
  assert.equal(a, b);
  await a;
  assert.equal(appended.length, 1);
});

test("each request gets its own token client", async () => {
  const { tm, gis, grant } = harness();
  const p1 = tm.requestToken().catch(() => {});
  await tick(); grant(0, "A"); await p1;
  tm.requestToken().catch(() => {});
  await tick();
  assert.equal(gis.clients.length, 2);
  assert.notEqual(gis.clients[0], gis.clients[1]);
});

test("a request superseded before it asked opens no popup", async () => {
  const { tm, gis } = harness();
  tm.requestToken().catch(() => {});
  tm.requestToken({ interactive: true }).catch(() => {});
  await tick(20);
  assert.equal(gis.popups, 1);
});

test("supersession follows request order, not script-load order", async () => {
  const { tm } = harness();
  const older = tm.requestToken().catch(e => (e.superseded ? "superseded" : "err"));
  tm.requestToken({ interactive: true }).catch(() => {});
  assert.equal(await older, "superseded");
});

test("a stale grant never overwrites a newer token", async () => {
  const { tm, grant } = harness();
  tm.requestToken().catch(() => {});
  await tick();
  tm.requestToken({ interactive: true }).catch(() => {});
  await tick();
  grant(1, "NEW");
  grant(0, "STALE");
  assert.equal(tm.token, "NEW");
});

test("a late grant is kept when nothing newer wrote", async () => {
  const { tm, grant } = harness({ timeout: 10 });
  const p = tm.requestToken({ interactive: true }).catch(e => e.message);
  await tick(5);
  assert.match(await p, /timed out/);
  grant(0, "LATE");
  assert.equal(tm.token, "LATE");
});

test("a superseded request's good grant survives a cancelled newer one", async () => {
  const { tm, grant, deny } = harness();
  tm.requestToken().catch(() => {});
  await tick();
  tm.requestToken({ interactive: true }).catch(() => {});
  await tick();
  deny(1);
  grant(0, "SILENT");
  assert.equal(tm.token, "SILENT");
});

test("concurrent renewals coalesce onto one popup; a click does not coalesce", async () => {
  const { tm, gis, grant } = harness();
  const a = tm.ensureToken(), b = tm.ensureToken(), c = tm.ensureToken();
  await tick();
  assert.equal(gis.popups, 1);
  grant(0, "T");
  await Promise.all([a, b, c]);
  tm.state.accessToken = null;
  tm.ensureToken().catch(() => {});
  await tick();
  tm.ensureToken({ interactive: true }).catch(() => {});
  await tick();
  assert.equal(gis.popups, 3);
});

test("callers coalesced onto a superseded request follow it to its successor", async () => {
  const { tm, grant } = harness();
  const waiting = tm.ensureToken();
  await tick();
  const click = tm.ensureToken({ interactive: true });
  await tick();
  grant(1, "CLICKED");
  await Promise.all([waiting, click]);
  assert.equal(tm.token, "CLICKED");
});

test("silent renewal hints the remembered account; a sign-in click shows the chooser", async () => {
  const { tm, gis, grant } = harness({ emails: { A: "viewer@example.com" } });
  const p = tm.requestToken(); await tick(); grant(0, "A"); await p; await tick(10);
  tm.requestToken().catch(() => {}); await tick();
  assert.deepEqual(gis.clients[1].asked[0], { prompt: "", hint: "viewer@example.com" });
  tm.requestToken({ interactive: true }).catch(() => {}); await tick();
  assert.deepEqual(gis.clients[2].asked[0], { prompt: "select_account" });
});

test("an identity change clears the hint only when it lands", async () => {
  const { tm, store, grant, deny } = harness({ store: [["bizdash.userEmail", "old@example.com"]] });
  tm.requestToken({ interactive: true }).catch(() => {}); await tick();
  deny(0);
  await tick();
  assert.equal(store.get("bizdash.userEmail"), "old@example.com");
  tm.requestToken({ interactive: true }).catch(() => {}); await tick();
  grant(1, "B");
  assert.equal(store.get("bizdash.userEmail"), undefined);
});

test("out-of-order userinfo cannot point the hint at the old account", async () => {
  const { tm, store, grant } = harness({ emails: { OLD: "old@example.com", NEW: "new@example.com" },
                                         userinfoDelay: { OLD: 30 } });
  tm.requestToken().catch(() => {}); await tick();
  grant(0, "OLD");
  tm.requestToken({ interactive: true }).catch(() => {}); await tick();
  grant(1, "NEW");
  await tick(60);
  assert.equal(store.get("bizdash.userEmail"), "new@example.com");
});

test("background failures stay quiet; foreground ones ask for sign-in", async () => {
  const { tm, deny, events } = harness();
  const bg = tm.ensureToken({ background: true }).catch(() => {});
  await tick(); deny(0); await bg;
  assert.equal(events.signin, 0);
  const fg = tm.ensureToken().catch(() => {});
  await tick(); deny(1); await fg;
  assert.equal(events.signin, 1);
});

test("the sweeper tops up a live token, once per broken window, and never bootstraps", async () => {
  const { tm, gis, deny, grant } = harness();
  assert.equal(tm.sweep(), null);                 // signed out: nothing
  tm.state.accessToken = "LIVE"; tm.state.tokenExpiry = Date.now() + 120e3;   // inside the refresh lead
  const s1 = tm.sweep(); await tick(); deny(0); await s1;
  assert.equal(tm.state.sweeperPaused, true);
  assert.equal(tm.sweep(), null);
  const p = tm.requestToken({ interactive: true }); await tick(); grant(1, "AGAIN"); await p;
  assert.equal(tm.state.sweeperPaused, false);   // any grant re-arms it
  assert.equal(gis.popups, 2);
});

test("a 401 drops only the token that was sent", () => {
  const { tm } = harness();
  tm.state.accessToken = "NEWER";
  tm.dropToken("OLDER");
  assert.equal(tm.token, "NEWER");
  tm.dropToken("NEWER");
  assert.equal(tm.token, null);
});

test("a persisted token is restored; ?switch_account forgets it", () => {
  const saved = [["bizdash.token", JSON.stringify({ token: "KEPT", expiry: Date.now() + 3600e3 })]];
  assert.equal(harness({ store: saved }).tm.token, "KEPT");
  assert.equal(harness({ store: saved, forceAccountSelect: true }).tm.token, null);
});

test("a grant without the Analytics scope is refused, not stored, and the next popup asks for consent", async () => {
  const { tm, gis, store, events } = harness();
  const p = tm.requestToken({ interactive: true });
  await tick();
  gis.clients[0].cfg.callback({ access_token: "NOGA", expires_in: 3600, scope: "openid email https://www.googleapis.com/auth/userinfo.email" });
  await assert.rejects(p, e => e.missingScope && /Google Analytics access wasn't granted/.test(e.message));
  assert.equal(tm.token, null);
  assert.equal(store.get("bizdash.token"), undefined);
  assert.equal(events.signin, 1);
  const p2 = tm.requestToken({ interactive: true });
  await tick();
  assert.equal(gis.clients[1].asked[0].prompt, "consent");
  gis.clients[1].cfg.callback({ access_token: "OK", expires_in: 3600,
    scope: "openid email https://www.googleapis.com/auth/analytics.readonly" });
  await p2;
  assert.equal(tm.token, "OK");
  const p3 = tm.requestToken({ interactive: true });
  await tick();
  assert.equal(gis.clients[2].asked[0].prompt, "select_account", "consent is asked for only after a refusal");
  gis.clients[2].cfg.callback({ access_token: "OK2", expires_in: 3600 });
  await p3;
});

test("a GA 403 for scopes (refuseScopes) drops the token and makes the next popup ask for consent", async () => {
  const { tm, gis, grant } = harness();
  const p = tm.requestToken(); await tick(); grant(0, "T"); await p;
  tm.refuseScopes("T");
  assert.equal(tm.token, null);
  tm.requestToken().catch(() => {});
  await tick();
  assert.equal(gis.clients[1].asked[0].prompt, "consent");
});
