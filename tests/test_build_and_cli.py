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
