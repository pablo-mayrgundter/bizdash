# bizdash

A reusable Google Analytics 4 bizdev dashboard: realtime and hourly history
from the GA4 Data API, spike detection and drill-down, breakdowns, a funnel by
first channel, recent conversions, key-event tiles, and a rule that splits
non-standard traffic (bots, local copies, previews) from the rest.

It runs as a **static single-page app** (each viewer signs in with Google;
nothing secret is deployed) or behind a small **Python server** that holds the
credentials and caches GA for everyone. A deployment is one config file plus
optional plugin cards, pinned to a bizdash release.

- [Quick start](#quick-start) · [Demo](#demo) · [Auth](#auth) · [Config](#config)
  · [Plugins](#plugins) · [Deploying](#deploying) · [Window semantics](#window-semantics)
  · [Python tools](#python-tools) · [Architecture](#architecture) · [Tests](#tests)

## Quick start

```sh
pip install "git+https://github.com/pablo-mayrgundter/bizdash@v0.1.0"   # or @<commit sha>
python -m bizdash serve --demo          # http://127.0.0.1:5077, synthetic data
```

Or, with no Python at all, serve `bizdash/web/` from any static file server and
open `index.html?demo=1`.

## Demo

`?demo=1` (static) and `--demo` (server) answer every query from a
**synthetic GA4 Data API**: a deterministic population of visitors, sessions
and events, generated from the config's `demo` section. Because the demo
answers *queries*, not cards, every card, plugin cards included, runs the same
code against it as against GA, and de-duplicated counts behave as in GA (users
over a day are fewer than the sum of users per hour).

The demo property is in UTC. It has three injected episodes to find: a
referral spike about 76 hours ago, a single-country direct burst about 14 hours
ago, and an off-site burst about 20 hours ago on a host that is not the site.
It refuses a few things on purpose, as GA does, so the gap paths are exercised:
realtime has no `screenPageViews` and no `hostName`, and an unregistered
`customEvent:` dimension is a 400.

The browser demo (`src/ga/demo.js`) and the Python one (`bizdash/demo.py`)
produce identical payloads; a test holds them together.

## Auth

**Static hosting (GitHub Pages).** The page calls the GA4 Data API from the
browser with a Google Identity Services token, scope
`analytics.readonly`. Data access is whatever GA grants the viewer's own
Google account. One-time setup, in any Google Cloud project:

1. *APIs & Services → Library*: enable the **Google Analytics Data API**.
2. *APIs & Services → Credentials → Create credentials → OAuth client ID →
   Web application*. Add your Pages origin (e.g. `https://<user>.github.io`)
   and `http://localhost:8080` as authorized JavaScript origins. No redirect
   URIs are needed.
3. Configure the consent screen (Internal on Workspace; otherwise External
   with your team as test users).
4. Put the client ID and the numeric property ID in `bizdash.config.json`.
   The OAuth client ID is public by design.

Either can be overridden per browser with `?client_id=…&property=…`; the
values are remembered in localStorage and scrubbed from the address bar.
`?switch_account=1` forgets the session and shows the account chooser.

The token (about an hour) is kept in localStorage, renewed silently a few
minutes before it expires, and renewed on demand after a 401. The renewal
logic is a small state machine in `src/ga/auth.js` with tests for each
ordering that matters: concurrent renewals coalesce onto one popup, a newer
request supersedes an older one, a stale grant never overwrites a newer token,
a late grant is kept, and the remembered account (`hint`) changes only when a
new identity actually lands.

**Behind `bizdash serve`.** The server reads the Data API with Application
Default Credentials (`gcloud auth application-default login
--scopes=https://www.googleapis.com/auth/analytics.readonly,https://www.googleapis.com/auth/cloud-platform`),
`GOOGLE_APPLICATION_CREDENTIALS`, or a base64 service-account key in
`GA_SERVICE_ACCOUNT_JSON_B64` (decoded in memory, never written). The service
account needs Viewer access on the property. The page detects the server via
`api/meta`; static builds ship `api/meta` as `{"static": true}` so the probe
is a 200 rather than a logged 404.

## Config

One file per deployment, `bizdash.config.json`, next to `index.html`. It is
merged over `src/defaults.json`; everything is optional except what a feature
needs. The packaged `bizdash/web/bizdash.config.json` is the demo deployment
and a complete example.

| key | what |
|---|---|
| `title` | page title and heading |
| `propertyId`, `clientId` | the GA4 property (numeric) and the OAuth client ID (static hosting) |
| `timezoneNote` | the sentence under the chart saying which clock hours are in |
| `ranges`, `defaultRange` | range buttons, in minutes. `30` is the realtime range |
| `events` | `{name: {label, since, unit}}`. `since` (YYYY-MM-DD) is when the event started being sent: hours before it are *unknown*, not zero |
| `chart.secondary` | an event drawn on the chart's right axis (and as a tile) |
| `tiles` | key-event tiles: `[{event, label, metric: "totalUsers" \| "eventCount"}]`, each with when it last happened |
| `breakdowns` | `[{key, title, dimension, pairEvent}]`. `pairEvent` draws a second bar (that event's count) on the same scale |
| `realtime.tops` | the realtime card's lists: `[{key, title, dimension}]` |
| `funnel` | `{title, steps: [{key, label, short, events, filter, fallback: {events, filter, note}}], lookbackDays, channelDimension, channels, channelBuckets}` |
| `conversions` | `{title, events, lookbackDays, cap}`: the recent-conversions list |
| `internalUsers` | `{dimension, ids: {id: label}, label}`: the deployment's own people, by an id carried in an event-scoped custom dimension. Left out of the funnel and tiles; listed and labelled elsewhere |
| `traffic` | `{siteHosts, rule, label, labels, default, fingerprint, leakEvents}`: the non-standard traffic rule and the segment control |
| `spikes` | `{z, minExtra, lookbackDays}` |
| `links` | `{ga, errorTracker, extra}`: `ga` and `errorTracker` are URL templates with `{placeholders}` (`errorTracker` is for plugins, e.g. `https://errors.example/search?q={id}`); `extra` is `[{label, url}]` shown in the footer |
| `layout` | `{order: [cardId...], hide: [cardId...]}` |
| `plugins` | module paths relative to the config, on the same site |
| `refresh`, `ga` | polling intervals; GA concurrency and cache TTLs |
| `demo` | `{siteHost, usersPerHour, events: [{name, rate, repeat, requires, params}], customDimensions}`: the shape of the synthetic data |

`python -m bizdash check --config path` (or `--site DIR`) validates a config
with the same rules the page applies (the Python and JavaScript resolvers are
tested to agree), and then two things the page cannot: that `demo{}` is sound
(each `requires` names an event listed before it, every `rate` is in [0, 1],
parameter values are strings) and that every plugin path exists in the site
directory, the config's own directory by default. `build` checks both too.

**Funnel steps.** Each step is one report: people (`totalUsers`) by first
channel whose events are in `events`, AND the step's own `filter` (any GA4
FilterExpression), AND not an internal id. A step whose report fails (say, it
filters on a custom dimension not registered yet) uses its `fallback` report
and shows the fallback's `note`.

**The traffic rule** is data: `{any|all: [...]}`, `{not: node}` and leaves
`{dim, in: [...]}` / `{dim, notIn: [...]}`. With only `siteHosts`, the rule is
"the hit landed on any other host". The rule's revision (`rev`, a hash unless
set) goes into the footer and every subtitle, so a changed rule is visible.
`bizdash bots` calibrates a rule against the live property.

## Plugins

A plugin is an ES module listed in `plugins`. Its default export receives the
plugin API (or it exports a `cards` array):

```js
export default function (bizdash) {
  bizdash.defineCard({
    id: "my-card",              // unique
    title: "My card",
    span: 6,                    // 1..12 columns at desktop width
    kind: "card",               // "card" | "tile" | "frameless"
    after: "funnel",            // or before: "...", or order: 450
    refresh: "range",           // "range" | "realtime" (every 30 s) | "once"
    followsSegment: true,       // show "Traffic filter: ..." for data with segments
    async load(ctx) { ... return data },          // called when the range changes
    render(body, data, ctx) { ... },              // called after load and on any view change
    snapshot(data, ctx) { return "plain text" },  // for "Copy data"
    error(body, err, ctx) { ... },                // optional custom error view
  });
  bizdash.addHeaderAction({ id, label, title, onClick(ctx) {} });
  bizdash.on("state" | "loaded" | "rendered", fn);
}
```

`render` runs again, from data already held, whenever the segment, the chart
bucket, the window size or the colour scheme changes: no request. Failures are
isolated: a plugin that fails to import, load or render costs its own card,
and the notice names it.

**The context** a card's `load`, `render` and `snapshot` receive:

| `ctx.` | |
|---|---|
| `config`, `mode`, `version` | the resolved config; `"demo"`, `"direct"` or `"server"` |
| `state` | `{minutes, bucket, segment}` |
| `range` | `{minutes, label, tileLabel, isRealtime, dayMinutes, daySpan, start, dateRanges}` |
| `ga` | `runReport(body)`, `runRealtimeReport(body)`, `batchRunReports(requests)`, `parts({key: body})` (each its own failure), `rows(body)`, `quota`, `stats`. Shared cache, in-flight sharing, concurrency cap and the concurrency-429 retry, whatever the transport |
| `core` | the core cards' data, shared and memoized per range: `timeseries()`, `window()` (the anchored `{start, end}`), `breakdowns()`, `funnel()`, `conversions()`, `tiles()`, `realtime()`, `spike(start, end)`, `latest` |
| `comparisons` | the three segment comparisons to attach to a request, or null |
| `segments` | `{enabled, current, label(seg), view(data), applied(data), ranked(body), isNonstandard(row), classifyHosts(hosts)}`. `ranked(body)` runs a top-N report under the comparisons with `body.limit` rows in every segment (GA's own `limit` counts all three segments' rows together); plain without a traffic rule |
| `filters`, `internal` | FilterExpression builders (`and`, `or`, `not`, `inList`, `equals`, `events`) and the internal-user exclusion |
| `rows`, `time`, `format`, `rules` | payload readers, window arithmetic, number formatting, the rule compiler |
| `links` | `ga()`, `errorTracker(vars)`, `fill(template, vars)` |
| `ui` | `el`, `svgEl`, `hbarRow`, `hbarPairRow`, `sparkline`, `dataTable`, `columnChart`, `mainChart`, `gapList`, `notice(msg)` |
| `storage` | localStorage scoped to the card, the mode and (outside the demo) the property, so demo data never shares a real property's keys: `get`, `set`, `remove`, `clear` |
| `card` | the frame: `el`, `body`, `tools`, `tip`, `setTitle`, `setSubtitle`, `setFilter(data)`, `setFoot(text)` |
| `dashboard` | `footnote(cardId, key, text)` (a provenance line under any card), `refresh(id)`, `data(id)`, `setState`, `snapshot()`, `on`, `emit` |

The core cards are defined through this same API (`src/cards/core.js`), so
anything a core card does, a plugin can. `examples/minimal/site/cards/` has a
complete plugin card.

## Deploying

A deployment repository holds only its own files:

```
site/bizdash.config.json
site/cards/*.js
.github/workflows/deploy.yml     # push to main -> GitHub Pages
.github/workflows/preview.yml    # a preview per pull request
```

and calls bizdash's reusable workflow, pinned to a release:

```yaml
jobs:
  deploy:
    uses: pablo-mayrgundter/bizdash/.github/workflows/deploy.yml@v0.1.0
    with:
      version: v0.1.0      # keep equal to the @ref
      site-dir: site
      # preview: true      # in the pull_request workflow
```

The workflow installs that release, runs `python -m bizdash build --site site
--out _site` (the packaged dashboard with `site/` laid over it, config
validated, every plugin present), and publishes `_site` to the `gh-pages`
branch. Previews go to `pr-preview/pr-<n>/` with a sticky comment, and the
main deploy leaves that directory alone. Enable Pages once: *Settings → Pages
→ Deploy from a branch → gh-pages / root*. The caller grants
`contents: write` (and `pull-requests: write` for previews).
`examples/minimal/` is a complete deployment.

Files in `site/` may replace core files (`index.html`, `styles.css`, `src/`),
and `build` warns when they do: such a deployment is tied to the release it
was written against. A deployment-specific stylesheet is better added as a
plugin that inserts a `<link>`.

To upgrade, change the version in the workflows. Releases follow semantic
versioning; release candidates (`v0.1.0-rc.1`) come first so deployments can
build against them before a release is final.

**Pinning by commit.** The `@ref` and `version` also take a full commit SHA,
which is how a deployment builds against a candidate before it is tagged (or
against any commit). Keep the two equal, in both workflows: the reusable
workflow cannot see its own `@ref`, so `version` is what gets installed.

```yaml
    uses: pablo-mayrgundter/bizdash/.github/workflows/deploy.yml@<40-character commit sha>
    with:
      version: <the same sha>
```

`pip install "git+https://github.com/pablo-mayrgundter/bizdash@<sha>"` installs
the same commit locally. Move to the tag once it exists: a tag names a
release, a SHA only a commit. bizdash's own demo is
published from `main` the same way (`pages.yml`); open it with `?demo=1`.

## Window semantics

Two facts about GA4 collide, and getting either wrong makes a dashboard
contradict itself.

**`dateHour` is in the property's timezone.** A window edge derived from
`Date.now()` and formatted in the browser is shifted by the browser/property
offset: a browser hours ahead of the property would cut a "24 h" view short.
The chart's window is instead counted back from **the latest `dateHour` GA
itself returned**, which is "now" in property time by definition and needs no
timezone at all. All hour arithmetic happens in label space (`src/time.js`),
and a test loads the page in three browser timezones and requires the same
window.

**`NdaysAgo` is a date, so `NdaysAgo..today` spans N+1 calendar days.**
`1daysAgo..today` is yesterday 00:00 through now. The obvious correction,
asking for `days - 1`, fails worse at the short end: `0daysAgo..today` is
*today*, which in the property's timezone can be an hour old. Day-granular
reports therefore floor their start at one day back. A day too wide is a small
error; most of a day missing is not.

**A de-duplicated total is its own query.** `activeUsers` counts distinct
people over the range asked for; summing it over `dateHour` rows counts one
person once per hour they visited (and `sessions` is nearly as bad, since a
session straddling the hour lands in both). The **Users** and **Sessions**
tiles come from one extra report with no `dateHour` dimension and a `dateHour`
inList filter naming exactly the hours the chart settled on, so GA
de-duplicates over the window the chart shows. Page views are a plain count
and are summed from the series. If that query fails the tiles read `—`: they
never fall back to the sum.

**New vs returning is its own query, and the two do not add up.** The split is
GA's `newVsReturning`, a separate report (its failure costs the two sub-rows,
not the headline), de-duplicated within each group, so somebody whose first
visit and a later return both fall in the window is in both numbers. The tile
says so rather than scaling the two to fit.

**The arrows compare against the window immediately before this one**, by the
same de-duplicated queries. No comparison fetched shows no arrow, which is not
"no change"; a previous period of zero reads `new`; under half a percent reads
flat.

**Sum over hour, day, week.** The chart can fold the hourly response into days
or weeks (Monday first, property time) with no second query, so it cannot
disagree with the tiles. A summed day of `activeUsers` is traffic volume, not
people per day, and the chart says so. Event counts sum exactly and keep their
unknown-versus-zero distinction (hours before an event's `since` are `null`
and the line lifts its pen). Spike shading and `z` are hourly and stay in the
hour view. A bucket the range cannot fill twice over is disabled, and the
selection falls back to hours.

**The two short ranges are different kinds of thing.**

| | `1 h` | `30 m` |
|---|---|---|
| source | `runReport`, `dateHour` | `runRealtimeReport`, `minutesAgo` |
| bucket | one hour | one minute |
| freshness | GA's latest reported hour | live |
| expected baseline, spikes | yes | no |
| sessions | yes | no realtime form |

`1 h` is one bucket, drawn with a dot, and anchored to GA's latest reported
hour, so the tiles say **latest reported hour**, not "last 1 h". `30 m` cannot
come from the standard report at all: it has no baseline (spike detection says
it *does not run*, which is not *none detected*), realtime answers a smaller
schema, and **every realtime field is its own query**, so a refused field is
named in the notice and renders `—`, never `0`. Realtime points name no
timestamp (`7 min ago`, `now`): `minutesAgo` is relative to the request and
`dateHour` to the property, and minting a wall-clock label would silently mean
one of them. The realtime headlines (30 and 5 minutes) are each their own
de-duplicated query: five minutes is not a slice of thirty.

**Breakdowns, the funnel and key-event tiles are whole days.** A count of
people has no honest hourly form, so under the short ranges they ask for the
same two days the 24 h view does and say *today and yesterday*.

**The funnel.** Rows are how a person *first* arrived
(`firstUserDefaultChannelGroup`, collapsed to Paid, Organic, Direct, Referral,
Other): a later conversion often happens in a direct session, which would
otherwise credit Direct with everything. Total is GA's own de-duplicated count
(`metricAggregations: TOTAL`), never the sum of the rows. A step can pass 100%:
steps are not nested. A step GA has never reported in the lookback reads `—` /
*not seen in GA yet*; one seen before but not in range reads `0`. GA cannot
tell a missing event from an unused one, so the page does not guess "not
instrumented".

**Internal users.** Where a query can exclude the deployment's own ids (event
counts and people on the events that carry the id dimension) it does. User,
session and page-view counts cannot be filtered by an event-scoped id, and the
cards built on them do not pretend to.

