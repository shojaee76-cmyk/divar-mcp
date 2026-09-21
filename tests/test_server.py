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
    assert names == {
        "divar_search",
        "divar_get_post",
        "divar_price_analysis",
        "divar_similar_posts",
        "divar_list_cities",
        "divar_list_categories",
        "divar_post_filters",
        "divar_search_url",
    }
    for tool in tools:
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object"
    get_post = next(t for t in tools if t["name"] == "divar_get_post")
    assert get_post["inputSchema"]["required"] == ["token"]


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
    assert _handle({"jsonrpc": "2.0", "id": 8, "method": "resources/list"})["result"] == {"resources": []}
    assert _handle({"jsonrpc": "2.0", "id": 9, "method": "prompts/list"})["result"] == {"prompts": []}


def test_cli_list_tools_and_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__
    assert main(["--list-tools"]) == 0
    tools = json.loads(capsys.readouterr().out)
    assert len(tools) == 8


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
    assert len(replies[1]["result"]["tools"]) == 8
    cities = json.loads(replies[2]["result"]["content"][0]["text"])
    assert cities["cities"][0]["id"] == "3"


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
    assert len(names) == 8
