"""A synthetic GA4 Data API, number for number the same as web/src/ga/demo.js.

It answers runReport, runRealtimeReport and batchRunReports *bodies* over a
deterministic synthetic population of visitors, sessions and events, so the
server's demo mode, the snapshot and the bot calibration all exercise the same
code paths they use against GA. De-duplicated metrics behave as in GA: users
over a day are fewer than the sum of users per hour.

The demo property's timezone is UTC. See demo.js for the injected episodes and
the deliberate refusals. Only integer arithmetic, +, *, / and sqrt are used,
which IEEE 754 rounds identically in Python and JavaScript; the parity test in
tests/test_demo_parity.py holds the two implementations together.
"""

import json
import math
import re
import time
from datetime import datetime, timezone
from functools import cmp_to_key

HISTORY_DAYS = 120
DIURNAL = [1.45, 1.233, 1.0, 0.767, 0.55, 0.364, 0.221, 0.131, 0.1, 0.131, 0.221, 0.364,
           0.55, 0.767, 1.0, 1.233, 1.45, 1.636, 1.779, 1.869, 1.9, 1.869, 1.779, 1.636]
WEEKLY = [1, 1, 1, 1, 1, 0.75, 0.75]
DECAY = [1.0, 0.659, 0.435, 0.287, 0.189, 0.125, 0.082, 0.054, 0.036]

SOURCES = [
    [["google", "organic", "Organic Search"], 36],
    [["(direct)", "(none)", "Direct"], 26],
    [["bing", "organic", "Organic Search"], 6],
    [["github.com", "referral", "Referral"], 9],
    [["news.example", "referral", "Referral"], 5],
    [["social.example", "referral", "Organic Social"], 6],
    [["google", "cpc", "Paid Search"], 8],
    [["newsletter", "email", "Email"], 4],
]
CAMPAIGN = {"Organic Search": "(organic)", "Organic Social": "(referral)", "Referral": "(referral)",
            "Direct": "(direct)", "Paid Search": "spring_promo", "Email": "weekly_digest"}
COUNTRIES = [
    [["United States", ["New York", "San Francisco", "Austin", "Chicago"]], 40],
    [["India", ["Bengaluru", "Mumbai", "Delhi"]], 15],
    [["Germany", ["Berlin", "Munich"]], 10],
    [["United Kingdom", ["London", "Manchester"]], 9],
    [["Canada", ["Toronto", "Vancouver"]], 7],
    [["Brazil", ["Sao Paulo"]], 6],
    [["France", ["Paris", "Lyon"]], 5],
    [["Japan", ["Tokyo"]], 4],
    [["Australia", ["Sydney"]], 4],
]
PAGES = [["/", 34], ["/items", 20], ["/docs", 14], ["/pricing", 12], ["/blog", 10], ["/about", 6], ["/signup", 4]]
TITLES = {"/": "Home", "/items": "Items", "/docs": "Docs", "/pricing": "Pricing", "/blog": "Blog",
          "/about": "About", "/signup": "Sign up"}
DEVICES = [["desktop", 60], ["mobile", 35], ["tablet", 5]]
BROWSERS = {"desktop": [["Chrome", 64], ["Safari", 14], ["Edge", 12], ["Firefox", 10]],
            "mobile": [["Chrome", 55], ["Safari", 45]], "tablet": [["Safari", 70], ["Chrome", 30]]}
OSES = {"desktop": [[["Windows", "Windows 11"], 38], [["Macintosh", "Macintosh Intel 10.15"], 34],
                    [["Windows", "Windows 10"], 18], [["Linux", "Linux x86_64"], 10]],
        "mobile": [[["Android", "Android 14"], 55], [["iOS", "iOS 18.1"], 45]],
        "tablet": [[["iOS", "iOS 18.1"], 70], [["Android", "Android 14"], 30]]}
SCREENS = {"desktop": [["1920x1080", 40], ["1536x864", 20], ["2560x1440", 15], ["1440x900", 15], ["1366x768", 10]],
           "mobile": [["390x844", 40], ["412x915", 35], ["375x667", 25]],
           "tablet": [["820x1180", 60], ["768x1024", 40]]}

