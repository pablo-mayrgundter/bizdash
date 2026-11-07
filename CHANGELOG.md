# Changelog

All notable changes to bizdash. Versions follow semantic versioning; release
candidates are tagged before each release so deployments can build against
them first.

## 0.1.1

- **Sign-in without the Analytics scope.** Google's consent screen lets a
  viewer untick "See and download your Google Analytics data", and sign-in
  still succeeds, with a token that gets "Request had insufficient
  authentication scopes" (403) on every report. Such a grant is now refused
  and not stored, a GA 403 for scopes drops the token, and the dashboard says
  what to tick. The next sign-in asks for consent again so the boxes are
  shown.
- **Release workflow.** `gh release create --target` makes the tag on GitHub;
  the runner needs no git identity and pushes no tag.

## 0.1.0

The first release. Fixes found building deployments on 0.1.0-rc.1:

- **Row limits under comparisons.** GA's `limit` counts every segment's
  rows together, so breakdowns asking for 10 rows gave each segment three or
  four. Ranked reports now ask for limit × comparisons, and a segment that
  larger ones still crowd out of the shared ranking is asked for again alone,
  so each gets its full top N. Whole-report limits (funnel, new vs returning,
  spike drill-down, `bizdash bots`) scale the same way. Plugins get the same
  through `ctx.segments.ranked(body)`, which `examples/minimal`'s card uses.
- **`ctx.storage`** is scoped by mode as well as property, so a demo page
  with a configured property no longer stores synthetic data under the real
  property's key. Keys in the old scheme are no longer read.
- **The Python demo's `numericFilter`**, as the JS demo has it, so a numeric
  filter no longer returns a 400 under `bizdash serve --demo`.
- **`bizdash check`** validates `demo{}` (`requires` naming an event listed
  before it, rates in [0, 1], string parameter values) and that each plugin
  path exists in the site directory; `build` checks `demo{}` too. Numeric
  demo parameter values are coerced to strings rather than crashing the
  Python demo.
- **README:** pinning a deployment by commit SHA (`uses: …@<sha>` with
  `version: <sha>`, kept equal), for release candidates before their tag.

## 0.1.0-rc.1

First release candidate.

### Dashboard (static SPA, no build step)
- Users and Sessions tiles from their own de-duplicated query over the
  chart's anchored window, with GA's new/returning split and arrows against
  the previous window.
- Active users per hour with expected baseline, spike shading, an optional
  secondary event series on its own axis, a data table, and day/week sums.
- Realtime card (per-minute columns, 30- and 5-minute headlines as their own
  queries, top lists), and a 30-minute realtime range alongside 1 h, 24 h,
  7, 30 and 90 days.
- Breakdowns (configurable, with an optional paired event bar), key-event
  tiles, recent conversions, a funnel by first channel with GA's own totals,
  detected spikes with a drill-down against the same hours a week earlier.
- A traffic rule and an All / Standard / Non-standard control on GA4
  comparisons, with a Non-standard traffic card (size, engagement,
  fingerprint, leakage).
- Internal-user exclusion by an id carried in an event-scoped dimension.
- Three transports behind one GA client: browser-direct with Google sign-in,
  the bizdash server, and a synthetic demo Data API (`?demo=1`).
- One config file per deployment; plugin cards through `defineCard()`, the
  same API the core cards use; "Copy data" snapshot of the current view.

### Python package
- `bizdash serve` (caching GA4 proxy, `--demo`), `build`, `check`,
  `snapshot` (DuckDB), `bots` (traffic-rule calibration).

### Deployment
- Reusable GitHub Pages workflow with PR previews; `examples/minimal`.
