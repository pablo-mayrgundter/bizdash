"""Fixtures for bizdash's tests.

Three layers:
  - Node unit tests (tests/js/*.test.mjs) for the SPA's pure modules, run by
    test_js_units.py with Node's built-in runner;
  - Python unit tests for the package, including parity tests that run the
    same input through the Python and JavaScript implementations;
  - browser tests that drive the SPA in Chromium against the synthetic Data
    API (?demo=1): no credentials and no network.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "bizdash" / "web"
WIDE, NARROW, PHONE = 1440, 1024, 390
sys.path.insert(0, str(ROOT))


def node_available():
    return shutil.which("node") is not None


def run_node(script):
    """Run an ES module snippet with Node and parse what it prints as JSON.
    `WEB` in the snippet is the packaged SPA's file URL."""
    code = f"const WEB = {json.dumps(WEB.as_uri() + '/')};\n" + script
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise RuntimeError(out.stderr)
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(not node_available(), reason="node is not installed")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(port, proc):
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError("server exited early")
            time.sleep(0.05)
    raise RuntimeError(f"server did not come up on {port}")


class StaticServer:
    """A plain static file server, like GitHub Pages: no api/ga, so the page
    falls back to the browser-direct transport unless ?demo=1."""

    def __init__(self, directory):
        self.port = _free_port()
        self.proc = subprocess.Popen([sys.executable, "-m", "http.server", str(self.port), "--bind", "127.0.0.1"],
                                     cwd=directory, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _wait(self.port, self.proc)
        self.url = f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)


@pytest.fixture(scope="session")
def static_url():
    s = StaticServer(WEB)
    yield s.url
    s.stop()


@pytest.fixture
def site_server(tmp_path):
    """Build a site from a dict of {relative path: text} over the packaged
    dashboard and serve it statically; returns its base URL."""
    servers = []

    def make(files, config=None, check=True):
        from bizdash.build import build_site
        site = tmp_path / f"site{len(servers)}"
        site.mkdir()
        if config is not None:
            files = {**files, "bizdash.config.json": json.dumps(config)}
        for rel, text in files.items():
            (site / rel).parent.mkdir(parents=True, exist_ok=True)
            (site / rel).write_text(text)
        out = tmp_path / f"out{len(servers)}"
        build_site(out, site, check=check)
        srv = StaticServer(out)
        servers.append(srv)
        return srv.url
    yield make
    for s in servers:
        s.stop()


def _chromium_executable():
    explicit = os.environ.get("BIZDASH_TEST_CHROMIUM")
    if explicit:
        return explicit
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if root and Path(root).is_dir():
        found = sorted(Path(root).glob("chromium-*/chrome-linux*/chrome"))
        if found:
            return str(found[-1])
    return None


@pytest.fixture(scope="session")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        exe = _chromium_executable()
        b = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        yield b
        b.close()


@pytest.fixture
def open_page(browser):
    """open_page(url, width=WIDE, timezone=None) -> a loaded page. Fails the
    test on any uncaught page error. Waits for the dashboard to mount."""
    contexts = []

    def opener(url, width=WIDE, timezone=None, wait=True):
        ctx = browser.new_context(viewport={"width": width, "height": 900},
                                  **({"timezone_id": timezone} if timezone else {}))
        page = ctx.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        contexts.append((ctx, page))
        page.goto(url)
        if wait:
            page.wait_for_function("window.bizdashReady !== undefined")
            page.evaluate("window.bizdashReady")
            settle(page)
        return page
    yield opener
    for ctx, page in contexts:
        errors = list(page.errors)
        ctx.close()
        assert not errors, f"uncaught page errors: {errors}"


def settle(page):
    """Wait until no card is loading."""
    page.wait_for_function("window.bizdash && document.querySelectorAll('.card').length > 0 "
                           "&& !document.querySelector('.card.loading')", timeout=30000)