INT_METRICS = {"activeUsers", "totalUsers", "newUsers", "sessions", "engagedSessions", "eventCount",
               "screenPageViews", "userEngagementDuration", "keyEvents"}
FLOAT_METRICS = {"engagementRate", "bounceRate", "screenPageViewsPerSession", "averageSessionDuration",
                 "eventsPerSession", "sessionsPerUser"}
EVENT_SCOPED = {"eventName", "pagePath", "pageTitle", "unifiedScreenName", "pagePathPlusQueryString"}
SESSION_DIMS = {"dateHour", "date", "hour", "dateHourMinute", "dayOfWeek", "sessionSource", "sessionMedium",
                "sessionSourceMedium", "sessionDefaultChannelGroup", "sessionCampaignName", "firstUserSource",
                "firstUserMedium", "firstUserDefaultChannelGroup", "firstUserCampaignName", "landingPage", "hostName",
                "newVsReturning", "country", "city", "deviceCategory", "browser", "operatingSystem",
                "operatingSystemWithVersion", "screenResolution", "platform"}
REALTIME_DIMS = {"minutesAgo", "country", "city", "deviceCategory", "unifiedScreenName", "eventName", "platform",
                 "streamName", "appVersion"}
REALTIME_METRICS = {"activeUsers", "eventCount", "keyEvents"}
M32 = 0xFFFFFFFF


class GAError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---- PRNG, as random.js --------------------------------------------------------

def fnv1a(s):
    h = 0x811C9DC5
    for ch in s:
        h ^= ord(ch)
        h = (h * 0x01000193) & M32
    return h


def rng(seed):
    state = [fnv1a(str(seed))]

    def nxt():
        a = (state[0] + 0x6D2B79F5) & M32
        state[0] = a
        t = a
        t = ((t ^ (t >> 15)) * (t | 1)) & M32
        t = (t ^ ((t + (((t ^ (t >> 7)) * (t | 61)) & M32)) & M32)) & M32
        return ((t ^ (t >> 14)) & M32) / 4294967296
    return nxt


def count_around(r, mean):
    if mean <= 0:
        return 0
    u = r() + r() + r() + r() - 2
    return max(0, math.floor(mean + u * math.sqrt(3 * mean) + 0.5))


def pick(r, table):
    total = 0
    for _, w in table:
        total += w
    x = r() * total
    for v, w in table:
        x -= w
        if x < 0:
            return v
    return table[-1][0]


# ---- GA filter semantics, as filter.js ------------------------------------------

def ga_match(expr, get):
    """GA4 FilterExpression over one row; `get(fieldName)` returns a string or None."""
    if not expr:
        return True
    if "andGroup" in expr:
        return all(ga_match(e, get) for e in expr["andGroup"]["expressions"])
    if "orGroup" in expr:
        return any(ga_match(e, get) for e in expr["orGroup"]["expressions"])
    if "notExpression" in expr:
        return not ga_match(expr["notExpression"], get)
    f = expr["filter"]
    value = get(f["fieldName"])
    if value is None:
        return False
    value = str(value)
    if "inListFilter" in f:
        vals = [str(v) for v in f["inListFilter"].get("values", [])]
        if f["inListFilter"].get("caseSensitive"):
            return value in vals
        return value.lower() in {v.lower() for v in vals}
    if "stringFilter" in f:
        sf = f["stringFilter"]
        mt, want = sf.get("matchType", "EXACT"), str(sf["value"])
        if not sf.get("caseSensitive"):
            value, want = value.lower(), want.lower()
        if mt == "FULL_REGEXP":
            return re.fullmatch(want, value) is not None
        if mt == "PARTIAL_REGEXP":
            return re.search(want, value) is not None
        return {"EXACT": value == want, "BEGINS_WITH": value.startswith(want),
                "ENDS_WITH": value.endswith(want), "CONTAINS": want in value}[mt]
    if "numericFilter" in f:
        nf = f["numericFilter"]
        v = nf.get("value") or {}
        n, want = _js_number(value), _js_number(v["int64Value"] if v.get("int64Value") is not None else v.get("doubleValue"))
        return {"EQUAL": n == want, "LESS_THAN": n < want, "LESS_THAN_OR_EQUAL": n <= want,
                "GREATER_THAN": n > want, "GREATER_THAN_OR_EQUAL": n >= want}.get(nf.get("operation"), False)
    if "emptyFilter" in f:
        return value == ""
    raise GAError(400, f"unsupported filter {json.dumps(f)}")


