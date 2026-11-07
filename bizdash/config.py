"""The deployment config (bizdash.config.json), resolved exactly as the SPA does
in web/src/config.js: merged over web/src/defaults.json, validated, with the
traffic rule's default and revision filled in. tests/test_config.py runs both
implementations over the same fixtures and requires the same answer."""

import copy
import json
import re
from pathlib import Path

from . import WEB_DIR
from .demo import fnv1a
from .rules import host_rule, rule_kind

DEFAULTS = json.loads((WEB_DIR / "src" / "defaults.json").read_text())
KNOWN_KEYS = set(DEFAULTS["config"]) | {"$schema", "_comment", "version"}


class ConfigError(ValueError):
    pass


def _is_obj(v):
    return isinstance(v, dict)


def merge(base, over):
    """Deep merge: dicts merge, everything else (lists included) replaces."""
    if not _is_obj(base) or not _is_obj(over):
        return copy.deepcopy(over)
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if k in out else copy.deepcopy(v)
    return out


def is_safe_relative_path(p):
    return (isinstance(p, str) and len(p) > 0 and not re.match(r"^[a-z][a-z0-9+.-]*:", p, re.I)
            and not p.startswith("/") and not p.startswith("\\") and ".." not in re.split(r"[\\/]", p))


def rule_rev(rule):
    return "r" + format(fnv1a(json.dumps(rule, separators=(",", ":"), ensure_ascii=False)), "x")


