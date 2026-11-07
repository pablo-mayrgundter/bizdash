"""bizdash serve: the caching proxy, the meta probe and the site overlay."""

import json

from bizdash.gaclient import DemoClient
from bizdash.server import create_app

BODY = {"dateRanges": [{"startDate": "today", "endDate": "today"}], "metrics": [{"name": "activeUsers"}]}


def client(tmp_path=None, site=None):
    return create_app(DemoClient(), site_dir=site).test_client()


def test_meta_says_demo_and_shadows_the_static_stub():
    r = client().get("/api/meta")
    assert r.json["demo"] is True and r.json["property_id"] == "demo"


def test_proxy_runs_and_caches_raw_bodies():
    app = create_app(DemoClient())
    c = app.test_client()
    a = c.post("/api/ga/runReport", json=BODY)
    b = c.post("/api/ga/runReport", json=dict(reversed(list(BODY.items()))))
    assert a.status_code == 200 and a.json == b.json
    cache = app.extensions["bizdash_cache"]
    assert cache.hits == 1 and cache.misses == 1


def test_proxy_passes_ga_errors_through_and_does_not_cache_them():
    app = create_app(DemoClient())
    c = app.test_client()
    bad = {**BODY, "dimensions": [{"name": "customEvent:unregistered"}]}
    for _ in range(2):
        r = c.post("/api/ga/runReport", json=bad)
        assert r.status_code == 400 and "not a valid dimension" in r.json["error"]["message"]
    assert app.extensions["bizdash_cache"].hits == 0


def test_only_data_api_methods_are_proxied():
    c = client()
    assert c.post("/api/ga/deleteProperty", json={}).status_code == 404
    assert c.post("/api/ga/runReport", data="not json", content_type="application/json").status_code == 400


def test_realtime_is_proxied():
    r = client().post("/api/ga/runRealtimeReport", json={"metrics": [{"name": "activeUsers"}]})
    assert r.status_code == 200 and "rows" in r.json


def test_site_overlay_wins_and_traversal_is_refused(tmp_path):
    (tmp_path / "bizdash.config.json").write_text(json.dumps({"title": "Overlay"}))
    (tmp_path / "cards").mkdir()
    (tmp_path / "cards" / "x.js").write_text("export const cards = [];")
    c = client(site=tmp_path)
    assert c.get("/bizdash.config.json").json == {"title": "Overlay"}
    assert c.get("/cards/x.js").status_code == 200
    assert c.get("/src/app.js").status_code == 200
    assert c.get("/").status_code == 200
    assert c.get("/../pyproject.toml").status_code == 404
    assert c.get("/src/../../config.py").status_code == 404
