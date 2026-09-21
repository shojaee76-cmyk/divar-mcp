"""Shell-independent smoke test: run the MCP server CLI end to end.

Used by CI (Windows runners default to PowerShell, whose `>` redirection writes
UTF-16, which broke the old inline `cmd > file` version) and usable locally:

    python tools/smoke.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run(*args: str) -> str:
    env = dict(os.environ)
    # the package has no dependencies, so pointing PYTHONPATH at src/ lets the
    # smoke test run under any interpreter (runner python, project venv, ...)
    src = str(Path(__file__).resolve().parent.parent / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "divar_mcp.server", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=120, env=env,
    )
    if proc.returncode != 0:
        raise SystemExit(f"`divar-mcp {' '.join(args)}` failed:\n{proc.stderr[-800:]}")
    return proc.stdout


def main() -> int:
    tools = json.loads(run("--list-tools"))
    assert len(tools) == 8, f"expected 8 tools, got {len(tools)}"
    assert all(t["description"].strip() for t in tools), "a tool has an empty description"
    assert "تهران" in json.dumps(tools, ensure_ascii=False), "Persian text did not survive stdio"

    cities = json.loads(run("--call", "divar_list_cities", "--args", json.dumps({"query": "تهران"})))
    assert cities["cities"][0]["id"] == "1", cities

    cats = json.loads(run("--call", "divar_list_categories", "--args", json.dumps({"query": "mobile"})))
    assert any(c["slug"] == "mobile-phones" for c in cats["categories"]), cats

    print(f"smoke OK: {len(tools)} tools; city lookup and category lookup both answered offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
