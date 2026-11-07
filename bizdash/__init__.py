"""bizdash: a reusable Google Analytics 4 bizdev dashboard.

The dashboard itself is a static single-page app in ``bizdash/web``; this
package serves it (``python -m bizdash serve``), proxies and caches the GA4
Data API for it, builds a deployable site from a deployment's config and
plugin cards, snapshots data to DuckDB, and calibrates the traffic rule.
"""

from pathlib import Path

__version__ = "0.1.0rc1"
WEB_DIR = Path(__file__).resolve().parent / "web"