**Segments.** The traffic control (All, Standard, Non-standard) rides on GA4
comparisons: one request carries all three and GA de-duplicates each, so
Standard is GA's own count, never All minus Non-standard (a hit-scoped rule
puts some people in both). Switching is a re-render with no request. Each
segment's chart is scored on its own series, so an off-site burst stops being
a spike under Standard. A card that cannot follow the control (a list, or
realtime, which has no comparisons and no `hostName`) says *Traffic filter:
All*. A response without the `comparison` column is not an answer: the page
falls back to the plain report and says the control is off. The
**Non-standard traffic** card shows the segment's size, its engagement beside
the others, a fingerprint (host, city, screen, OS, browser, landing page,
new/returning), and *leakage*: flagged people who reached a funnel step, which
should be zero.

**Gaps in the hourly series.** GA omits an hour nobody visited. The series is
filled with zeros between its first and last hour, because spike scoring reads
"the same hour yesterday" as 24 rows back.

## Python tools

```sh
python -m bizdash serve [--demo] [--site DIR] [--config FILE] [--property ID] [--port 5077]
python -m bizdash build --site DIR --out _site
python -m bizdash check [--config FILE | --site DIR]
python -m bizdash snapshot --days 60 --db ga.duckdb [--demo]      # pip install "bizdash[snapshot]"
python -m bizdash bots --out /outside/the/repo/cal.json [--days 90] [--demo]
```

