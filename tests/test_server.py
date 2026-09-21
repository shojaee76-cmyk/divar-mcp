"""MCP protocol conformance for the zero-dependency stdio server."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from divar_mcp import __version__
from divar_mcp.server import SUPPORTED_PROTOCOLS, _handle, main, serve

ROOT = Path(__file__).resolve().parent.parent


def run_lines(*messages: dict) -> list[dict]:
    payload = "".join(json.dumps(m) + "\n" for m in messages)
    out = io.StringIO()
    serve(stream_in=io.StringIO(payload), stream_out=out)
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def test_initialize_negotiates_protocol():
    res = run_lines(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
    )[0]["result"]
    assert res["protocolVersion"] in SUPPORTED_PROTOCOLS
    assert res["serverInfo"]["name"] == "divar-mcp"
    assert res["serverInfo"]["version"] == __version__
    assert "tools" in res["capabilities"]
    assert "divar.ir" in res["instructions"]


def test_unknown_protocol_falls_back():
    res = run_lines(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}
    )[0]["result"]
    assert res["protocolVersion"] in SUPPORTED_PROTOCOLS


def test_notifications_get_no_reply():
    assert run_lines({"jsonrpc": "2.0", "method": "notifications/initialized"}) == []


def test_tools_list_schema():
    tools = run_lines({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})[0]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert len(tools) == 19, f"expected 19 tools, got {len(tools)}"
    assert {
        "divar_search", "divar_get_post", "divar_price_analysis", "divar_similar_posts",
        "divar_list_cities", "divar_list_categories", "divar_post_filters", "divar_search_url",
        "divar_appraise_post", "divar_find_deals", "divar_market_breakdown", "divar_price_trend",
        "divar_watch_create", "divar_watch_check", "divar_watch_list", "divar_watch_delete",
        "divar_export", "divar_status", "divar_help",
    } <= names
    for tool in tools:
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object"
        # every tool advertises an output schema and MCP annotations
        assert tool["outputSchema"]["type"] == "object", tool["name"]
        assert "readOnlyHint" in tool["annotations"], tool["name"]
        assert tool["title"]
    get_post = next(t for t in tools if t["name"] == "divar_get_post")
    assert get_post["inputSchema"]["required"] == ["token"]


def test_output_schemas_match_what_tools_return():
    """The advertised output contract must hold for real responses.

    Rules checked for every offline-callable tool:
      * every key in the schema's ``required`` list is actually returned
      * a key that appears without being declared is only allowed when the
        schema explicitly sets additionalProperties (our _out() helper does)
    """
    from divar_mcp.tools import TOOL_SPECS, call_tool

    specs = {spec["name"]: spec["outputSchema"] for spec in TOOL_SPECS}
    offline_calls = {
        "divar_help": {},
        "divar_list_cities": {"query": "تهران"},
        "divar_list_categories": {"query": "mobile"},
        "divar_price_trend": {"query": "x"},
        "divar_watch_list": {},
        "divar_status": {"probe": False},
    }
    for name, args in offline_calls.items():
        schema = specs[name]
        payload = call_tool(name, args)
        missing = set(schema.get("required", [])) - set(payload)
        assert not missing, f"{name} is missing required output keys: {sorted(missing)}"
        extra = set(payload) - set(schema.get("properties", {}))
        assert not extra or schema.get("additionalProperties") is True, (
            f"{name} returns undeclared keys {sorted(extra)} and does not allow additionalProperties"
        )


def test_call_tool_offline():
    response = run_lines(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "divar_list_cities", "arguments": {"query": "تهران"}}}
    )[0]["result"]
    assert response["isError"] is False
    payload = json.loads(response["content"][0]["text"])
    assert payload["cities"][0]["id"] == "1"


def test_call_categories_offline():
    response = run_lines(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "divar_list_categories", "arguments": {"query": "موبایل"}}}
    )[0]["result"]
    payload = json.loads(response["content"][0]["text"])
    assert any(c["slug"] == "mobile-phones" for c in payload["categories"])


def test_call_unknown_tool_is_error():
    response = run_lines(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
    )[0]["result"]
    assert response["isError"] is True
    assert "unknown tool" in response["content"][0]["text"]


def test_call_bad_arguments_is_error():
    response = run_lines(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "divar_search", "arguments": {"sort": "cheapest_ever"}}}
    )[0]["result"]
    assert response["isError"] is True
    assert "unknown sort" in response["content"][0]["text"]


def test_parse_error_and_unknown_method():
    out = io.StringIO()
    serve(stream_in=io.StringIO("not json\n" + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "x/y"}) + "\n"),
          stream_out=out)
    replies = [json.loads(l) for l in out.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["error"]["code"] == -32601


def test_ping_and_empty_lists():
    reply = _handle({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert reply == {"jsonrpc": "2.0", "id": 7, "result": {}}
    resources = _handle({"jsonrpc": "2.0", "id": 8, "method": "resources/list"})["result"]["resources"]
    assert {"divar://cities", "divar://categories", "divar://status", "divar://help"} <= {r["uri"] for r in resources}
    prompts = _handle({"jsonrpc": "2.0", "id": 9, "method": "prompts/list"})["result"]["prompts"]
    assert {p["name"] for p in prompts} >= {"price-an-item", "appraise-listing", "find-deals", "watch-market"}


def test_cli_list_tools_and_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__
    assert main(["--list-tools"]) == 0
    tools = json.loads(capsys.readouterr().out)
    assert len(tools) == 19


def test_stdio_end_to_end_subprocess():
    """Launch the real server process the way an MCP client does.

    Feed every message up front and close stdin: the server answers and exits
    when the pipe closes, which is exactly the contract MCP clients rely on.
    """
    payload = "".join(
        json.dumps(m) + "\n"
        for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "divar_list_cities", "arguments": {"query": "مشهد"}}},
        )
    )
    proc = subprocess.run(
        [sys.executable, "-m", "divar_mcp.server"],
        input=payload, capture_output=True, text=True, timeout=120,
        cwd=str(ROOT), env=_env(), encoding="utf-8",
    )
    replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert len(replies) == 3, proc.stderr[-500:]
    assert replies[0]["result"]["serverInfo"]["name"] == "divar-mcp"
    assert len(replies[1]["result"]["tools"]) == 19
    cities = json.loads(replies[2]["result"]["content"][0]["text"])
    assert cities["cities"][0]["id"] == "3"


def test_persian_survives_ansi_stdio():
    """Regression: on Windows (or PYTHONIOENCODING=cp1252) redirecting this CLI
    to a file used to crash with UnicodeEncodeError and write a traceback into
    the output file. stdout must be forced to UTF-8."""
    import os
    import subprocess
    import sys

    env = _env()
    env["PYTHONIOENCODING"] = "cp1252"
    out_file = ROOT / "tools" / "ansi_smoke.json"
    proc = subprocess.run(
        [sys.executable, "-m", "divar_mcp.server", "--list-tools"],
        stdout=open(out_file, "wb"), stderr=subprocess.PIPE,
        cwd=str(ROOT), env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-500:]
    raw = out_file.read_bytes()
    assert "تهران".encode("utf-8") in raw, "Persian text did not survive the redirect as UTF-8"
    tools = json.loads(raw.decode("utf-8"))
    assert len(tools) == 19
    out_file.unlink()


def _env():
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def test_official_mcp_client_can_talk_to_server():
    """If the official SDK is installed, prove interop with it too."""
    mcp_client = pytest.importorskip("mcp.client.stdio")
    import asyncio

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "divar_mcp.server"],
        env=_env(),
        cwd=str(ROOT),
    )

    async def scenario():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                assert "divar_search" in names
                result = await session.call_tool("divar_list_categories", {"query": "mobile"})
                # field name differs across SDK majors (isError / is_error)
                is_error = getattr(result, "isError", getattr(result, "is_error", False))
                assert is_error in (False, None)
                text = result.content[0].text
                assert "mobile-phones" in text
                return names

    names = asyncio.run(scenario())
    assert len(names) == 19
