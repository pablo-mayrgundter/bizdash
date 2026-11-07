"""``bizdash snapshot``: GA4 data into a local DuckDB file for ad hoc SQL.

Tables (hourly grain): hourly, hourly_source, hourly_page, hourly_geo,
hourly_device, and spikes (detected episodes, as on the dashboard).

    python -m bizdash snapshot --days 60 --db ga.duckdb
    duckdb ga.duckdb "SELECT * FROM spikes ORDER BY start_ts DESC"

Note that active_users is a distinct count per hour: summing it over hours
counts a returning visitor once per hour (see README, "Window semantics").
"""

from datetime import datetime

from .spikes import find_episodes, robust_scores

METRICS = ["activeUsers", "sessions", "screenPageViews", "eventCount"]
COLUMNS = ["active_users", "sessions", "page_views", "event_count"]
PULLS = [
    ("hourly", [("dateHour", "date_hour")]),
    ("hourly_source", [("dateHour", "date_hour"), ("sessionSource", "source"), ("sessionMedium", "medium")]),
    ("hourly_page", [("dateHour", "date_hour"), ("pagePath", "page_path")]),
    ("hourly_geo", [("dateHour", "date_hour"), ("country", "country")]),
    ("hourly_device", [("dateHour", "date_hour"), ("deviceCategory", "device")]),
]


def _ts(dh):
    return datetime.strptime(dh, "%Y%m%d%H")


def _load(con, table, rows, dims):
    cols = [c for _, c in dims] + COLUMNS
    data = [[_ts(r["dateHour"]) if a == "dateHour" else r.get(a, "") for a, _ in dims]
            + [r.get(m, 0) for m in METRICS] for r in rows]
    con.execute(f"DROP TABLE IF EXISTS {table}")
    defs = ", ".join(f"{c} TIMESTAMP" if c == "date_hour" else f"{c} DOUBLE" if c in COLUMNS else f"{c} VARCHAR"
                     for c in cols)
    con.execute(f"CREATE TABLE {table} ({defs})")
    if data:
        con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(cols))})", data)
    return len(data)


def snapshot(client, db_path, days=60, spikes_cfg=None, log=print):
    import duckdb
    spikes_cfg = spikes_cfg or {}
    con = duckdb.connect(str(db_path))
    counts = {}
    for table, dims in PULLS:
        rows = client.run_report([a for a, _ in dims], METRICS, f"{days}daysAgo", "today")
        counts[table] = _load(con, table, rows, dims)
        log(f"  {table}: {counts[table]} rows")
    series = con.execute("SELECT strftime(date_hour, '%Y%m%d%H'), active_users FROM hourly ORDER BY date_hour").fetchall()
    scored = robust_scores(series, lookback_days=spikes_cfg.get("lookbackDays", 14))
    episodes = find_episodes(scored, z=spikes_cfg.get("z", 4.0), min_extra=spikes_cfg.get("minExtra", 15))
    con.execute("DROP TABLE IF EXISTS spikes")
    con.execute("CREATE TABLE spikes (start_ts TIMESTAMP, end_ts TIMESTAMP, hours INT, peak_users DOUBLE, "
                "peak_z DOUBLE, baseline DOUBLE, extra_users DOUBLE)")
    for e in episodes:
        con.execute("INSERT INTO spikes VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [_ts(e["start"]), _ts(e["end"]), len(e["hours"]), e["peak"], e["peak_z"], e["baseline"], e["extra"]])
    counts["spikes"] = len(episodes)
    log(f"  spikes: {len(episodes)} episodes")
    con.close()
    return counts
