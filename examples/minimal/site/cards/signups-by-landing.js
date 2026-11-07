// An example plugin card: where the people who signed up first landed.
//
// A plugin module's default export receives the dashboard's plugin API and
// defines cards. Each card loads its data from GA through ctx.ga (cached and
// rate-limited with every other card), and renders into its own body.
export default function (bizdash) {
  bizdash.defineCard({
    id: "signups-by-landing",
    title: "Sign-ups by landing page",
    span: 6,                      // half the width at desktop size
    after: "funnel",              // placed right after a core card
    followsSegment: true,         // shows "Traffic filter: ..." and follows the control
    async load(ctx) {
      // Whole days, like the core breakdowns: a count of people has no honest
      // hourly form. ctx.range.dateRanges is the day-granular span for this range.
      const body = {
        dateRanges: ctx.range.dateRanges,
        dimensions: [{ name: "landingPage" }],
        metrics: [{ name: "totalUsers" }],
        dimensionFilter: ctx.filters.and(ctx.filters.events(["sign_up"]), ctx.internal.exclusion()),
        orderBys: [{ metric: { metricName: "totalUsers" }, desc: true }],
        limit: 10,
      };
      // With a traffic rule configured, ask for the three segments in the same
      // request and keep one view per segment; switching then costs no request.
      if (!ctx.comparisons) return { rows: ctx.rows.gaRows(await ctx.ga.runReport(body), ["landingPage"], ["totalUsers"]) };
      const p = await ctx.ga.runReport({ ...body, comparisons: ctx.comparisons });
      const bySeg = ctx.rows.gaRowsByComparison(p, ["landingPage"], ["totalUsers"]);
      return { segments: Object.fromEntries(Object.entries(bySeg).map(([seg, rows]) => [seg, { rows }])) };
    },
    render(body, data, ctx) {
      const { rows } = ctx.segments.view(data);
      body.textContent = "";
      body.appendChild(ctx.ui.el("p", "sub", `People who signed up, by the page their session started on, ${ctx.range.daySpan}.`));
      if (!rows.length) { body.appendChild(ctx.ui.el("div", "empty", "No sign-ups in this range.")); return; }
      const max = Math.max(...rows.map(r => r.totalUsers));
      for (const r of rows) body.appendChild(ctx.ui.hbarRow(r.landingPage, r.totalUsers, max));
    },
    snapshot: (data, ctx) => ctx.segments.view(data).rows.map(r => `${r.landingPage}\t${r.totalUsers}`).join("\n"),
  });
}
