"""CLI contract tests.

These exist because a live demo (not the library tests) caught the CLI forwarding
a shared flag, ``has_photo``, to tools that did not accept it. The wiring between
argparse namespaces and tool signatures now has an explicit contract.
"""

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from divar_mcp.tools import REGISTRY

ROOT = Path(__file__).resolve().parent.parent

# every flag a search-shaped subcommand may build
SEARCH_KWARGS = ("query", "city", "cities", "category", "price_min", "price_max", "has_photo", "pages")

# divar_price_trend is deliberately absent: local history is keyed by
# city + category + query, so the CLI gives it its own narrower argument set
# rather than silently dropping flags.
SEARCH_SHAPED = (
    "divar_search", "divar_price_analysis", "divar_find_deals", "divar_market_breakdown",
    "divar_export", "divar_watch_create",
)
ALLOWED_MISSING: dict[str, set[str]] = {}


@pytest.mark.parametrize("tool", SEARCH_SHAPED)
def test_cli_shared_flags_are_accepted_by_the_tool(tool):
    params = inspect.signature(REGISTRY[tool]).parameters
    missing = set(SEARCH_KWARGS) - set(params)
    assert missing <= ALLOWED_MISSING.get(tool, set()), (
        f"{tool} does not accept {sorted(missing)}: the CLI would crash on that flag"
    )


def _run_cli(*args: str, store: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["DIVAR_STORE"] = str(store)
    return subprocess.run(
        [sys.executable, "-m", "divar_mcp.cli", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=180, env=env, cwd=str(ROOT),
    )


@pytest.mark.parametrize(
    "argv",
    [
        ("help",),
        ("store", "stats"),
        ("cities", "تهران"),
        ("categories", "mobile"),
        ("status", "--no-probe"),
        ("url", "پژو", "--city", "تهران"),
        ("watch", "list"),
    ],
)
def test_cli_commands_run_offline(argv, tmp_path):
    proc = _run_cli(*argv, store=tmp_path / "s.db")
    assert proc.returncode == 0, proc.stderr[-600:]
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict), payload


def test_cli_help_lists_recipes(tmp_path):
    payload = json.loads(_run_cli("help", store=tmp_path / "s.db").stdout)
    assert payload["server"]["version"]
    assert payload["recipes"] and payload["tools"]


def test_cli_reports_errors_as_json_not_a_traceback(tmp_path):
    proc = _run_cli("post", "definitely-not-a-token", store=tmp_path / "s.db")
    # network is required, so either it answers or it fails cleanly as JSON
    if proc.returncode != 0:
        assert "Traceback" not in proc.stderr
        payload = json.loads(proc.stderr)
        assert "error" in payload and "hint" in payload


@pytest.mark.parametrize("command", ["search", "price", "deals", "breakdown", "export", "trend"])
def test_cli_accepts_both_positional_and_flag_query(command, tmp_path):
    """`divar <cmd> "text"` and `divar <cmd> -q text` must both parse."""
    import argparse

    from divar_mcp.cli import main

    for argv in ([command, "پژو", "--help"], [command, "-q", "پژو", "--help"]):
        try:
            main(argv)
        except SystemExit as exc:  # --help exits 0
            assert exc.code == 0, f"{argv} exited {exc.code}"
        except argparse.ArgumentError as exc:  # pragma: no cover
            raise AssertionError(f"{argv} failed to parse: {exc}")


def test_cli_subcommands_have_help(tmp_path):
    for command in ("search", "post", "price", "deals", "appraise", "breakdown", "trend",
                    "similar", "watch", "export", "cities", "categories", "filters", "url",
                    "status", "help", "store"):
        proc = _run_cli(command, "--help", store=tmp_path / "s.db")
        assert proc.returncode == 0, f"{command} --help failed: {proc.stderr[-300:]}"
