"""The two non-demo transports end to end: behind `bizdash serve`, and
browser-direct with a (fake) Google sign-in against a GA4 API answered by the
synthetic Data API."""

import json
import threading

import pytest
from werkzeug.serving import make_server

from bizdash.config import load
from bizdash.demo import DemoAPI
from bizdash.gaclient import DemoClient
from bizdash.server import create_app

pytestmark = pytest.mark.browser

FAKE_GIS = """
window.google = { accounts: { oauth2: { initTokenClient: cfg => ({
  requestAccessToken: () => setTimeout(() => cfg.callback({ access_token: "tok-1", expires_in: 3600 }), 10),
}) } } };
"""


@pytest.fixture
def served():
    app = create_app(DemoClient(load()[0]))
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}", app
    srv.shutdown()


def test_behind_the_server(open_page, served):
    url, app = served
    page = open_page(f"{url}/")
    assert page.evaluate("bizdash.ga.mode") == "server"
    assert page.locator("#demoBadge").is_visible()          # the server says it is on demo data
    assert not page.locator(".card-error").count()
    cache = app.extensions["bizdash_cache"]
    assert cache.misses > 10


def test_browser_direct_with_sign_in(browser, static_url):
    api = DemoAPI(load()[0])
    seen = []

    def answer(route):
        req = route.request
        method = req.url.rsplit(":", 1)[1]
        seen.append((req.url, req.headers.get("authorization")))
        status, payload = api.post(method, json.loads(req.post_data))
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(FAKE_GIS)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("https://analyticsdata.googleapis.com/**", answer)
    page.route("https://openidconnect.googleapis.com/**",
               lambda r: r.fulfill(status=200, content_type="application/json", body='{"email": "viewer@example.com"}'))
    page.goto(f"{static_url}/index.html?client_id=test-client&property=987654")
    page.wait_for_function("document.getElementById('signin').hidden === false")
    page.click("#signin")
    page.wait_for_function("window.bizdash && document.querySelectorAll('.card').length > 5 "
                           "&& !document.querySelector('.card.loading')", timeout=30000)
    assert page.evaluate("bizdash.ga.mode") == "direct"
    assert seen and all("/properties/987654:" in u and a == "Bearer tok-1" for u, a in seen)
    assert page.evaluate("JSON.parse(localStorage.getItem('bizdash.token')).token") == "tok-1"
    assert page.evaluate("bizdash.ga.quota.tokensPerHour.remaining") > 0
    assert "GA quota" in page.text_content("#pageFoot")
    ctx.close()
    assert not errors