- **serve** serves the dashboard (a site directory over the packaged one) and
  proxies raw Data API bodies at `api/ga/<method>` with the server's
  credentials, cached 25 s (realtime) and 5 minutes (reports) for every
  viewer. There are no per-card routes, so plugin cards need no server code.
- **snapshot** writes `hourly`, `hourly_source`, `hourly_page`, `hourly_geo`,
  `hourly_device` and `spikes` tables to DuckDB for ad hoc SQL.
- **bots** runs the questions that decide whether a traffic rule is sound
  (segment size, leakage, what it misses, the flagged segment's fingerprint
  against everyone else's) and writes the raw responses for analysis. Keep
  traffic numbers out of repositories.

## Architecture

```
bizdash/web/                     the static SPA: ES modules, no build step
  index.html, styles.css
  bizdash.config.json            the demo deployment's config
  api/meta                       {"static": true}: the server probe's answer on static hosting
  src/app.js                     bootstrap: config, transport, core cards, plugins, polling
  src/config.js, defaults.json   config resolution (mirrored in bizdash/config.py)
  src/dashboard.js               card registry, plugin context, layout, lifecycle
  src/cards/core.js              the core cards, defined through the plugin API
  src/core/reports.js            the core cards' data from GA reports (no DOM)
  src/ga/client.js               cache, concurrency cap, retries, quota; direct/server/demo transports
  src/ga/auth.js                 Google Identity Services token state machine
  src/ga/demo.js                 the synthetic Data API (mirrored in bizdash/demo.py)
  src/ga/rows.js, filter.js      payload readers, FilterExpression builders and evaluator
  src/rules.js, spikes.js, time.js, format.js, random.js, ui/charts.js
bizdash/*.py                     server, build, config, demo, rules, spikes, snapshot, bots, cli
examples/minimal/                a deployment repository
tests/                           Node unit tests (tests/js), Python, parity and browser tests
```

Every request, from a core card or a plugin, goes through one GA client and
one of three transports: **demo** (the synthetic API in the page), **server**
(`api/ga/*`), or **direct** (the Data API with the viewer's token). The page
never asks the server a card-shaped question, so the server and the page
cannot drift into answering different ones.

Security notes: the OAuth client ID and property ID are not secrets. The
access token lives in the viewer's localStorage. `?config=` accepts only a
same-site relative `.json` path, and plugins only same-origin relative module
paths, so a crafted link cannot load code into a signed-in session.

## Tests

```sh
pip install -e ".[dev]"
python -m playwright install chromium     # or point BIZDASH_TEST_CHROMIUM at a Chromium
python -m pytest                          # Node is needed for tests/js and the parity tests
```

The browser tests drive Chromium against `?demo=1`: no credentials, no
network. `node --test tests/js/*.test.mjs` runs the JavaScript unit tests
alone.

## Licence

MIT. See [LICENSE](LICENSE).
