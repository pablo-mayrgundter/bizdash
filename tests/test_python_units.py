"""The package's pure parts: spikes, rules, config, the demo API."""

import json

import pytest

from bizdash import WEB_DIR
from bizdash.config import ConfigError, load, resolve
from bizdash.demo import DemoAPI, ga_match
from bizdash.rules import comparisons, filter_expression, host_rule, is_nonstandard
from bizdash.spikes import find_episodes, robust_scores

NOW = 1793806200          # 2026-11-04 15:30 UTC, as tests/js/helpers.mjs


def demo_cfg():
    return load()[0]


def test_spikes_flat_then_burst():
    series = [(str(2026010100 + i), 20 + i % 3) for i in range(16 * 24)]
    assert find_episodes(robust_scores(series)) == []
    for i in (370, 371, 372):
        series[i] = (series[i][0], 200)
    eps = find_episodes(robust_scores(series))
    assert len(eps) == 1 and len(eps[0]["hours"]) == 3 and eps[0]["peak"] == 200
    assert find_episodes(robust_scores(series), min_extra=500) == []


def test_rule_compiler_agrees_with_evaluator():
    rule = {"any": [{"dim": "hostName", "notIn": ["www.example.com"]}, {"dim": "country", "in": ["Atlantis"]}]}
    rows = [{"hostName": "www.example.com", "country": "Utopia"}, {"hostName": "localhost", "country": "Utopia"},
            {"hostName": "WWW.example.com", "country": "Atlantis"}]
    for row in rows:
        assert is_nonstandard(row, rule) == ga_match(filter_expression(rule), row.get)
    assert is_nonstandard({"country": "Utopia"}, rule) is None
    assert [c["name"] for c in comparisons(host_rule(["a"]))] == ["all", "nonstandard", "standard"]


def test_config_defaults_and_errors():
    cfg, errors, warnings = resolve({})
    assert errors == [] and cfg["funnel"] is None
    _, errors, warnings = resolve({"propertyId": "G-1", "plugins": ["https://x/y.js"], "oops": 1})
    assert any("numeric" in e for e in errors) and any("plugin" in e for e in errors)
    assert warnings == ['unknown key "oops" ignored']


def test_config_load_rejects_invalid(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"funnel": {"steps": []}}))
    with pytest.raises(ConfigError):
        load(p)


def test_demo_dedup_and_shapes():
    api = DemoAPI(demo_cfg(), now=NOW)
    week = [{"startDate": "6daysAgo", "endDate": "today"}]
    st, total = api.post("runReport", {"dateRanges": week, "metrics": [{"name": "activeUsers"}]})
    st, hours = api.post("runReport", {"dateRanges": week, "dimensions": [{"name": "dateHour"}],
                                       "metrics": [{"name": "activeUsers"}], "limit": 100000})
    assert int(total["rows"][0]["metricValues"][0]["value"]) < sum(int(r["metricValues"][0]["value"]) for r in hours["rows"])
    st, p = api.post("runRealtimeReport", {"metrics": [{"name": "screenPageViews"}]})
    assert st == 400
    st, p = api.post("runReport", {"dateRanges": week, "dimensions": [{"name": "customEvent:nope"}],
                                   "metrics": [{"name": "eventCount"}]})
    assert st == 400 and "not a valid dimension" in p["error"]["message"]
    st, p = api.post("nope", {})
    assert st == 404


def test_packaged_web_is_complete():
    for rel in ("index.html", "styles.css", "api/meta", "bizdash.config.json", "src/app.js", "src/defaults.json"):
        assert (WEB_DIR / rel).is_file(), rel
    assert json.loads((WEB_DIR / "api" / "meta").read_text()) == {"static": True}


def test_the_three_version_strings_agree():
    """pyproject (PEP 440), the package and the page (semver) name one release,
    mapped as release.yml maps a tag to pyproject's version."""
    import re

    from bizdash import __version__
    pyproject = re.search(r'^version = "([^"]+)"', (WEB_DIR.parent.parent / "pyproject.toml").read_text(), re.M)[1]
    page = re.search(r'export const VERSION = "([^"]+)"', (WEB_DIR / "src" / "dashboard.js").read_text())[1]
    as_pep440 = re.sub(r"-alpha\.?(\d+)$", r"a\1", re.sub(r"-beta\.?(\d+)$", r"b\1", re.sub(r"-rc\.?(\d+)$", r"rc\1", page)))
    assert pyproject == __version__ == as_pep440
    changelog = (WEB_DIR.parent.parent / "CHANGELOG.md").read_text()
    assert f"\n## {page}\n" in changelog
