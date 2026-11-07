"""The SPA in Chromium on the synthetic Data API (?demo=1)."""

import pytest

from conftest import PHONE, settle

pytestmark = pytest.mark.browser
CORE = ["users", "sessions", "secondary", "tile:sign_up", "tile:purchase", "chart", "realtime", "breakdown:sources",
        "breakdown:pages", "breakdown:countries", "funnel", "conversions", "spikes", "traffic"]


def demo(static_url, query=""):
    return f"{static_url}/index.html?demo=1{query}"


def click(page, selector):
    page.click(selector)
    settle(page)


def test_every_core_card_renders(open_page, static_url):
    page = open_page(demo(static_url))
    assert page.evaluate("bizdash.dash.ordered()") == CORE
    assert page.evaluate("bizdash.ga.mode") == "demo"
    assert not page.locator(".card-error").count()
    assert page.locator("#demoBadge").is_visible()
    assert page.text_content("#title") == "Example traffic"
    assert page.locator("#mainSvg .value-line").count() == 1
    assert page.locator("#funnelTable tbody tr").count() == 6        # five channels and Total
    assert "not seen in GA yet" in page.text_content("#funnelTable")


def test_window_does_not_depend_on_the_browser_timezone(open_page, static_url):
    """Window edges come from GA's own dateHour labels, never the browser clock."""
    seen = []
    for tz in ("America/Los_Angeles", "Europe/Berlin", "Asia/Kolkata"):
        page = open_page(demo(static_url), timezone=tz)
        seen.append(page.evaluate("""async () => { const ts = await bizdash.dash.core.timeseries();
            return [ts.window, ts.points.length, ts.points[0].ts]; }"""))
    assert seen[0] == seen[1] == seen[2]
    assert seen[0][1] == 24


def test_users_tile_is_a_distinct_count_with_its_own_query(open_page, static_url):
    page = open_page(demo(static_url))
    r = page.evaluate("""async () => { const ts = await bizdash.dash.core.timeseries();
        return { users: ts.segments.standard.totals.users,
                 summed: ts.segments.standard.points.reduce((s, p) => s + p.value, 0),
                 shown: document.getElementById('t-users').textContent }; }""")
    assert r["users"] < r["summed"]
    assert r["shown"].replace(",", "") == str(r["users"])
    assert "need not add to the total" in page.text_content("#card-users")


def test_one_hour_is_one_point_labelled_latest_reported_hour(open_page, static_url):
    page = open_page(demo(static_url))
    click(page, "#rangeToggle button[data-minutes='60']")
    assert page.locator("#mainSvg .pt-marker").count() >= 1
    assert "latest reported hour" in page.text_content("#card-users")
    assert page.evaluate("bizdash.dash.core.timeseries().then(t => t.points.length)") == 1


def test_thirty_minutes_is_realtime_with_honest_gaps(open_page, static_url):
    page = open_page(demo(static_url, "&range=30"))
    assert page.text_content("#card-chart h2") == "Active users per minute"
    assert page.locator("#legendBaseline").is_hidden()
    assert "does not run over the realtime window" in page.text_content("#card-spikes")
    assert "page views" in page.text_content("#notice")
    assert page.text_content("#t-sessions") == "—"
    assert "not reported over this window" in page.text_content("#card-users")


def test_sum_over_day_and_week(open_page, static_url):
    page = open_page(demo(static_url))
    assert page.is_disabled("#bucketToggle button[data-bucket='day']")       # 24 h cannot fill two days
    click(page, "#rangeToggle button[data-minutes='10080']")
    hours = page.locator("#mainTable tbody tr").count()
    page.click("#bucketToggle button[data-bucket='day']")
    assert page.text_content("#card-chart h2") == "Active users per day"
    assert page.locator("#mainTable tbody tr").count() < hours / 20
    assert page.locator("#mainSvg .spike-shade").count() == 0
    assert "traffic volume, not distinct people" in page.text_content("#card-chart")
    assert page.is_disabled("#bucketToggle button[data-bucket='week']")
    click(page, "#rangeToggle button[data-minutes='60']")
    page.wait_for_function("document.querySelector('#bucketToggle button[data-bucket=hour]').getAttribute('aria-pressed') === 'true'")


def test_switching_segment_rerenders_without_a_request(open_page, static_url):
    page = open_page(demo(static_url))
    before = page.evaluate("bizdash.ga.stats.requests")
    std_shades = page.locator("#mainSvg .spike-shade").count()
    page.click("#segmentToggle button[data-segment='all']")
    assert page.evaluate("bizdash.ga.stats.requests") == before
    assert page.locator("#mainSvg .spike-shade").count() > std_shades      # the off-site burst is shaded under All
    assert page.text_content("#card-chart .seg-sub") == "Traffic filter: All"
    assert page.text_content("#card-realtime .seg-sub") == "Traffic filter: All"
    assert page.evaluate("localStorage.getItem('bizdash.segment')") == "all"
    page2 = open_page(demo(static_url, "&segment=nonstandard"))
    assert page2.text_content("#card-funnel .seg-sub") == "Traffic filter: Non-standard"


def test_spike_drill_down(open_page, static_url):
    page = open_page(demo(static_url, "&range=10080&segment=all"))
    page.locator("#spikesTable tr.spike-row").first.click()
    page.wait_for_function("document.querySelector('#drill .headline').textContent.includes('top movers')")
    assert page.locator("#drill .hbar-row").count() > 0


def test_header_links_the_configured_property_and_not_the_demo(open_page, static_url):
    page = open_page(demo(static_url))
    assert page.get_attribute("#prop", "href") is None
    page = open_page(f"{static_url}/index.html?client_id=test-client&property=123456", wait=False)
    page.wait_for_function("document.getElementById('signin').hidden === false")
    assert "p123456" in page.get_attribute("#prop", "href")
    assert "client_id" not in page.url                       # scrubbed, remembered in this browser
    assert page.evaluate("localStorage.getItem('bizdash.clientId')") == "test-client"


def test_unconfigured_static_site_points_at_the_demo(open_page, static_url):
    page = open_page(f"{static_url}/index.html", wait=False)
    page.wait_for_function("document.getElementById('notice').textContent.includes('view the demo')")
    assert page.get_attribute("#notice a", "href") == "?demo=1"


def test_copy_data_snapshot(open_page, static_url):
    page = open_page(demo(static_url))
    text = page.evaluate("bizdash.dash.snapshot()")
    for heading in ("## Users", "## Active users per hour", "## Funnel by first channel", "## Detected spikes"):
        assert heading in text


def test_phone_width_has_no_horizontal_scroll(open_page, static_url):
    page = open_page(demo(static_url), width=PHONE)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
