"""bizdash build, snapshot and bots, and the CLI around them."""

import json

import pytest

from bizdash.bots import calibrate, requests_for
from bizdash.build import build_site
from bizdash.cli import main
from bizdash.config import ConfigError, load
from bizdash.gaclient import DemoClient


def test_build_overlays_a_site(tmp_path):
    site = tmp_path / "site"
    (site / "cards").mkdir(parents=True)
    (site / "bizdash.config.json").write_text(json.dumps({"title": "Mine", "plugins": ["cards/a.js"]}))
    (site / "cards" / "a.js").write_text("export const cards = [];")
    out = tmp_path / "out"
    assert build_site(out, site) == []
    assert json.loads((out / "bizdash.config.json").read_text())["title"] == "Mine"
    assert (out / "src" / "app.js").is_file() and (out / "cards" / "a.js").is_file()
    assert json.loads((out / "version.json").read_text())["bizdash"]
    with pytest.raises(FileExistsError):
        build_site(out, site)


def test_build_refuses_a_missing_plugin_and_warns_on_core_overrides(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "bizdash.config.json").write_text(json.dumps({"plugins": ["cards/missing.js"]}))
    with pytest.raises(ConfigError, match="missing.js"):
        build_site(tmp_path / "o1", site)
    (site / "bizdash.config.json").write_text("{}")
    (site / "styles.css").write_text("/* mine */")
    assert any("core file" in w for w in build_site(tmp_path / "o2", site))


def test_snapshot_demo(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from bizdash.snapshot import snapshot
    counts = snapshot(DemoClient(load()[0]), tmp_path / "s.duckdb", days=10, log=lambda m: None)
    assert counts["hourly"] > 200 and counts["spikes"] >= 1
    con = duckdb.connect(str(tmp_path / "s.duckdb"))
    assert con.execute("SELECT count(*) FROM hourly_source").fetchone()[0] > 0


def test_bots_calibration_runs_the_rule_against_demo(tmp_path):
    cfg = load()[0]
    reqs = requests_for(cfg, days=7)
    assert "leakage" in reqs and "fingerprint_hostName" in reqs
    out = calibrate(DemoClient(cfg), cfg, tmp_path / "cal.json", days=7, log=lambda m: None)
    assert all(r["status"] == 200 for r in out["results"].values()), {k: r["status"] for k, r in out["results"].items()}
    hosts = out["results"]["fingerprint_hostName"]["response"]["rows"]
    assert hosts[0]["dimensionValues"][0]["value"] == "localhost"
    assert "rows" not in out["results"]["leakage"]["response"]         # no flagged person converted


def test_cli_check_and_build(tmp_path, capsys):
    main(["check"])
    assert "config ok" in capsys.readouterr().out
    main(["build", "--out", str(tmp_path / "site")])
    assert (tmp_path / "site" / "index.html").is_file()
    bad = tmp_path / "bad.json"
    bad.write_text('{"ranges": []}')
    with pytest.raises(SystemExit):
        main(["check", "--config", str(bad)])


def _check(tmp_path, capsys, cfg, files=(), site=True):
    d = tmp_path / "site"
    d.mkdir(exist_ok=True)
    (d / "bizdash.config.json").write_text(json.dumps(cfg))
    for f in files:
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_text("export default () => {};")
    args = ["check", "--site", str(d)] if site else ["check", "--config", str(d / "bizdash.config.json")]
    try:
        main(args)
    except SystemExit:
        return capsys.readouterr().err
    assert "config ok" in capsys.readouterr().out
    return ""


def test_check_validates_demo(tmp_path, capsys):
    ok = {"demo": {"events": [{"name": "a", "rate": 0.5, "params": {"kind": ["x", "y"]}},
                              {"name": "b", "rate": 1, "requires": "a", "repeat": 2}]}}
    assert _check(tmp_path, capsys, ok) == ""
    err = _check(tmp_path, capsys, {"demo": {"events": [
        {"name": "b", "rate": 0.2, "requires": "a"},
        {"name": "a", "rate": 1.5},
        {"name": "c", "rate": -0.1, "requires": "page_view"},
        {"name": "d", "rate": "0.3", "params": {"plan": [1, "pro"], "tier": []}},
        {"rate": 0.1},
    ], "usersPerHour": 0}})
    assert 'demo.events[0] "b": requires "a", which is listed after it; list "a" first' in err
    assert 'demo.events[1] "a": rate must be a number from 0 to 1 (a probability), not 1.5' in err
    assert 'demo.events[2] "c": rate must be a number from 0 to 1 (a probability), not -0.1' in err
    assert 'demo.events[2] "c": requires "page_view", which is not an event listed before it' in err
    assert 'demo.events[3] "d": rate must be a number from 0 to 1 (a probability), not "0.3"' in err
    assert 'demo.events[3] "d": params.plan values must be strings, as GA reports them (write "1", not 1)' in err
    assert 'demo.events[3] "d": params.tier must be a non-empty list of values' in err
    assert "demo.events[4] needs a name" in err
    assert "demo.usersPerHour must be a positive number, not 0" in err
    # `build` refuses the same demo{}
    site = tmp_path / "site"
    with pytest.raises(ConfigError, match="rate must be a number from 0 to 1"):
        build_site(tmp_path / "out", site)


def test_check_finds_missing_plugins_relative_to_the_site(tmp_path, capsys):
    cfg = {"plugins": ["cards/here.js", "cards/gone.js"]}
    err = _check(tmp_path, capsys, cfg, files=["cards/here.js"])
    assert "plugin cards/gone.js does not exist in" in err and "here.js" not in err
    err = _check(tmp_path, capsys, cfg, files=["cards/here.js"], site=False)     # --config: its own directory
    assert "plugin cards/gone.js does not exist in" in err
    assert _check(tmp_path, capsys, {"plugins": ["cards/here.js"]}) == ""
    assert _check(tmp_path, capsys, {"plugins": ["cards/here.js"]}, site=False) == ""


def test_demo_params_that_are_numbers_do_not_crash_either_demo():
    from bizdash.demo import DemoAPI
    cfg = load()[0]
    cfg["demo"] = {**cfg["demo"], "events": [{"name": "item_open", "rate": 0.5, "params": {"size": [1, 2.0, True]}}]}
    status, p = DemoAPI(cfg, now=1793806200).post("runReport", {
        "dateRanges": [{"startDate": "today", "endDate": "today"}], "dimensions": [{"name": "customEvent:size"}],
        "metrics": [{"name": "eventCount"}], "dimensionFilter": {"filter": {"fieldName": "eventName",
        "inListFilter": {"values": ["item_open"]}}}})
    assert status == 200
    assert sorted(r["dimensionValues"][0]["value"] for r in p["rows"]) == ["1", "2", "true"]
