"""Command line: python -m bizdash {serve,build,check,snapshot,bots}."""

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load


def _config(args, strict=False):
    site = getattr(args, "site", None)
    path = args.config or (Path(site) / "bizdash.config.json" if site and (Path(site) / "bizdash.config.json").exists() else None)
    cfg, warnings = load(path, site_dir=site, strict=strict)
    for w in warnings:
        print(f"config: {w}", file=sys.stderr)
    return cfg


def _client(args, cfg):
    from .gaclient import make_client
    return make_client(demo=args.demo, property_id=args.property, config=cfg)


def cmd_serve(args):
    from .server import create_app
    cfg = _config(args)
    app = create_app(_client(args, cfg), site_dir=args.site)
    print(f"bizdash {__version__} on http://{args.host}:{args.port}/" + (" (demo data)" if args.demo else ""))
    app.run(host=args.host, port=args.port, threaded=True)


def cmd_build(args):
    from .build import build_site
    for w in build_site(args.out, args.site, force=args.force):
        print(f"warning: {w}", file=sys.stderr)
    print(f"built {args.out}")


def cmd_check(args):
    _config(args, strict=True)
    print("config ok")


def cmd_snapshot(args):
    from .snapshot import snapshot
    cfg = _config(args)
    client = _client(args, cfg)
    print(f"pulling {args.days} days from property {client.property_id} into {args.db}")
    snapshot(client, args.db, days=args.days, spikes_cfg=cfg["spikes"])


def cmd_bots(args):
    from .bots import calibrate
    cfg = _config(args)
    calibrate(_client(args, cfg), cfg, args.out, days=args.days, only=args.only,
              log=lambda m: print(m, file=sys.stderr))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bizdash", description="A reusable GA4 bizdev dashboard.")
    ap.add_argument("--version", action="version", version=f"bizdash {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, client=True):
        p.add_argument("--config", help="bizdash.config.json (default: the site's, else the packaged demo config)")
        p.add_argument("--site", help="a deployment's site directory (config, cards, assets)")
        if client:
            p.add_argument("--demo", action="store_true", help="synthetic data, no credentials")
            p.add_argument("--property", help="GA4 property id (default: GA_PROPERTY_ID, then the config)")

    p = sub.add_parser("serve", help="serve the dashboard with a caching GA4 proxy")
    common(p)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5077)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("build", help="assemble a static site from the dashboard and a site directory")
    p.add_argument("--site", help="the deployment's site directory")
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true", help="replace a non-empty --out")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("check", help="validate a config, its demo{} and its plugin paths")
    common(p, client=False)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("snapshot", help="pull hourly data into DuckDB")
    common(p)
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--db", default="ga.duckdb")
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser("bots", help="calibrate the traffic rule (read-only)")
    common(p)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--out", required=True, help="where to write raw responses; keep it outside any repository")
    p.add_argument("--only", nargs="*", help="run just these request names")
    p.set_defaults(fn=cmd_bots)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except (ConfigError, FileExistsError, FileNotFoundError, ValueError) as e:
        print(f"bizdash: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
