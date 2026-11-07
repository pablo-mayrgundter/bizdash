"""The traffic rule, as web/src/rules.js: compile the config's rule DSL to a GA4
FilterExpression, evaluate it three-valued over rows, and build the three
segment comparisons."""

SEGMENTS = ("all", "standard", "nonstandard")


def _match(rule):
    return rule.get("match", rule) if isinstance(rule, dict) else rule


def rule_kind(node):
    if isinstance(node, dict):
        if isinstance(node.get("all"), list):
            return "all"
        if isinstance(node.get("any"), list):
            return "any"
        if node.get("not"):
            return "not"
        if node.get("dim") and (isinstance(node.get("in"), list) or isinstance(node.get("notIn"), list)):
            return "leaf"
    raise ValueError(f"traffic rule: unrecognized node {node!r}")


def filter_expression(rule):
    def compile_(node):
        kind = rule_kind(node)
        if kind == "leaf":
            values = [str(v) for v in (node.get("in") if "in" in node else node["notIn"])]
            in_list = {"filter": {"fieldName": node["dim"],
                                  "inListFilter": {"values": values, "caseSensitive": False}}}
            return in_list if "in" in node else {"notExpression": in_list}
        if kind == "not":
            return {"notExpression": compile_(node["not"])}
        parts = [compile_(n) for n in node[kind]]
        if len(parts) == 1:
            return parts[0]
        return {"andGroup" if kind == "all" else "orGroup": {"expressions": parts}}
    return compile_(_match(rule))


def is_nonstandard(row, rule):
    """True, False, or None when the row lacks a dimension the rule needs."""
    def ev(node):
        kind = rule_kind(node)
        if kind == "leaf":
            if row is None or row.get(node["dim"]) is None:
                return None
            v = str(row[node["dim"]]).lower()
            vals = node.get("in") if "in" in node else node["notIn"]
            hit = any(str(x).lower() == v for x in vals)
            return hit if "in" in node else not hit
        if kind == "not":
            v = ev(node["not"])
            return None if v is None else not v
        vals = [ev(n) for n in node[kind]]
        decisive = kind != "all"
        if decisive in vals:
            return decisive
        return None if None in vals else not decisive
    return ev(_match(rule))


def comparisons(rule):
    expr = filter_expression(rule)
    return [{"name": "all", "dimensionFilter": {"filter": {"fieldName": "country",
             "stringFilter": {"matchType": "FULL_REGEXP", "value": ".*"}}}},
            {"name": "nonstandard", "dimensionFilter": expr},
            {"name": "standard", "dimensionFilter": {"notExpression": expr}}]


def host_rule(hosts):
    return {"any": [{"dim": "hostName", "notIn": list(hosts)}]}
