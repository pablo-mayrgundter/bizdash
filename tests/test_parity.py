"""The Python and JavaScript implementations, held together: the same input
must give the same answer. Python serves --demo, snapshots and calibrates; the
page does everything else; they must not drift."""

import json

import pytest

from bizdash.config import load, resolve
from bizdash.demo import DemoAPI
from bizdash.rules import filter_expression, is_nonstandard
from bizdash.spikes import find_episodes, robust_scores
from conftest import WEB, needs_node, run_node

NOW = 1793806200
CFG_RAW = json.loads((WEB / "bizdash.config.json").read_text())
BODIES = [
    ("runReport", {"dateRanges": [{"startDate": "13daysAgo", "endDate": "today"}], "dimensions": [{"name": "dateHour"}],
                   "metrics": [{"name": "activeUsers"}, {"name": "sessions"}, {"name": "screenPageViews"}], "limit": 100000}),
    ("runReport", {"dateRanges": [{"startDate": "29daysAgo", "endDate": "today"}],
                   "dimensions": [{"name": "eventName"}, {"name": "firstUserDefaultChannelGroup"}],
                   "metrics": [{"name": "totalUsers"}, {"name": "eventCount"}, {"name": "engagementRate"}],
                   "metricAggregations": ["TOTAL"]}),
    ("runReport", {"dateRanges": [{"startDate": "2daysAgo", "endDate": "today"}], "dimensions": [{"name": "hostName"}],
                   "metrics": [{"name": "activeUsers"}, {"name": "userEngagementDuration"}],
                   "comparisons": "COMPARISONS", "metricAggregations": ["TOTAL"]}),
    ("runReport", {"dateRanges": [{"startDate": "89daysAgo", "endDate": "today"}],
                   "dimensions": [{"name": "dateHour"}, {"name": "eventName"}, {"name": "customEvent:client_id"}],
                   "metrics": [{"name": "eventCount"}], "dimensionFilter": {"filter": {"fieldName": "eventName",
                   "inListFilter": {"values": ["sign_up", "purchase"]}}},
                   "orderBys": [{"dimension": {"dimensionName": "dateHour"}, "desc": True}], "limit": 200}),
    # numericFilter: every operation, int64Value as a string or a number, doubleValue, and an unknown operation
    *[("runReport", {"dateRanges": [{"startDate": "2daysAgo", "endDate": "today"}], "dimensions": [{"name": "dateHour"}],
                     "metrics": [{"name": "sessions"}], "dimensionFilter": {"filter": {"fieldName": "dateHour",
                     "numericFilter": {"operation": op, "value": value}}}, "limit": 1000})
      for op, value in [("EQUAL", {"int64Value": "2026110312"}), ("LESS_THAN", {"int64Value": 2026110300}),
                        ("LESS_THAN_OR_EQUAL", {"doubleValue": 2026110300.0}), ("GREATER_THAN", {"int64Value": "2026110410"}),
                        ("GREATER_THAN_OR_EQUAL", {"doubleValue": 2026110410.5}), ("BETWEEN", {"int64Value": "1"})]],
    ("runReport", {"dateRanges": [{"startDate": "6daysAgo", "endDate": "today"}], "dimensions": [{"name": "country"}],
                   "metrics": [{"name": "activeUsers"}], "dimensionFilter": {"notExpression": {"filter": {
                   "fieldName": "customEvent:item_kind", "numericFilter": {"operation": "GREATER_THAN",
                   "value": {"int64Value": "0"}}}}}, "limit": 50}),
    # a ranked report under comparisons: the limit counts every segment's rows together, in both
    ("runReport", {"dateRanges": [{"startDate": "29daysAgo", "endDate": "today"}], "dimensions": [{"name": "country"}],
                   "metrics": [{"name": "activeUsers"}], "comparisons": "COMPARISONS",
                   "orderBys": [{"metric": {"metricName": "activeUsers"}, "desc": True}], "limit": 7}),
    ("runRealtimeReport", {"dimensions": [{"name": "minutesAgo"}], "metrics": [{"name": "activeUsers"}]}),
    ("runRealtimeReport", {"dimensions": [{"name": "country"}], "metrics": [{"name": "activeUsers"}, {"name": "eventCount"}]}),
]


