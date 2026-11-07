"""Plugin cards: loading from the config, the context they get, layout,
isolation of failures, and the parts of the API a deployment's cards rely on."""

import json

import pytest

from conftest import settle

pytestmark = pytest.mark.browser

PLUGIN = """
export default function (bizdash) {
  bizdash.defineCard({
    id: "probe", title: "Probe", span: 6, after: "chart", followsSegment: true,
    async load(ctx) {
      const win = await ctx.core.window();
      const p = await ctx.ga.runReport({ dateRanges: [{ startDate: ctx.time.isoDay(win.start), endDate: ctx.time.isoDay(win.end) }],
        dimensions: [{ name: "eventName" }], metrics: [{ name: "eventCount" }],
        dimensionFilter: ctx.filters.and(ctx.filters.events(["item_open"]), ctx.internal.exclusion()) });
      ctx.storage.set("seen", (ctx.storage.get("seen") || 0) + 1);
      return { win, rows: ctx.rows.gaRows(p, ["eventName"], ["eventCount"]), segments: null };
    },
    render(body, data, ctx) {
      body.textContent = "";
      const p = ctx.ui.el("p", "probe", `${data.win.start}..${data.win.end} ${data.rows[0]?.eventCount} ${ctx.state.segment}`);
      body.appendChild(p);
      ctx.card.setFoot("probe footnote");
      ctx.dashboard.footnote("funnel", "probe", "a note from the probe");
      window.__renders = (window.__renders || 0) + 1;
    },
    snapshot: data => `probe ${data.rows.length}`,
  });
  bizdash.addHeaderAction({ id: "hello", label: "Hello", onClick: ctx => { window.__clicked = ctx.config.title; } });
}
"""
BROKEN = """
export const cards = [
  { id: "bad-load", title: "Bad load", order: 150, load: async () => { throw new Error("boom"); }, render() {} },
  { id: "bad-render", title: "Bad render", order: 160, load: async () => 1, render() { throw new Error("crash"); } },
];
"""


def config(**extra):
    base = json.load(open(__import__("conftest").WEB / "bizdash.config.json"))
    base.update(extra)
    return base


def test_a_plugin_card_gets_the_shared_window_and_the_ga_client(open_page, site_server):
    url = site_server({"cards/probe.js": PLUGIN}, config(plugins=["cards/probe.js"]))
    page = open_page(f"{url}/index.html?demo=1")
    order = page.evaluate("bizdash.dash.ordered()")
    assert order[order.index("chart") + 1] == "probe"          # after: "chart"
    win = page.evaluate("bizdash.dash.core.window()")
    text = page.text_content("#card-probe .probe")
    assert text.startswith(f"{win['start']}..{win['end']} ") and text.endswith(" standard")
    assert page.text_content("#card-probe .seg-sub") == "Traffic filter: All"   # data without segments
    assert "probe footnote" in page.text_content("#card-probe .card-foot")
    assert "a note from the probe" in page.text_content("#card-funnel .card-foot")
    assert "## Probe" in page.evaluate("bizdash.dash.snapshot()")
    page.click("button[data-action='hello']")
    assert page.evaluate("window.__clicked") == "Example traffic"
    keys = page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('bizdash.card.probe.'))")
    assert keys == ["bizdash.card.probe.demo.seen"]
    # a view change re-renders the plugin from held data, with no request
    before, renders = page.evaluate("[bizdash.ga.stats.requests, window.__renders]")
    page.click("#segmentToggle button[data-segment='all']")
    assert page.evaluate("bizdash.ga.stats.requests") == before
    assert page.evaluate("window.__renders") > renders
    assert page.text_content("#card-probe .probe").endswith(" all")


def test_demo_storage_never_lands_under_a_configured_property(open_page, site_server):
    url = site_server({"cards/probe.js": PLUGIN}, config(plugins=["cards/probe.js"], propertyId="123456789"))
    page = open_page(f"{url}/index.html?demo=1")
    keys = page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('bizdash.card.'))")
    assert keys == ["bizdash.card.probe.demo.seen"]
    assert not any("123456789" in k for k in keys)


def test_failures_are_isolated_to_their_card(open_page, site_server):
    url = site_server({"cards/broken.js": BROKEN, "cards/syntax.js": "export default function ( {"},
                      config(plugins=["cards/broken.js", "cards/syntax.js", "cards/absent.js"]), check=False)
    page = open_page(f"{url}/index.html?demo=1")
    assert "Bad load unavailable: boom" in page.text_content("#card-bad-load")
    assert "Bad render failed to render: crash" in page.text_content("#card-bad-render")
    notice = page.text_content("#notice")
    assert "cards/syntax.js" in notice and "cards/absent.js" in notice
    assert page.locator("#mainSvg .value-line").count() == 1   # everything else still works
    page.errors.clear()                                          # the broken modules log by design


def test_layout_hide_and_order(open_page, site_server):
    url = site_server({}, config(layout={"hide": ["traffic", "realtime"], "order": ["funnel", "users"]}))
    page = open_page(f"{url}/index.html?demo=1")
    order = page.evaluate("bizdash.dash.ordered()")
    assert order[:2] == ["funnel", "users"]
    assert "traffic" not in order and page.locator("#card-traffic").count() == 0


def test_a_minimal_config_runs_without_funnel_or_traffic(open_page, site_server):
    url = site_server({}, {"title": "Bare"})
    page = open_page(f"{url}/index.html?demo=1")
    assert page.evaluate("bizdash.dash.ordered()") == ["users", "sessions", "chart", "realtime", "breakdown:sources",
                                                        "breakdown:pages", "breakdown:countries", "spikes"]
    assert page.locator("#segmentToggle").is_hidden()
    assert page.text_content("#card-chart .seg-sub") == ""


def test_config_by_url_is_same_site_only(open_page, site_server):
    url = site_server({"alt/other.config.json": json.dumps({"title": "Alternative"})}, config())
    page = open_page(f"{url}/index.html?demo=1&config=alt/other.config.json")
    assert page.text_content("#title") == "Alternative"
    page = open_page(f"{url}/index.html?demo=1&config=https://example.com/x.json")
    assert page.text_content("#title") == "Example traffic"


def test_an_invalid_config_says_why(open_page, site_server):
    url = site_server({}, {"ranges": []}, check=False)
    page = open_page(f"{url}/index.html?demo=1", wait=False)
    page.wait_for_function("document.getElementById('notice').textContent.includes('ranges must be')")


def test_the_minimal_example_deployment(open_page, tmp_path):
    from bizdash.build import build_site
    from conftest import ROOT, StaticServer
    out = tmp_path / "site"
    assert build_site(out, ROOT / "examples" / "minimal" / "site") == []
    srv = StaticServer(out)
    try:
        page = open_page(f"{srv.url}/index.html?demo=1")
        order = page.evaluate("bizdash.dash.ordered()")
        assert order[order.index("funnel") + 1] == "signups-by-landing"
        assert page.locator("#card-signups-by-landing .hbar-row").count() > 0
        assert page.text_content("#title") == "Example.org traffic"
    finally:
        srv.stop()
