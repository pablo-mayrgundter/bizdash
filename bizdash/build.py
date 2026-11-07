"""``bizdash build``: assemble a deployable static site.

The packaged dashboard is copied to ``--out``, then the deployment's site
directory is laid over it: its bizdash.config.json, its plugin cards, and any
extra assets. The result is what GitHub Pages (or any static host) serves.
The config is validated and every plugin path must exist in the output.
"""

import json
import shutil
from pathlib import Path

from . import WEB_DIR, __version__
from .config import ConfigError, resolve

CORE = ("index.html", "styles.css", "src")


def build_site(out_dir, site_dir=None, force=False):
    out = Path(out_dir)
    if out.exists():
        if not force and any(out.iterdir()):
            raise FileExistsError(f"{out} is not empty (use --force to replace it)")
        shutil.rmtree(out)
    shutil.copytree(WEB_DIR, out, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    warnings = []
    if site_dir:
        site = Path(site_dir)
        if not site.is_dir():
            raise FileNotFoundError(f"site directory {site} does not exist")
        for src in sorted(site.rglob("*")):
            if src.is_dir() or src.name.startswith(".") or "__pycache__" in src.parts:
                continue
            rel = src.relative_to(site)
            if rel.parts[0] in CORE:
                warnings.append(f"{rel} replaces a core file: pin the bizdash version this was written against")
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out / rel)
    cfg_path = out / "bizdash.config.json"
    try:
        raw = json.loads(cfg_path.read_text())
    except ValueError as e:
        raise ConfigError(f"{cfg_path}: {e}") from e
    cfg, errors, warns = resolve(raw)
    warnings += warns
    for p in cfg["plugins"]:
        if not (out / p).is_file():
            errors.append(f"plugin {p} is not in the site")
    if errors:
        raise ConfigError("; ".join(errors))
    (out / "version.json").write_text(json.dumps({"bizdash": __version__}) + "\n")
    (out / ".nojekyll").write_text("")
    return warnings
