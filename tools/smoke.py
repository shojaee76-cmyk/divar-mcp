"""Shell-independent smoke test: run the MCP server CLI end to end, offline.

Used by CI (Windows runners default to PowerShell, whose `>` redirection writes
UTF-16, which broke an earlier inline `cmd > file` version) and usable locally:

    python tools/smoke.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

EXPECTED_TOOLS = 19


def run(*args: str, env_extra: dict | None = None) -> str:
    env = dict(os.environ)
    # the package has no dependencies, so pointing PYTHONPATH at src/ lets the
    # smoke test run under any interpreter (runner python, project venv, ...)
    src = str(Path(__file__).resolve().parent.parent / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, "-m", "divar_mcp.server", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=180, env=env,
    )
    if proc.returncode != 0:
        raise SystemExit(f"`divar-mcp {' '.join(args)}` failed:\n{proc.stderr[-900:]}")
    return proc.stdout


def call(tool: str, args: dict, env_extra: dict | None = None):
    return json.loads(run("--call", tool, "--args", json.dumps(args, ensure_ascii=False),
                          env_extra=env_extra))


def main() -> int:
    tools = json.loads(run("--list-tools"))
    assert len(tools) == EXPECTED_TOOLS, f"expected {EXPECTED_TOOLS} tools, got {len(tools)}"
    for tool in tools:
        assert tool["description"].strip(), f"{tool['name']} has no description"
        assert tool["inputSchema"]["type"] == "object", tool["name"]
        assert tool["outputSchema"]["type"] == "object", f"{tool['name']} has no outputSchema"
        assert "readOnlyHint" in tool["annotations"], f"{tool['name']} has no annotations"
    assert "تهران" in json.dumps(tools, ensure_ascii=False), "Persian text did not survive stdio"

    # resources and prompts must be advertised
    resources = json.loads(run("--resources"))
    assert {"divar://cities", "divar://categories", "divar://status", "divar://help"} <= {
        r["uri"] for r in resources
    }
    prompts = json.loads(run("--prompts"))
    assert {p["name"] for p in prompts} >= {"price-an-item", "appraise-listing", "find-deals", "watch-market"}

    cities = call("divar_list_cities", {"query": "تهران"})
    assert cities["cities"][0]["id"] == "1", cities

    cats = call("divar_list_categories", {"query": "mobile"})
    assert any(c["slug"] == "mobile-phones" for c in cats["categories"]), cats

    # a wrong category must come back with repair suggestions, not a stack trace
    proc = subprocess.run(
        [sys.executable, "-m", "divar_mcp.server", "--call", "divar_list_categories",
         "--args", json.dumps({"query": "zzz-nothing"})],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
             "PYTHONIOENCODING": "utf-8"},
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    hint = json.loads(proc.stdout)
    assert "suggestions" in hint and hint["count"] == 0, hint

    with tempfile.TemporaryDirectory() as tmp:
        env_extra = {"DIVAR_STORE": str(Path(tmp) / "store.db")}
        trend = call("divar_price_trend", {"query": "anything", "city": "تهران"}, env_extra)
        assert trend["status"] == "collecting" and trend["days_tracked"] == 0, trend
        watches = call("divar_watch_list", {}, env_extra)
        assert watches["count"] == 0 and "store" in watches, watches
        help_payload = call("divar_help", {}, env_extra)
        assert help_payload["recipes"] and help_payload["datasets"]["cities"] > 0, help_payload
        status = call("divar_status", {"probe": False}, env_extra)
        assert status["datasets"]["categories"] > 0 and status["server"]["version"], status

    print(
        f"smoke OK: {len(tools)} tools, {len(resources)} resources, {len(prompts)} prompts; "
        "cities, categories, trend, watches, help and status all answered offline"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
