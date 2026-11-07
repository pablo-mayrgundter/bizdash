# Changelog

All notable changes to bizdash. Versions follow semantic versioning; release
candidates are tagged before each release so deployments can build against
them first.

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