_JS_DECIMAL = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def _js_number(v):
    """JavaScript's unary + on a filter operand: "" is 0, junk is NaN."""
    if v is None:
        return math.nan
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return 0.0
    if _JS_DECIMAL.fullmatch(s):
        return float(s)
    if s in ("Infinity", "+Infinity", "-Infinity"):
        return float(s.replace("Infinity", "inf"))
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", s):
        return float(int(s, 16))
    return math.nan


def filter_fields(expr, out=None):
    out = set() if out is None else out
    if not expr:
        return out
    for k in ("andGroup", "orGroup"):
        if k in expr:
            for e in expr[k]["expressions"]:
                filter_fields(e, out)
    if "notExpression" in expr:
        filter_fields(expr["notExpression"], out)
    if "filter" in expr:
        out.add(expr["filter"]["fieldName"])
    return out


def dh_of(h):
    return datetime.fromtimestamp(h * 3600, timezone.utc).strftime("%Y%m%d%H")


def fmt_value(name, v):
    if name in FLOAT_METRICS:
        s = f"{v:.6f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    return str(int(math.floor(v + 0.5)))


class _Acc:
    __slots__ = ("users", "new_users", "sessions", "engaged", "events", "pv", "engagement")

    def __init__(self):
        self.users, self.new_users, self.sessions, self.engaged = set(), set(), set(), set()
        self.events = self.pv = self.engagement = 0

    def add(self, s, e, event_mode):
        self.users.add(s["user"])
        if s["nvr"] == "new":
            self.new_users.add(s["user"])
        if s["id"] not in self.sessions:
            self.sessions.add(s["id"])
            if s["engaged"]:
                self.engaged.add(s["id"])
            self.engagement += s["engagement"]
            if not event_mode:
                self.events += len(s["events"])
                self.pv += s["pv"]
        if event_mode:
            self.events += 1
            if e["name"] == "page_view":
                self.pv += 1

    def value(self, m):
        sessions = len(self.sessions)
        if m in ("activeUsers", "totalUsers"):
            return len(self.users)
        table = {
            "newUsers": lambda: len(self.new_users),
            "sessions": lambda: sessions,
            "engagedSessions": lambda: len(self.engaged),
            "engagementRate": lambda: len(self.engaged) / sessions if sessions else 0,
            "bounceRate": lambda: 1 - len(self.engaged) / sessions if sessions else 0,
            "eventCount": lambda: self.events,
            "keyEvents": lambda: 0,
            "screenPageViews": lambda: self.pv,
            "screenPageViewsPerSession": lambda: self.pv / sessions if sessions else 0,
            "eventsPerSession": lambda: self.events / sessions if sessions else 0,
            "sessionsPerUser": lambda: sessions / len(self.users) if self.users else 0,
            "userEngagementDuration": lambda: self.engagement,
            "averageSessionDuration": lambda: self.engagement * 1.4 / sessions if sessions else 0,
        }
        if m not in table:
            raise GAError(400, f"Field {m} is not a valid metric.")
        return table[m]()


