"""divar-mcp: Model Context Protocol server for divar.ir.

Zero runtime dependencies: this speaks MCP's stdio transport directly
(newline-delimited JSON-RPC 2.0), so it runs under any MCP client
(Claude Desktop/Code, Cursor, Cline, Hermes, mcp-cli, ...) with nothing
but a Python 3.10+ interpreter.

Run:  divar-mcp            (stdio server, what MCP clients launch)
      divar-mcp --list-tools
      divar-mcp --call divar_search --args '{"query":"پژو ۲۰۶","city":"تهران"}'
"""

from __future__ import annotations

import io
import json
import sys
import traceback

from . import __version__
from .client import DivarError
from .tools import REGISTRY, TOOL_SPECS, call_tool

DEFAULT_PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05", "2024-10-07")

SERVER_INFO = {"name": "divar-mcp", "version": __version__}

INSTRUCTIONS = (
    "Read-only tools for divar.ir, the largest Iranian classifieds marketplace. "
    "Prices are in Toman. divar_search finds live listings (add query/city/category/price range), "
    "divar_get_post reads one post in full, divar_price_analysis gives a comparable price distribution "
    "so a seller knows what to ask, and divar_similar_posts lists the competition for an existing post. "
    "Resolve city names with divar_list_cities and category slugs with divar_list_categories first. "
    "This server never posts, edits or messages on Divar and never returns phone numbers: it only reads "
    "public listings."
)


def _reply(message_id, result=None, error=None) -> dict:
    out: dict = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result if result is not None else {}
    return out


def _handle(message: dict) -> dict | None:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}

    # notifications carry no id and must never be answered
    if message_id is None and method:
        return None

    if method == "initialize":
        requested = str(params.get("protocolVersion") or "")
        protocol = requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
        return _reply(
            message_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            },
        )

    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return _reply(message_id, {})

    if method == "tools/list":
        return _reply(message_id, {"tools": TOOL_SPECS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in REGISTRY:
            return _reply(
                message_id,
                {
                    "content": [{"type": "text", "text": f"unknown tool: {name!r}"}],
                    "isError": True,
                },
            )
        try:
            payload = call_tool(name, arguments)
            text = json.dumps(payload, ensure_ascii=False, indent=1, default=str)
            return _reply(message_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except DivarError as exc:
            hint = ""
            if "district is not valid" in exc.message:
                hint = " Get a valid district id from divar_get_post (field district_id)."
            elif "invalid category" in exc.message:
                hint = " Run divar_list_categories to see valid slugs."
            return _reply(
                message_id,
                {"content": [{"type": "text", "text": f"divar error: {exc.message}{hint}"}], "isError": True},
            )
        except TypeError as exc:
            return _reply(
                message_id,
                {"content": [{"type": "text", "text": f"bad arguments for {name}: {exc}"}], "isError": True},
            )
        except Exception as exc:  # pragma: no cover - defensive
            return _reply(
                message_id,
                {
                    "content": [
                        {"type": "text", "text": f"unexpected failure in {name}: {exc!r}"},
                    ],
                    "isError": True,
                },
            )

    if method in ("resources/list",):
        return _reply(message_id, {"resources": []})
    if method in ("resources/templates/list",):
        return _reply(message_id, {"resourceTemplates": []})
    if method in ("prompts/list",):
        return _reply(message_id, {"prompts": []})
    if method == "logging/setLevel":
        return _reply(message_id, {})

    return _reply(message_id, error={"code": -32601, "message": f"method not found: {method}"})


def serve(stream_in=None, stream_out=None) -> None:
    # Real MCP clients pipe JSON over stdio. Always speak UTF-8 bytes there:
    # Windows defaults stdio to the ANSI code page, which would mangle Persian.
    if stream_in is None:
        stream_in = getattr(sys.stdin, "buffer", sys.stdin)
    if stream_out is None:
        stream_out = getattr(sys.stdout, "buffer", sys.stdout)
    binary_out = not isinstance(stream_out, io.TextIOBase)

    while True:
        # readline(), never `for line in stream`: pipe iteration buffers.
        line = stream_in.readline()
        if not line:
            break
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _emit(stream_out, _reply(None, error={"code": -32700, "message": "parse error"}), binary_out)
            continue
        messages = message if isinstance(message, list) else [message]
        for item in messages:
            if not isinstance(item, dict):
                continue
            try:
                response = _handle(item)
            except Exception:  # pragma: no cover - defensive
                traceback.print_exc(file=sys.stderr)
                response = _reply(item.get("id"), error={"code": -32603, "message": "internal error"})
            if response is not None:
                _emit(stream_out, response, binary_out)


def _emit(stream_out, payload: dict, binary: bool) -> None:
    text = json.dumps(payload, ensure_ascii=False) + "\n"
    stream_out.write(text.encode("utf-8") if binary else text)
    stream_out.flush()


def _force_utf8_streams() -> None:
    """Never let a Windows ANSI code page break Persian output.

    `divar-mcp --list-tools > out.json` and `divar search` both print Persian.
    On Windows the default stdio encoding is the ANSI code page, which raises
    UnicodeEncodeError on those characters (and, when redirected, writes an
    error message into the output file). Force UTF-8 wherever we can.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - detached stream
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_streams()
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--version" in argv:
        print(__version__)
        return 0

    if "--list-tools" in argv:
        print(json.dumps(TOOL_SPECS, ensure_ascii=False, indent=1))
        return 0

    if "--call" in argv:
        index = argv.index("--call")
        try:
            name = argv[index + 1]
        except IndexError:
            print("usage: divar-mcp --call <tool> [--args '<json>']", file=sys.stderr)
            return 2
        arguments = {}
        if "--args" in argv:
            arguments = json.loads(argv[argv.index("--args") + 1])
        try:
            print(json.dumps(call_tool(name, arguments), ensure_ascii=False, indent=1, default=str))
            return 0
        except DivarError as exc:
            print(f"divar error: {exc}", file=sys.stderr)
            return 1

    try:
        serve()
    except (KeyboardInterrupt, BrokenPipeError):  # pragma: no cover
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
