"""``bizdash bots``: calibrate the traffic rule against the live property.

Runs the questions that decide whether a rule is sound, read-only, and writes
every raw response to one JSON file for analysis:

  share        the segments over time, with GA's own de-duplicated totals
  leakage      people the rule flags who reached a conversion event
               (the config's traffic.leakEvents, else every funnel event)
  misses       direct and unassigned traffic the rule does NOT flag
  fingerprint  the flagged segment's hosts, cities, screens, OS, browsers,
               landing pages, and the same for everyone else as a baseline

Write --out outside any repository: traffic numbers do not belong in one.
"""

import json

from .rules import comparisons, filter_expression

ENGAGEMENT = ["activeUsers", "newUsers", "sessions", "engagedSessions", "engagementRate",
              "userEngagementDuration", "screenPageViewsPerSession"]
CHANNEL = "firstUserDefaultChannelGroup"


def _in(field, values):
    return {"filter": {"fieldName": field, "inListFilter": {"values": list(values)}}}


def requests_for(cfg, days=90):
    t = cfg["traffic"]
    rule = filter_expression(t["rule"])
    cmp = comparisons(t["rule"])
    rng = [{"startDate": f"{days - 1}daysAgo", "endDate": "today"}]

    def dims(*n):
        return [{"name": x} for x in n]

    def mets(*n):
        return [{"name": x} for x in n]
    leak = list(t.get("leakEvents") or [])
    if not leak and cfg.get("funnel"):
        leak = sorted({e for s in cfg["funnel"]["steps"] for e in s["events"]})
    reqs = {
        "share_date": {"dateRanges": rng, "dimensions": dims("date"), "metrics": mets(*ENGAGEMENT),
                       "comparisons": cmp, "metricAggregations": ["TOTAL"], "limit": 10000},
        "share_window": {"dateRanges": rng, "metrics": mets(*ENGAGEMENT), "comparisons": cmp,
                         "metricAggregations": ["TOTAL"]},
        "share_channel": {"dateRanges": rng, "dimensions": dims(CHANNEL), "metrics": mets("totalUsers"),
                          "comparisons": cmp, "metricAggregations": ["TOTAL"], "limit": 100},
        "misses_country_city": {"dateRanges": rng, "dimensions": dims("country", "city"), "metrics": mets(*ENGAGEMENT),
                                "dimensionFilter": {"andGroup": {"expressions": [
                                    {"notExpression": rule}, _in(CHANNEL, ["Direct", "Unassigned"])]}},
                                "limit": 5000},
        "hosts_all": {"dateRanges": rng, "dimensions": dims("hostName"), "metrics": mets(*ENGAGEMENT), "limit": 1000},
    }
    if leak:
        reqs["leakage"] = {"dateRanges": rng, "dimensions": dims("eventName", "country", "city", "firstUserSource"),
                           "metrics": mets("totalUsers", "eventCount"),
                           "dimensionFilter": {"andGroup": {"expressions": [rule, _in("eventName", leak)]}},
                           "limit": 1000}
    for d in t.get("fingerprint") or []:
        order = [{"metric": {"metricName": "activeUsers"}, "desc": True}]
        reqs[f"fingerprint_{d}"] = {"dateRanges": rng, "dimensions": dims(d), "metrics": mets(*ENGAGEMENT),
                                    "dimensionFilter": rule, "limit": 1000, "orderBys": order}
        reqs[f"baseline_{d}"] = {"dateRanges": rng, "dimensions": dims(d), "metrics": mets(*ENGAGEMENT),
                                 "dimensionFilter": {"notExpression": rule}, "limit": 1000, "orderBys": order}
    for body in reqs.values():
        body["returnPropertyQuota"] = True
    return reqs


def calibrate(client, cfg, out_path, days=90, only=None, log=print):
    if not cfg.get("traffic"):
        raise ValueError("the config has no traffic rule to calibrate")
    out = {"property": client.property_id, "days": days, "rule": cfg["traffic"]["rule"],
           "rev": cfg["traffic"]["rev"], "results": {}}
    for name, body in requests_for(cfg, days).items():
        if only and name not in only:
            continue
        status, payload = client.post("runReport", body)
        out["results"][name] = {"request": body, "status": status, "response": payload}
        used = (payload.get("propertyQuota") or {}).get("tokensPerDay", {}).get("consumed") if status == 200 else None
        log(f"{name:36s} {status} rows={payload.get('rowCount', '-') if status == 200 else '-'} tokens={used}")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1)
    log(f"wrote {out_path}")
    return out
