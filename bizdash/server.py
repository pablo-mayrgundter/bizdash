"""``bizdash serve``: the dashboard plus a caching GA4 proxy.

The page detects the server through ``api/meta`` and then sends its own Data
API bodies to ``api/ga/<method>``; the server runs them with its credentials
(or the synthetic API under ``--demo``) and caches the answers for everyone it
serves: 25 s for realtime, 5 minutes for reports. There are no per-card routes,
so a deployment's plugin cards work behind the server without server changes.

Static files come from the deployment's site directory first (its
bizdash.config.json and cards), then from the packaged dashboard.
"""

import hashlib
import json
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

from . import WEB_DIR, __version__
from .gaclient import METHODS

TTL = {"runRealtimeReport": 25, "runReport": 300, "batchRunReports": 300}


class Cache:
    def __init__(self):
        self._d, self._lock = {}, threading.Lock()
        self.hits = self.misses = 0

    def get(self, key, ttl):
        with self._lock:
            hit = self._d.get(key)
            if hit and time.time() - hit[0] < ttl:
                self.hits += 1
                return hit[1]
            self.misses += 1
            return None

    def put(self, key, value):
        with self._lock:
            self._d[key] = (time.time(), value)
            if len(self._d) > 2000:                       # drop the oldest half
                for k in sorted(self._d, key=lambda k: self._d[k][0])[:1000]:
                    del self._d[k]


def create_app(client, site_dir=None):
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
    roots = [Path(site_dir).resolve()] if site_dir else []
    roots.append(WEB_DIR)
    cache = Cache()
    app.extensions["bizdash_cache"] = cache

    @app.get("/api/meta")
    def meta():
        return jsonify({"property_id": client.property_id, "demo": bool(client.demo), "version": __version__})

    @app.post("/api/ga/<method>")
    def ga(method):
        if method not in METHODS:
            abort(404)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400, "expected a JSON Data API request body")
        body.pop("property", None)                       # the server's property, always
        key = hashlib.sha256(f"{method}:{json.dumps(body, sort_keys=True)}".encode()).hexdigest()
        hit = cache.get(key, TTL[method])
        if hit is not None:
            return jsonify(hit)
        status, payload = client.post(method, body)
        if status == 200:
            cache.put(key, payload)
        return jsonify(payload), status

    @app.get("/")
    @app.get("/<path:path>")
    def static_files(path="index.html"):
        if path.endswith("/"):
            path += "index.html"
        for root in roots:
            if (root / path).is_file():
                resp = send_from_directory(root, path)
                if path.endswith(".json") or path == "api/meta":
                    resp.headers["Cache-Control"] = "no-cache"
                return resp
        abort(404)

    return app