def _bodies(cfg):
    from bizdash.rules import comparisons
    out = []
    for m, b in BODIES:
        b = json.loads(json.dumps(b))
        if b.get("comparisons") == "COMPARISONS":
            b["comparisons"] = comparisons(cfg["traffic"]["rule"])
        out.append((m, b))
    return out


@needs_node
def test_demo_api_is_identical_in_both_languages():
    cfg = load()[0]
    bodies = _bodies(cfg)
    js = run_node(f"""
      const {{ createDemoApi }} = await import(WEB + "src/ga/demo.js");
      const {{ resolveConfig }} = await import(WEB + "src/config.js");
      const cfg = resolveConfig({json.dumps(CFG_RAW)}).config;
      const api = createDemoApi(cfg, {{ now: {NOW * 1000} }});
      const out = [];
      for (const [m, b] of {json.dumps(bodies)}) out.push(JSON.parse((await api.post(m, b)).text));
      console.log(JSON.stringify(out));""")
    api = DemoAPI(cfg, now=NOW)
    for (method, body), theirs in zip(bodies, js):
        status, ours = api.post(method, body)
        assert status == 200
        assert ours == theirs, f"{method} {json.dumps(body)[:120]}"


@needs_node
@pytest.mark.parametrize("raw", [
    {},
    CFG_RAW,
    {"propertyId": "properties/123", "chart": {"secondary": {"event": "x", "since": "2026-01-02"}},
     "tiles": ["a", {"event": "b", "metric": "eventCount"}], "internalUsers": {"dimension": "customEvent:id", "ids": ["k"]},
     "traffic": {"rule": {"all": [{"dim": "country", "in": ["Atlantis"]}]}, "default": "all"}},
    {"propertyId": "G-1", "ranges": [], "funnel": {"steps": [{"key": "visitors"}]}, "plugins": ["../x.js"], "extra": 1},
])
def test_config_resolution_is_identical(raw):
    js = run_node(f"""
      const {{ resolveConfig }} = await import(WEB + "src/config.js");
      console.log(JSON.stringify(resolveConfig({json.dumps(raw)})));""")
    cfg, errors, warnings = resolve(raw)
    assert errors == js["errors"]
    assert warnings == js["warnings"]
    if not errors:
        assert cfg == js["config"]


@needs_node
def test_rules_and_spikes_are_identical():
    rule = {"any": [{"dim": "hostName", "notIn": ["www.example.com"]},
                    {"all": [{"dim": "country", "in": ["Atlantis"]}, {"not": {"dim": "city", "in": ["X"]}}]}]}
    rows = [{"hostName": "www.example.com", "country": "Atlantis", "city": "Y"}, {"hostName": "localhost"},
            {"country": "Atlantis"}, {"hostName": "www.example.com", "country": "Atlantis", "city": "X"}]
    series = [[str(2026010100 + i), (i * 7919) % 37 + (300 if i in (400, 401) else 0)] for i in range(18 * 24)]
    js = run_node(f"""
      const R = await import(WEB + "src/rules.js");
      const S = await import(WEB + "src/spikes.js");
      const rule = {json.dumps(rule)};
      const scored = S.robustScores({json.dumps(series)}.map(([ts, value]) => ({{ ts, value }})));
      console.log(JSON.stringify({{ expr: R.trafficFilterExpression(rule),
        flags: {json.dumps(rows)}.map(r => R.isNonstandard(r, rule)),
        scored, episodes: S.findEpisodes(scored) }}));""")
    assert filter_expression(rule) == js["expr"]
    assert [is_nonstandard(r, rule) for r in rows] == js["flags"]
    scored = robust_scores([tuple(p) for p in series])
    assert scored == js["scored"]
    assert find_episodes(scored) == js["episodes"]