class DemoAPI:
    """The synthetic Data API. `cfg` is a resolved bizdash config (only `demo`,
    `internalUsers` are read); `now` is epoch seconds, fixed for the instance."""

    def __init__(self, cfg=None, now=None):
        cfg = cfg or {}
        demo = {"siteHost": "www.example.com", "usersPerHour": 14, "events": [], "customDimensions": []}
        demo.update(cfg.get("demo") or {})
        self.demo = demo
        iu = cfg.get("internalUsers") or {}
        int_dim = iu.get("dimension")
        self.int_param = int_dim[len("customEvent:"):] if int_dim and int_dim.startswith("customEvent:") else None
        self.internal_ids = list((iu.get("ids") or {}).keys())
        self.registered = set(demo.get("customDimensions") or [])
        if self.int_param:
            self.registered.add(self.int_param)
        for e in demo["events"]:
            self.registered.update((e.get("params") or {}).keys())
        now_ms = int((time.time() if now is None else now) * 1000)
        self.now_h = now_ms // 3600000
        self.now_m = now_ms // 60000
        self.first_h = self.now_h - HISTORY_DAYS * 24 + 1
        self._hours = {}
        self._rt = {}
        self.tokens_used = 0
        self.latest_hour = dh_of(self.now_h)

    # -- population ------------------------------------------------------------
    def base(self, h):
        r = rng(f"base:{h}")
        return self.demo["usersPerHour"] * DIURNAL[h % 24] * WEEKLY[(h // 24 + 3) % 7] * (0.85 + 0.3 * r())

    def n_new(self, h):
        return count_around(rng(f"new:{h}"), self.base(h) * 0.62)

    def first_touch(self, h, s):
        r = rng(f"s:{h}.{s}")
        r()
        return pick(r, SOURCES)

    def user_attrs(self, uid):
        r = rng(f"u:{uid}")
        country, cities = pick(r, COUNTRIES)
        city = cities[math.floor(r() * len(cities))]
        device = pick(r, DEVICES)
        browser = pick(r, BROWSERS[device])
        os_, osv = pick(r, OSES[device])
        screen = pick(r, SCREENS[device])
        return {"country": country, "city": city, "device": device, "browser": browser, "os": os_, "osv": osv,
                "screen": screen, "cid": f"demo.{format(fnv1a(uid), 'x')}"}

    def make_session(self, sid, uid, h, r, minute, src, first, is_new, spec=None):
        spec = spec or {}
        u = self.user_attrs(uid)
        u.update(spec.get("user") or {})
        source, medium, channel = src
        s = {"id": sid, "user": uid, "H": h, "dh": dh_of(h), "minute": minute, "source": source, "medium": medium,
             "channel": channel, "campaign": CAMPAIGN.get(channel, "(not set)"),
             "firstSource": first[0], "firstMedium": first[1], "firstChannel": first[2],
             "firstCampaign": CAMPAIGN.get(first[2], "(not set)"),
             "host": spec.get("host") or self.demo["siteHost"], "nvr": "new" if is_new else "returning",
             "country": u["country"], "city": u["city"], "device": u["device"], "browser": u["browser"],
             "os": u["os"], "osv": u["osv"], "screen": u["screen"], "cid": spec.get("cid") or u["cid"],
             "events": [], "pv": 0, "engaged": False, "engagement": 0}
        landing = spec.get("landing") or pick(r, PAGES)
        pv = spec.get("pages") or 1 + math.floor(r() * r() * 5)
        pages = [landing]
        for _ in range(1, pv):
            pages.append(pick(r, PAGES))
        s["landing"] = landing
        s["engaged"] = spec["engaged"] if spec.get("engaged") is not None else r() < 0.62
        if spec.get("engagement") is not None:
            s["engagement"] = spec["engagement"]
        else:
            s["engagement"] = 10 + math.floor(r() * r() * 300) if s["engaged"] else math.floor(r() * 8)
        ev = s["events"]
        ev.append({"name": "session_start", "page": landing, "params": {}})
        if is_new:
            ev.append({"name": "first_visit", "page": landing, "params": {}})
        for p in pages:
            ev.append({"name": "page_view", "page": p, "params": {}})
        s["pv"] = len(pages)
        if s["engaged"]:
            ev.append({"name": "user_engagement", "page": pages[-1], "params": {}})
        if not spec.get("noEvents"):
            fired = set()
            for e in self.demo["events"]:
                if e.get("requires") and e["requires"] not in fired:
                    continue
                p = e["rate"] * (1.5 if channel == "Paid Search" else 1) * (1.3 if s["engaged"] else 0.4) \
                    * (spec.get("boost") or 1)
                if r() < p:
                    n = 1 + (math.floor(r() * r() * e["repeat"]) if e.get("repeat") else 0)
                    for i in range(n):
                        params = {}
                        for k, vals in (e.get("params") or {}).items():
                            params[k] = vals[math.floor(r() * len(vals))]
                        if self.int_param:
                            params[self.int_param] = s["cid"]
                        ev.append({"name": e["name"], "page": pages[min(i, len(pages) - 1)], "params": params})
                    fired.add(e["name"])
        return s

    def sessions_at(self, h):
        if h in self._hours:
            return self._hours[h]
        out = []
        for s in range(self.n_new(h)):
            r = rng(f"s:{h}.{s}")
            minute = math.floor(r() * 60)
            src = pick(r, SOURCES)
            out.append(self.make_session(f"n{h}.{s}", f"u{h}.{s}", h, r, minute, src, src, True))
        nr = count_around(rng(f"ret:{h}"), self.base(h) * 0.5)
        for s in range(nr):
            r = rng(f"r:{h}.{s}")
            minute = math.floor(r() * 60)
            back = 1 + math.floor(r() * 8) if r() < 0.4 else 1 + math.floor(r() * r() * 24 * 28)
            h0 = h - back
            n0 = self.n_new(h0)
            if not n0:
                continue
            s0 = math.floor(r() * n0)
            first = self.first_touch(h0, s0)
            src = SOURCES[1][0] if r() < 0.55 else pick(r, SOURCES)
            out.append(self.make_session(f"r{h}.{s}", f"u{h0}.{s0}", h, r, minute, src, first, False))
        ref = h - (self.now_h - 76)
        if 0 <= ref < len(DECAY):
            n = math.floor(260 * DECAY[ref] + 0.5)
            src = ["news.example", "referral", "Referral"]
            for i in range(n):
                r = rng(f"x:{h}.ref.{i}")
                out.append(self.make_session(f"x{h}.ref.{i}", f"x{h}.ref.{i}", h, r, math.floor(r() * 60), src, src,
                                             True, {"landing": "/blog", "user": {"country": "United States"}}))
        burst = h - (self.now_h - 14)
        if 0 <= burst < 3:
            src = SOURCES[1][0]
            for i in range(150):
                r = rng(f"x:{h}.burst.{i}")
                minute = math.floor(r() * 60)
                spec = {"landing": "/", "pages": 1, "user": {"country": "India", "city": "Mumbai"},
                        "noEvents": True, "engaged": r() < 0.15}
                out.append(self.make_session(f"x{h}.burst.{i}", f"x{h}.burst.{i}", h, r, minute, src, src, True, spec))
        off = h - (self.now_h - 20)
        if 0 <= off < 5:
            src = SOURCES[1][0]
            for i in range(200):
                r = rng(f"x:{h}.off.{i}")
                out.append(self.make_session(
                    f"x{h}.off.{i}", f"x{h}.off.{i}", h, r, math.floor(r() * 60), src, src, True,
                    {"host": "localhost", "landing": "/", "pages": 1, "noEvents": True, "engaged": False,
                     "engagement": 1, "user": {"country": "Netherlands", "city": "(not set)", "device": "desktop",
                                               "browser": "Chrome", "os": "Linux", "osv": "Linux x86_64",
                                               "screen": "800x600"}}))
        for j, cid in enumerate(self.internal_ids):
            if h % 24 != (15 + j) % 24:
                continue
            r = rng(f"i:{h}.{j}")
            src = SOURCES[1][0]
            out.append(self.make_session(f"i{h}.{j}", f"internal.{j}", h, r, math.floor(r() * 60), src, src, False,
                                         {"cid": cid, "boost": 3, "engaged": True}))
        self._hours[h] = out
        return out

    # -- reporting ---------------------------------------------------------------
    def day_index(self, d):
        today = self.now_h // 24
        if d == "today":
            return today
        if d == "yesterday":
            return today - 1
        m = re.fullmatch(r"(\d+)daysAgo", d)
        if m:
            return today - int(m.group(1))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return int(dt.timestamp()) // 86400
        raise GAError(400, f'Invalid date "{d}"')

    _SESSION_KEYS = {
        "sessionSource": "source", "sessionMedium": "medium", "sessionDefaultChannelGroup": "channel",
        "sessionCampaignName": "campaign", "firstUserSource": "firstSource", "firstUserMedium": "firstMedium",
        "firstUserDefaultChannelGroup": "firstChannel", "firstUserCampaignName": "firstCampaign",
        "landingPage": "landing", "hostName": "host", "newVsReturning": "nvr", "country": "country",
        "city": "city", "deviceCategory": "device", "browser": "browser", "operatingSystem": "os",
        "operatingSystemWithVersion": "osv", "screenResolution": "screen"}

    @classmethod
    def session_field(cls, s, name):
        key = cls._SESSION_KEYS.get(name)
        if key:
            return s[key]
        if name == "dateHour":
            return s["dh"]
        if name == "date":
            return s["dh"][:8]
        if name == "hour":
            return s["dh"][8:10]
        if name == "dateHourMinute":
            return s["dh"] + f"{s['minute']:02d}"
        if name == "dayOfWeek":
            return str((s["H"] // 24 + 4) % 7)
        if name == "sessionSourceMedium":
            return f"{s['source']} / {s['medium']}"
        if name == "platform":
            return "web"
        return None

    def check_dimension(self, name):
        if name.startswith("customEvent:"):
            if name[len("customEvent:"):] not in self.registered:
                raise GAError(400, f"Field {name} is not a valid dimension. Did you register it?")
            return
        if name not in EVENT_SCOPED and name not in SESSION_DIMS:
            raise GAError(400, f"Field {name} is not a valid dimension.")

    def field(self, s, e, name):
        if name.startswith("customEvent:"):
            return e["params"].get(name[12:], "(not set)") if e else "(not set)"
        if name == "eventName":
            return e["name"] if e else None
        if name in ("pagePath", "pagePathPlusQueryString"):
            return e["page"] if e else s["landing"]
        if name in ("pageTitle", "unifiedScreenName"):
            return TITLES[e["page"] if e else s["landing"]]
        return self.session_field(s, name)

    @staticmethod
    def order_rows(rows, body, dims, metrics):
        order_bys = body.get("orderBys") or []
        if order_bys:
            def cmp(a, b):
                for o in order_bys:
                    if "metric" in o:
                        name = o["metric"]["metricName"]
                        if name not in metrics:
                            continue
                        i = metrics.index(name)
                        x, y = a["m"][i], b["m"][i]
                    elif "dimension" in o:
                        name = o["dimension"]["dimensionName"]
                        if name not in dims:
                            continue
                        i = dims.index(name)
                        x, y = a["d"][i], b["d"][i]
                    else:
                        continue
                    if x != y:
                        return (-1 if x < y else 1) * (-1 if o.get("desc") else 1)
                return 0
            rows.sort(key=cmp_to_key(cmp))
        else:
            t = next((dims.index(d) for d in ("dateHour", "date", "dateHourMinute", "minutesAgo") if d in dims), None)
            if t is not None:
                rows.sort(key=lambda r: r["d"][t])
            else:
                rows.sort(key=lambda r: (-(r["m"][0] if r["m"] else 0), ",".join(r["d"])))
        return rows

    def quota(self):
        u = self.tokens_used
        return {"tokensPerDay": {"consumed": u, "remaining": max(0, 200000 - u)},
                "tokensPerHour": {"consumed": u % 40000, "remaining": 40000 - (u % 40000)},
                "concurrentRequests": {"consumed": 0, "remaining": 10},
                "tokensPerProjectPerHour": {"consumed": u % 14000, "remaining": 14000 - (u % 14000)}}

    def payload(self, kind, dims, metrics, rows, totals, row_count, body):
        self.tokens_used += 1 + row_count // 1000
        out = {"dimensionHeaders": [{"name": d} for d in dims],
               "metricHeaders": [{"name": m, "type": "TYPE_FLOAT" if m in FLOAT_METRICS else "TYPE_INTEGER"}
                                 for m in metrics],
               "rows": [{"dimensionValues": [{"value": v} for v in r["d"]],
                         "metricValues": [{"value": fmt_value(metrics[i], v)} for i, v in enumerate(r["m"])]}
                        for r in rows],
               "rowCount": row_count, "metadata": {"currencyCode": "USD", "timeZone": "Etc/UTC"}, "kind": kind}
        if totals is not None:
            out["totals"] = [{"dimensionValues": [{"value": v} for v in r["d"]],
                              "metricValues": [{"value": fmt_value(metrics[i], v)} for i, v in enumerate(r["m"])]}
                             for r in totals]
        if not rows:
            del out["rows"]
        if body.get("returnPropertyQuota"):
            out["propertyQuota"] = self.quota()
        return out

    def run_report(self, body):
        dims = [d["name"] for d in body.get("dimensions") or []]
        metrics = [m["name"] for m in body.get("metrics") or []]
        if not metrics:
            raise GAError(400, "At least one metric is required.")
        for d in dims:
            self.check_dimension(d)
        for m in metrics:
            if m not in INT_METRICS and m not in FLOAT_METRICS:
                raise GAError(400, f"Field {m} is not a valid metric.")
        ranges = body.get("dateRanges") or []
        if len(ranges) != 1:
            raise GAError(400, "the demo answers exactly one dateRange")
        d0, d1 = self.day_index(ranges[0]["startDate"]), self.day_index(ranges[0]["endDate"])
        if d1 < d0:
            raise GAError(400, "startDate is after endDate")
        comparisons = body.get("comparisons") or None
        fields = filter_fields(body.get("dimensionFilter"))
        for c in comparisons or []:
            filter_fields(c.get("dimensionFilter"), fields)
        for f in fields:
            self.check_dimension(f)
        event_mode = any(d in EVENT_SCOPED or d.startswith("customEvent:") for d in [*dims, *fields])
        out_dims = [*dims, "comparison"] if comparisons else dims
        groups, totals_by = {}, {}
        want_totals = "TOTAL" in (body.get("metricAggregations") or []) or bool(comparisons and not dims)
        segs = comparisons or [{"name": None, "dimensionFilter": None}]
        dim_filter = body.get("dimensionFilter")
        h0, h1 = max(self.first_h, d0 * 24), min(self.now_h, d1 * 24 + 23)
        for h in range(h0, h1 + 1):
            for s in self.sessions_at(h):
                if h == self.now_h and s["minute"] > self.now_m % 60:
                    continue
                for e in (s["events"] if event_mode else [None]):
                    def get(name, s=s, e=e):
                        return self.field(s, e, name)
                    if not ga_match(dim_filter, get):
                        continue
                    for c in segs:
                        if c.get("dimensionFilter") and not ga_match(c["dimensionFilter"], get):
                            continue
                        d = [get(x) for x in dims]
                        if c["name"] is not None:
                            d.append(c["name"])
                        key = "\x01".join(d)
                        g = groups.get(key)
                        if g is None:
                            g = groups[key] = {"d": d, "acc": _Acc()}
                        g["acc"].add(s, e, event_mode)
                        if want_totals:
                            t = totals_by.get(c["name"])
                            if t is None:
                                t = totals_by[c["name"]] = _Acc()
                            t.add(s, e, event_mode)
        rows = [{"d": g["d"], "m": [g["acc"].value(m) for m in metrics]} for g in groups.values()]
        totals = None
        if want_totals:
            totals = [{"d": [*["RESERVED_TOTAL"] * len(dims), *([name] if name is not None else [])],
                       "m": [acc.value(m) for m in metrics]} for name, acc in totals_by.items()]
            if not totals:
                totals = [] if comparisons else [{"d": ["RESERVED_TOTAL"] * len(dims), "m": [0] * len(metrics)}]
        if comparisons and not dims:
            rows = []
        self.order_rows(rows, body, out_dims, metrics)
        row_count = len(rows)
        offset, limit = int(body.get("offset") or 0), int(body.get("limit") or 10000)
        return self.payload("analyticsData#runReport", out_dims, metrics, rows[offset:offset + limit], totals,
                            row_count, body)

    # -- realtime ------------------------------------------------------------------
    def visitors_at(self, m):
        if m in self._rt:
            return self._rt[m]
        h = m // 60
        ago = self.now_m - m
        n = count_around(rng(f"rtn:{m}"), self.base(h) * 0.07)
        if 5 <= ago <= 8 or 19 <= ago <= 22:
            n += 6
        out = []
        for i in range(n):
            r = rng(f"rt:{m}.{i}")
            stay = 1 + math.floor(r() * r() * 10)
            country, cities = pick(r, COUNTRIES)
            city = cities[math.floor(r() * len(cities))]
            device = pick(r, DEVICES)
            v = {"id": f"v{m}.{i}", "country": country, "city": city, "device": device, "events": []}
            for k in range(stay):
                v["events"].append({"name": "page_view", "minute": m + k, "page": pick(r, PAGES)})
            for e in self.demo["events"]:
                if e.get("requires") and not any(x["name"] == e["requires"] for x in v["events"]):
                    continue
                if r() < e["rate"]:
                    v["events"].append({"name": e["name"], "minute": m, "page": v["events"][0]["page"]})
            out.append(v)
        self._rt[m] = out
        return out

    def run_realtime_report(self, body):
        dims = [d["name"] for d in body.get("dimensions") or []]
        metrics = [m["name"] for m in body.get("metrics") or []]
        for d in dims:
            if d not in REALTIME_DIMS:
                raise GAError(400, f"Field {d} is not a valid dimension for realtime reports.")
        for m in metrics:
            if m not in REALTIME_METRICS:
                raise GAError(400, f"Field {m} is not a valid metric for realtime reports.")
        for f in filter_fields(body.get("dimensionFilter")):
            if f not in REALTIME_DIMS:
                raise GAError(400, f"Field {f} is not a valid dimension for realtime reports.")
        mr = (body.get("minuteRanges") or [{"startMinutesAgo": 29, "endMinutesAgo": 0}])[0]
        start_ago = mr.get("startMinutesAgo", 29)
        end_ago = mr.get("endMinutesAgo", 0)
        groups = {}
        for m in range(self.now_m - start_ago - 10, self.now_m + 1):
            for v in self.visitors_at(m):
                for e in v["events"]:
                    ago = self.now_m - e["minute"]
                    if ago < end_ago or ago > start_ago:
                        continue
                    row = {"minutesAgo": f"{ago:02d}", "country": v["country"], "city": v["city"],
                           "deviceCategory": v["device"], "unifiedScreenName": TITLES[e["page"]],
                           "eventName": e["name"], "platform": "web", "streamName": "Web", "appVersion": "(not set)"}
                    if not ga_match(body.get("dimensionFilter"), row.get):
                        continue
                    d = [row[x] for x in dims]
                    key = "\x01".join(d)
                    g = groups.get(key)
                    if g is None:
                        g = groups[key] = {"d": d, "users": set(), "events": 0}
                    g["users"].add(v["id"])
                    g["events"] += 1
        rows = [{"d": g["d"], "m": [len(g["users"]) if m == "activeUsers" else g["events"] if m == "eventCount" else 0
                                    for m in metrics]} for g in groups.values()]
        self.order_rows(rows, body, dims, metrics)
        return self.payload("analyticsData#runRealtimeReport", dims, metrics, rows[:int(body.get("limit") or 10000)],
                            None, len(rows), body)

    def batch_run_reports(self, body):
        return {"reports": [self.run_report(r) for r in body.get("requests") or []],
                "kind": "analyticsData#batchRunReports"}

    def post(self, method, body):
        """(status, payload) as the Data API would answer."""
        fn = {"runReport": self.run_report, "runRealtimeReport": self.run_realtime_report,
              "batchRunReports": self.batch_run_reports}.get(method)
        try:
            if fn is None:
                raise GAError(404, f"the demo has no method {method}")
            return 200, fn(json.loads(json.dumps(body or {})))
        except GAError as e:
            return e.status, {"error": {"code": e.status, "message": str(e), "status": "INVALID_ARGUMENT"}}