def resolve(raw):
    """Returns (config, errors, warnings)."""
    errors, warnings = [], []
    if not _is_obj(raw):
        return copy.deepcopy(DEFAULTS["config"]), ["config must be a JSON object"], warnings
    for k in raw:
        if k not in KNOWN_KEYS:
            warnings.append(f'unknown key "{k}" ignored')
    cfg = merge(DEFAULTS["config"], raw)
    sections = DEFAULTS["sections"]

    for k in ("title", "propertyId", "clientId", "timezoneNote"):
        if not isinstance(cfg[k], str):
            errors.append(f"{k} must be a string")
    cfg["propertyId"] = re.sub(r"^properties/", "", str(cfg["propertyId"] or ""))
    if cfg["propertyId"] and not re.fullmatch(r"\d+", cfg["propertyId"]):
        errors.append("propertyId must be the numeric GA4 property id")

    ranges = cfg["ranges"]
    if not isinstance(ranges, list) or not ranges or not all(isinstance(m, int) and m > 0 for m in ranges):
        errors.append("ranges must be a list of positive whole minutes")
    elif cfg["defaultRange"] not in ranges:
        errors.append("defaultRange must be one of ranges")

    if not _is_obj(cfg["events"]):
        errors.append("events must be an object keyed by event name")
        cfg["events"] = {}
    for name, e in cfg["events"].items():
        if not _is_obj(e):
            errors.append(f"events.{name} must be an object")
            continue
        e["label"] = e.get("label") or name
        if e.get("since") is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(e["since"])):
            errors.append(f"events.{name}.since must be YYYY-MM-DD")

    def meta(name):
        return cfg["events"].get(name) or {"label": name}

    sec = cfg["chart"].get("secondary")
    if sec:
        event = sec if isinstance(sec, str) else sec.get("event")
        if not event:
            errors.append("chart.secondary needs an event")
        else:
            sec_obj = sec if _is_obj(sec) else {}
            since = sec_obj.get("since")
            cfg["chart"]["secondary"] = {"event": event, "label": sec_obj.get("label") or meta(event)["label"],
                                         "since": since if since is not None else meta(event).get("since")}

    if not isinstance(cfg["tiles"], list):
        errors.append("tiles must be a list")
        cfg["tiles"] = []
    tiles = []
    for i, t in enumerate(cfg["tiles"]):
        tile = {"event": t} if isinstance(t, str) else dict(t)
        if not tile.get("event"):
            errors.append(f"tiles[{i}] needs an event")
        tile["label"] = tile.get("label") or meta(tile.get("event"))["label"]
        tile["metric"] = tile.get("metric") or "totalUsers"
        if tile["metric"] not in ("totalUsers", "eventCount"):
            errors.append(f"tiles[{i}].metric must be totalUsers or eventCount")
        tiles.append(tile)
    cfg["tiles"] = tiles

    if not isinstance(cfg["breakdowns"], list):
        errors.append("breakdowns must be a list")
        cfg["breakdowns"] = []
    for i, b in enumerate(cfg["breakdowns"]):
        if not b.get("key") or not b.get("dimension"):
            errors.append(f"breakdowns[{i}] needs key and dimension")
        b["title"] = b.get("title") or f"Users by {b.get('dimension')}"
        if b.get("pairEvent"):
            b["pairLabel"] = b.get("pairLabel") or meta(b["pairEvent"])["label"]

    if cfg.get("funnel"):
        cfg["funnel"] = merge(sections["funnel"], cfg["funnel"])
        steps = cfg["funnel"].get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append("funnel.steps must list at least one step")
        keys = {"visitors"}
        for i, s in enumerate(steps or []):
            if not s.get("key") or s["key"] in keys:
                errors.append(f'funnel.steps[{i}] needs a unique key (not "visitors")')
            keys.add(s.get("key"))
            if not isinstance(s.get("events"), list) or not s["events"]:
                errors.append(f"funnel.steps[{i}].events must list event names")
            s["label"] = s.get("label") or s.get("key")
            s["short"] = s.get("short") or s["label"]
            fb = s.get("fallback")
            if fb and (not isinstance(fb.get("events"), list) or not fb["events"]):
                errors.append(f"funnel.steps[{i}].fallback.events must list event names")

    if cfg.get("conversions"):
        cfg["conversions"] = merge(sections["conversions"], cfg["conversions"])
        if not isinstance(cfg["conversions"].get("events"), list) or not cfg["conversions"]["events"]:
            errors.append("conversions.events must list event names")

    iu = cfg["internalUsers"]
    if isinstance(iu.get("ids"), list):
        iu["ids"] = {i: iu["label"] for i in iu["ids"]}
    if not _is_obj(iu.get("ids")):
        errors.append("internalUsers.ids must map ids to labels")
        iu["ids"] = {}
    if iu["ids"] and not iu.get("dimension"):
        errors.append("internalUsers.ids needs internalUsers.dimension "
                      "(the event-scoped dimension carrying the id)")

    if cfg.get("traffic"):
        cfg["traffic"] = merge(sections["traffic"], cfg["traffic"])
        t = cfg["traffic"]
        if not t.get("rule") and t.get("siteHosts"):
            t["rule"] = host_rule(t["siteHosts"])
        if not t.get("rule"):
            errors.append("traffic needs siteHosts or a rule")
        else:
            try:
                rule_kind(t["rule"].get("match", t["rule"]))
            except ValueError as e:
                errors.append(str(e))
            t["label"] = t.get("label") or (f"host ≠ {t['siteHosts'][0]}" if t.get("siteHosts") else "custom rule")
            t["rev"] = t.get("rev") or rule_rev(t["rule"])
        if t.get("default") not in ("all", "standard", "nonstandard"):
            errors.append("traffic.default must be all, standard or nonstandard")

    for k in ("z", "minExtra", "lookbackDays"):
        v = cfg["spikes"].get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not v > 0:
            errors.append(f"spikes.{k} must be a positive number")

    if not isinstance(cfg["plugins"], list):
        errors.append("plugins must be a list of module paths")
        cfg["plugins"] = []
    for p in cfg["plugins"]:
        if not is_safe_relative_path(p):
            errors.append(f'plugin "{p}" must be a relative path on this site (no URL, no leading /, no ..)')

    if cfg["links"].get("errorTracker") and not isinstance(cfg["links"]["errorTracker"], str):
        errors.append("links.errorTracker must be a URL template string")
    return cfg, errors, warnings


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def demo_errors(demo):
    """What `bizdash check` and `build` find wrong in `demo{}`, the synthetic data's
    shape. The page and the Python demo read it as given, so a mistake here makes
    an event that never fires, a rate that is not a probability, or a crash."""
    if not _is_obj(demo):
        return ["demo must be an object"]
    errors = []
    if "usersPerHour" in demo and not (_is_number(demo["usersPerHour"]) and demo["usersPerHour"] > 0):
        errors.append(f"demo.usersPerHour must be a positive number, not {json.dumps(demo['usersPerHour'])}")
    if "siteHost" in demo and not (isinstance(demo["siteHost"], str) and demo["siteHost"]):
        errors.append("demo.siteHost must be a host name")
    cds = demo.get("customDimensions", [])
    if not isinstance(cds, list) or not all(isinstance(c, str) and c for c in cds):
        errors.append("demo.customDimensions must list parameter names")
    events = demo.get("events", [])
    if not isinstance(events, list):
        return errors + ["demo.events must be a list"]
    names = [e.get("name") if _is_obj(e) else None for e in events]
    for i, e in enumerate(events):
        if not _is_obj(e):
            errors.append(f"demo.events[{i}] must be an object")
            continue
        name = e.get("name")
        at = f'demo.events[{i}] "{name}"' if isinstance(name, str) and name else f"demo.events[{i}]"
        if not (isinstance(name, str) and name):
            errors.append(f"{at} needs a name")
        rate = e.get("rate")
        if not (_is_number(rate) and 0 <= rate <= 1):
            errors.append(f"{at}: rate must be a number from 0 to 1 (a probability), not {json.dumps(rate)}")
        req = e.get("requires")
        if req is not None and req not in names[:i]:
            if req in names[i + 1:]:
                errors.append(f'{at}: requires "{req}", which is listed after it; list "{req}" first '
                              "(events fire in list order, so this one never would)")
            else:
                errors.append(f'{at}: requires {json.dumps(req)}, which is not an event listed before it in demo.events')
        rep = e.get("repeat")
        if rep is not None and not (isinstance(rep, int) and not isinstance(rep, bool) and rep >= 0):
            errors.append(f"{at}: repeat must be a whole number, not {json.dumps(rep)}")
        params = e.get("params")
        if params is None:
            continue
        if not _is_obj(params):
            errors.append(f"{at}: params must map each parameter to a list of values")
            continue
        for k, vals in params.items():
            if not isinstance(vals, list) or not vals:
                errors.append(f"{at}: params.{k} must be a non-empty list of values")
            elif not all(isinstance(v, str) for v in vals):
                bad = next(v for v in vals if not isinstance(v, str))
                errors.append(f"{at}: params.{k} values must be strings, as GA reports them "
                              f"(write {json.dumps(json.dumps(bad))}, not {json.dumps(bad)})")
    return errors


def plugin_errors(cfg, site_dir):
    """Plugin paths that are not files in the site directory (or the packaged
    dashboard, which `build` lays the site over)."""
    site = Path(site_dir) if site_dir else WEB_DIR
    return [f"plugin {p} does not exist in {site}" for p in cfg["plugins"]
            if is_safe_relative_path(p) and not (site / p).is_file() and not (WEB_DIR / p).is_file()]


def load(path=None, site_dir=None, strict=False):
    """Load and resolve a config file (the package's demo config by default).
    Raises ConfigError on errors; warnings are returned alongside. `strict`
    (`bizdash check`) also checks `demo{}` and that each plugin exists in
    `site_dir` (default: the config's own directory)."""
    path = Path(path) if path else WEB_DIR / "bizdash.config.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ConfigError(f"{path}: {e}") from e
    cfg, errors, warnings = resolve(raw)
    if strict:
        errors += demo_errors(cfg["demo"]) + plugin_errors(cfg, site_dir or path.parent)
    if errors:
        raise ConfigError(f"invalid {path}:\n  " + "\n  ".join(errors))
    return cfg, warnings
