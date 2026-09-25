"""divar-mcp: Model Context Protocol server for divar.ir.

Zero runtime dependencies: this speaks MCP's stdio transport directly
(newline-delimited JSON-RPC 2.0), so it runs under any MCP client
(Claude Desktop/Code, Cursor, Cline, Hermes, mcp-cli, ...) with nothing
but a Python 3.10+ interpreter.

Beyond tools it serves what a serious MCP client expects:

  tools     19 read/write tools, each with an inputSchema, an outputSchema and
            MCP annotations (readOnlyHint / destructiveHint / idempotentHint /
            openWorldHint)
  outputs   every successful call returns both a text block and
            ``structuredContent`` so an agent can parse instead of regex
  resources divar://cities, divar://categories, divar://cities/slugs,
            divar://status, divar://help
  prompts   price-an-item, appraise-listing, watch-market, find-deals

Errors are self-repairing: a wrong city or category comes back with the closest
real values in ``suggestions`` plus a ``hint``, never just a bare failure.

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
from .datasets import load_categories, load_cities, load_city_slugs
from .tools import REGISTRY, TOOL_SPECS, call_tool, divar_help, divar_status

DEFAULT_PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05", "2024-10-07")

SERVER_INFO = {"name": "divar-mcp", "version": __version__}

INSTRUCTIONS = (
    "Read-only tools for divar.ir, Iran's largest classifieds marketplace, plus local state for "
    "watching searches and tracking prices over time. Prices are in Toman. "
    "Start with divar_help for the capability map and recipes. "
    "divar_search finds live listings (query, one or many cities, category, Toman price range, "
    "photo-only, district ids, brand/model, plus client-side age and keyword filters). "
    "divar_get_post reads one listing in full. "
    "divar_price_analysis gives a comparable price distribution so a seller knows what to ask; "
    "divar_appraise_post judges an existing listing (below/fair/above market); "
    "divar_find_deals ranks underpriced listings with an explainable score; "
    "divar_market_breakdown maps price by district; divar_price_trend reports local day-by-day history. "
    "divar_watch_create/check/list/delete remember a search so later runs report only new listings. "
    "divar_export writes CSV/JSONL to disk for bulk work. "
    "Inputs are forgiving: cities accept an id, Persian name or ASCII slug, categories accept a slug or "
    "Persian name, and a wrong value returns close matches to try. "
    "This server never posts, edits, messages or reports on Divar, and never collects phone numbers."
)

PROMPTS = [
    {
        "name": "price-an-item",
        "title": "What should I ask for this?",
        "description": "Value an item against live divar.ir comparables and produce an asking price with sources.",
        "arguments": [
            {"name": "item", "description": "What you want to sell (Persian or English).", "required": True},
            {"name": "city", "description": "City (default تهران).", "required": False},
            {"name": "condition", "description": "Condition or defects, if any.", "required": False},
        ],
    },
    {
        "name": "appraise-listing",
        "title": "Is this listing fairly priced?",
        "description": "Judge one divar.ir listing against live comparables and say whether to negotiate.",
        "arguments": [
            {"name": "listing", "description": "Post token or divar.ir URL.", "required": True},
            {"name": "city", "description": "City override for the comparables.", "required": False},
        ],
    },
    {
        "name": "find-deals",
        "title": "Find underpriced listings",
        "description": "Hunt below-market listings for a query and summarise the best candidates.",
        "arguments": [
            {"name": "query", "description": "What to hunt for.", "required": True},
            {"name": "city", "description": "City (default تهران).", "required": False},
            {"name": "budget", "description": "Maximum price in Toman.", "required": False},
        ],
    },
    {
        "name": "watch-market",
        "title": "Watch a search over time",
        "description": "Create a watch for a search and explain how to check it later for new listings.",
        "arguments": [
            {"name": "name", "description": "Short watch name.", "required": True},
            {"name": "query", "description": "Search text to watch.", "required": True},
            {"name": "city", "description": "City (default تهران).", "required": False},
        ],
    },
]

RESOURCES = [
    {"uri": "divar://cities", "name": "Divar cities", "description": "City id, Persian name and ASCII slug for every harvested city.",
     "mimeType": "application/json"},
    {"uri": "divar://categories", "name": "Divar categories", "description": "Category slugs with Persian names and breadcrumb parents.",
     "mimeType": "application/json"},
    {"uri": "divar://cities/slugs", "name": "Verified city page slugs", "description": "City id to divar.ir /s/<slug> mapping that passed validation.",
     "mimeType": "application/json"},
    {"uri": "divar://help", "name": "Capability map", "description": "Tools, units, datasets and recipes for this server.",
     "mimeType": "application/json"},
    {"uri": "divar://status", "name": "Server status", "description": "Dataset sizes, store stats and cache counters (no network probe).",
     "mimeType": "application/json"},
]


def _reply(message_id, result=None, error=None) -> dict:
    out: dict = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result if result is not None else {}
    return out


def _tool_error_payload(exc: Exception) -> dict:
    if isinstance(exc, DivarError):
        payload = exc.as_dict()
        if payload["suggestions"]:
            payload["next"] = "Retry with one of the suggested values."
        return payload
    if isinstance(exc, TypeError):
        return {
            "error": f"bad arguments: {exc}",
            "hint": "Call the tool again with only the arguments named in its inputSchema.",
            "suggestions": [],
            "retryable": False,
        }
    return {"error": repr(exc), "hint": "Unexpected failure; divar_status can help diagnose.",
            "suggestions": [], "retryable": False}


def _text(payload) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, indent=1, default=str)


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
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False, "subscribe": False},
                    "prompts": {"listChanged": False},
                },
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
            payload = {
                "error": f"unknown tool {name!r}",
                "suggestions": sorted(REGISTRY),
                "hint": "divar_help lists the tools this server provides.",
                "retryable": False,
            }
            return _reply(message_id, {
                "content": [{"type": "text", "text": _text(payload)}],
                "structuredContent": payload,
                "isError": True,
            })
        try:
            result = call_tool(name, arguments)
            structured = result if isinstance(result, dict) else {"result": result}
            return _reply(message_id, {
                "content": [{"type": "text", "text": _text(result)}],
                "structuredContent": structured,
                "isError": False,
            })
        except Exception as exc:  # DivarError carries the repair hints
            payload = _tool_error_payload(exc)
            return _reply(message_id, {
                "content": [{"type": "text", "text": _text(payload)}],
                "structuredContent": payload,
                "isError": True,
            })

    if method == "resources/list":
        return _reply(message_id, {"resources": RESOURCES})

    if method == "resources/read":
        uri = str(params.get("uri") or "")
        try:
            text = _read_resource(uri)
        except DivarError as exc:
            return _reply(message_id, error={"code": -32602, "message": str(exc)})
        return _reply(message_id, {
            "contents": [{"uri": uri, "mimeType": "application/json", "text": text}]
        })

    if method == "resources/templates/list":
        return _reply(message_id, {"resourceTemplates": []})

    if method == "prompts/list":
        return _reply(message_id, {"prompts": PROMPTS})

    if method == "prompts/get":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        for prompt in PROMPTS:
            if prompt["name"] == name:
                return _reply(message_id, {
                    "description": prompt["description"],
                    "messages": _prompt_messages(name, arguments),
                })
        return _reply(message_id, error={"code": -32602, "message": f"unknown prompt {name!r}"})

    if method == "logging/setLevel":
        return _reply(message_id, {})

    return _reply(message_id, error={"code": -32601, "message": f"method not found: {method}"})


def _read_resource(uri: str) -> str:
    if uri == "divar://cities":
        slugs = load_city_slugs()
        payload = [{"id": cid, "name": name, "slug": slugs.get(str(cid))}
                   for cid, name in load_cities()["by_id"].items()]
    elif uri == "divar://categories":
        payload = load_categories()
    elif uri == "divar://cities/slugs":
        payload = load_city_slugs()
    elif uri == "divar://help":
        payload = divar_help()
    elif uri == "divar://status":
        payload = divar_status(probe=False)
    else:
        raise DivarError(f"unknown resource {uri!r}",
                         suggestions=[r["uri"] for r in RESOURCES],
                         hint="Call resources/list to see the available URIs.")
    return _text(payload)


def _prompt_messages(name: str, arguments: dict) -> list[dict]:
    city = arguments.get("city") or "تهران"
    if name == "price-an-item":
        item = arguments.get("item", "")
        condition = arguments.get("condition") or "not given"
        body = (
            f"I want to sell: {item} (condition: {condition}), in {city}.\n\n"
            "1. Call divar_list_categories with the Persian name of the item to get the right slug.\n"
            "2. Call divar_price_analysis with that category, the item text and the city, pages=2.\n"
            "3. Answer with: the suggested ask range in Toman, the median, how many live listings were "
            "sampled, three fresh comparables with links, and one honest caveat about condition or "
            "unusual cheap listings.\n"
            "Do not invent prices: if the sample is small, say so."
        )
    elif name == "appraise-listing":
        body = (
            f"Appraise this divar.ir listing: {arguments.get('listing', '')} (city for comparables: {city}).\n\n"
            "1. divar_get_post on the token or URL.\n"
            "2. divar_appraise_post for the verdict against live comparables.\n"
            "3. Report: verdict, percentile, delta versus median, confidence, the cheapest alternatives, "
            "and a concrete negotiation suggestion."
        )
    elif name == "find-deals":
        budget = arguments.get("budget")
        budget_line = f" Stay under {budget} Toman." if budget else ""
        body = (
            f"Find underpriced listings for: {arguments.get('query', '')} in {city}.{budget_line}\n\n"
            "1. divar_find_deals with pages=3 and require_photo=true.\n"
            "2. For the top 3 deals call divar_get_post and check the described condition.\n"
            "3. Summarise with links, prices, the deal score reasons, and flag anything suspicious "
            "(missing photos, vague titles, prices far below the band)."
        )
    else:  # watch-market
        body = (
            f"Set up a watch named {arguments.get('name', 'watch')!r} for {arguments.get('query', '')} in {city}.\n\n"
            "1. divar_watch_create with the name, query and city.\n"
            "2. Explain that divar_watch_check later returns only listings that were not in the baseline.\n"
            "3. If this runs on a schedule, suggest how often to check for the kind of item."
        )
    return [{"role": "user", "content": {"type": "text", "text": body}}]


def _encode(line: str, stream) -> bytes | str:
    """Write UTF-8 bytes to real pipes, plain text to test doubles."""
    if isinstance(stream, io.TextIOBase) or not hasattr(stream, "buffer"):
        return line
    try:
        return line.encode("utf-8")
    except AttributeError:  # pragma: no cover - defensive
        return line


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

    if "--resources" in argv:
        print(json.dumps(RESOURCES, ensure_ascii=False, indent=1))
        return 0

    if "--prompts" in argv:
        print(json.dumps(PROMPTS, ensure_ascii=False, indent=1))
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
            print(_text(exc.as_dict()), file=sys.stderr)
            return 1

    try:
        serve()
    except (KeyboardInterrupt, BrokenPipeError):  # pragma: no cover
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
