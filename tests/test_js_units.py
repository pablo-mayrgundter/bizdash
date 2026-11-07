"""The Node unit tests for the SPA's pure modules (tests/js), as one pytest test
so `pytest` alone runs the whole suite."""

import subprocess
from pathlib import Path

from conftest import ROOT, needs_node


@needs_node
def test_node_unit_tests():
    files = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.mjs"))
    out = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=300, cwd=ROOT)
    summary = "\n".join(line for line in out.stdout.splitlines() if line.startswith(("not ok", "# pass", "# fail")))
    assert out.returncode == 0, f"{summary}\n{out.stdout[-4000:]}\n{out.stderr[-2000:]}"
