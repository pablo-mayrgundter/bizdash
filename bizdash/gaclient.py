"""GA4 Data API client for the server, the snapshot and the calibration.

``post(method, body)`` sends a raw Data API body and returns ``(status,
payload)``; the server proxies the page's own bodies through it unchanged, so
the browser and the server ask GA exactly the same questions. ``DemoClient``
answers the same calls from the synthetic population in demo.py.

Credentials, in order:
  GA_SERVICE_ACCOUNT_JSON_B64  base64 of a service-account key JSON, decoded in
                               memory only and never written to disk;
  otherwise Application Default Credentials:
    gcloud auth application-default login \\
      --scopes=https://www.googleapis.com/auth/analytics.readonly,https://www.googleapis.com/auth/cloud-platform
    or GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
The service account (or your account) needs Viewer access on the property.
"""

import base64
import json
import os
import random
import threading
import time

from .demo import DemoAPI

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
API = "https://analyticsdata.googleapis.com/v1beta"
METHODS = ("runReport", "runRealtimeReport", "batchRunReports")

# GA4 caps concurrent Data API requests per property (10 on a standard
# property). Each server thread takes one of a few process-wide slots, under
# the cap to leave room for browser-direct dashboards on the same property. A
# 429 that says "concurrent" clears when the other requests finish, so it is
# retried with backoff; the hourly and daily token-quota 429s are not.
MAX_CONCURRENT = 5
CONCURRENCY_RETRIES = 3
RETRY_BASE_S = 1.0
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


class GAError(RuntimeError):
    def __init__(self, method, status, message):
        super().__init__(f"GA4 {method}: {message} (HTTP {status})")
        self.status = status


def rows(payload, dimensions, metrics):
    """Rows as dicts keyed by API name, metrics as floats (as gaRows in the page)."""
    out = []
    for row in payload.get("rows", []):
        rec = {}
        for name, cell in zip(dimensions, row.get("dimensionValues", [])):
            rec[name] = cell.get("value", "")
        for name, cell in zip(metrics, row.get("metricValues", [])):
            try:
                rec[name] = float(cell.get("value", 0))
            except ValueError:
                rec[name] = 0.0
        out.append(rec)
    return out


def _credentials():
    b64 = os.environ.get("GA_SERVICE_ACCOUNT_JSON_B64")
    if b64:
        from google.oauth2 import service_account
        info = json.loads(base64.b64decode(b64))
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    import google.auth
    creds, _ = google.auth.default(scopes=SCOPES)
    return creds


class _Base:
    property_id = ""
    demo = False

    def run(self, method, body):
        """post(), raising GAError on anything but 200."""
        status, payload = self.post(method, body)
        if status != 200:
            msg = payload.get("error", {}).get("message") if isinstance(payload, dict) else str(payload)
            raise GAError(method, status, msg)
        return payload

    def run_report(self, dimensions, metrics, start_date, end_date, dimension_filter=None,
                   comparisons=None, limit=250000):
        body = {"dateRanges": [{"startDate": start_date, "endDate": end_date}],
                "dimensions": [{"name": d} for d in dimensions], "metrics": [{"name": m} for m in metrics],
                "limit": limit}
        if dimension_filter:
            body["dimensionFilter"] = dimension_filter
        if comparisons:
            body["comparisons"] = comparisons
            body["metricAggregations"] = ["TOTAL"]
        payload = self.run("runReport", body)
        return rows(payload, list(dimensions) + (["comparison"] if comparisons else []), metrics)


class GAClient(_Base):
    def __init__(self, property_id, credentials=None):
        from google.auth.transport.requests import AuthorizedSession
        if not property_id:
            raise ValueError("a GA4 property id is required (--property, GA_PROPERTY_ID or the config's propertyId)")
        self.property_id = str(property_id)
        self.session = AuthorizedSession(credentials or _credentials())

    def post(self, method, body):
        if method not in METHODS:
            return 404, {"error": {"code": 404, "message": f"unsupported method {method}"}}
        url = f"{API}/properties/{self.property_id}:{method}"
        for attempt in range(CONCURRENCY_RETRIES + 1):
            with _slots:
                resp = self.session.post(url, json=body, timeout=60)
            if resp.status_code == 429 and "concurrent" in resp.text.lower() and attempt < CONCURRENCY_RETRIES:
                time.sleep(RETRY_BASE_S * 2 ** attempt * (1 + random.random()))   # outside the slot
                continue
            break
        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": {"code": resp.status_code, "message": resp.text[:500]}}
        return resp.status_code, payload


class DemoClient(_Base):
    demo = True

    def __init__(self, config=None, now=None):
        self.api = DemoAPI(config, now=now)
        self.property_id = "demo"

    def post(self, method, body):
        return self.api.post(method, body)


def make_client(demo=False, property_id=None, config=None):
    if demo:
        return DemoClient(config)
    return GAClient(property_id or os.environ.get("GA_PROPERTY_ID") or (config or {}).get("propertyId"))
