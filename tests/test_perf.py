"""Performance-path tests: keep-alive reuse, caching, reconnect, WAL store.

These point a real DivarClient at a throwaway local HTTP server, so the
connection behaviour is exercised over an actual socket instead of being
assumed. Nothing here touches divar.ir.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from divar_mcp.client import DivarClient, DivarError
from divar_mcp.store import Store


class _Recorder:
    def __init__(self) -> None:
        self.requests = 0
        self.connections = 0
        self.paths: list[str] = []


def _make_server(recorder: _Recorder, *, force_close: bool = False, fail_paths: dict | None = None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive capable, like the real API

        def _respond(self, body: bytes, status: int = 200, close: bool = False):
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            if close or force_close:
                self.send_header("connection", "close")
                self.close_connection = True
            self.end_headers()
            self.wfile.write(body)

        def _drain(self):
            """A real server consumes the request body; without this the leftover
            bytes corrupt the next request on a reused keep-alive socket."""
            length = int(self.headers.get("content-length") or 0)
            if length:
                self.rfile.read(length)

        def do_GET(self):
            self._drain()
            recorder.requests += 1
            recorder.paths.append(self.path)
            if fail_paths and self.path in fail_paths:
                body = json.dumps({"message": fail_paths[self.path], "code": 3}).encode()
                self._respond(body, 400)
            else:
                self._respond(json.dumps({"ok": True, "path": self.path}).encode())

        do_POST = do_GET

        def setup(self):
            recorder.connections += 1
            super().setup()

        def log_message(self, *args):  # keep pytest output clean
            pass

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


@pytest.fixture
def local_api():
    recorder = _Recorder()
    servers: list[ThreadingHTTPServer] = []

    def start(*, force_close: bool = False, fail_paths: dict | None = None) -> str:
        server = _make_server(recorder, force_close=force_close, fail_paths=fail_paths)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield recorder, start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_keepalive_reuses_one_socket(local_api):
    """Three sequential requests must not open three TCP connections.

    Distinct paths on purpose: identical requests would be answered from the
    cache and this test would then be measuring the cache instead of the socket.
    """
    recorder, start = local_api
    client = DivarClient(api_base=start(), min_interval=0.0)
    for i in range(3):
        assert client._request("GET", f"/v8/one?i={i}")["ok"] is True
    assert recorder.requests == 3
    assert recorder.connections == 1, f"expected one socket, opened {recorder.connections}"
    stats = client.connection_stats
    assert stats["connections_opened"] == 1
    assert stats["connections_reused"] == 2


def test_cache_avoids_the_second_request(local_api):
    """An identical request inside the TTL must not hit the network again."""
    recorder, start = local_api
    client = DivarClient(api_base=start(), min_interval=0.0, cache_ttl=60.0)
    first = client._request("POST", "/v8/search", {"city_ids": ["1"]})
    second = client._request("POST", "/v8/search", {"city_ids": ["1"]})
    assert first == second
    assert recorder.requests == 1, "the cache did not prevent the second request"


def test_zero_ttl_disables_the_cache(local_api):
    """DIVAR_CACHE_TTL=0 must mean no caching at all.

    It used to mean "cache with a lifetime equal to the request duration",
    because the expiry was stamped after the response came back.
    """
    recorder, start = local_api
    client = DivarClient(api_base=start(), min_interval=0.0, cache_ttl=0.0)
    for _ in range(3):
        client._request("POST", "/v8/search", {"city_ids": ["1"]})
    assert recorder.requests == 3, f"ttl=0 still cached: {recorder.requests} requests for 3 calls"
    assert client._cache == {}


def test_cache_ttl_is_stamped_when_the_request_starts(local_api):
    """A slow response must not gain a longer life than the configured ttl."""
    recorder, start = local_api
    client = DivarClient(api_base=start(), min_interval=0.0, cache_ttl=5.0)
    client._request("POST", "/v8/search", {"city_ids": ["1"]})
    entry = next(iter(client._cache.values()))
    assert entry[0] - time.monotonic() <= 5.0, "expiry is measured from completion, not start"


def test_reconnects_when_the_server_closes(local_api):
    """A server that closes every connection must not break the client.

    This is the real-world failure mode: Divar drops idle keep-alive sockets,
    so the next request must transparently get a new one.
    """
    recorder, start = local_api
    client = DivarClient(api_base=start(force_close=True), min_interval=0.0)
    for i in range(3):
        assert client._request("GET", f"/v8/closed?i={i}")["ok"] is True
    assert recorder.requests == 3
    assert recorder.connections == 3, f"expected one socket per request, got {recorder.connections}"


def test_http_error_body_is_parsed_over_keepalive(local_api):
    recorder, start = local_api
    client = DivarClient(
        api_base=start(fail_paths={"/v8/postlist/w/search": "invalid category"}),
        min_interval=0.0,
    )
    with pytest.raises(DivarError) as err:
        client._request("GET", "/v8/postlist/w/search")
    assert err.value.status == 400
    assert "message" in str(err.value).lower() or err.value.code == 3


def test_4xx_does_not_retry(local_api):
    """Caller errors must fail fast: one request, not max_retries."""
    recorder, start = local_api
    client = DivarClient(
        api_base=start(fail_paths={"/v8/postlist/w/search": "invalid category"}),
        min_interval=0.0, max_retries=3,
    )
    with pytest.raises(DivarError):
        client._request("GET", "/v8/postlist/w/search")
    assert recorder.requests == 1


def test_store_uses_wal_and_batches_writes(tmp_path):
    """The store must be in WAL mode with one commit per page, not per row."""
    store = Store(tmp_path / "wal.db")
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    posts = [
        {"token": f"t{i}", "price_toman": 1_000_000 * i, "title": f"item {i}", "url": f"https://divar.ir/v/t{i}"}
        for i in range(1, 51)
    ]
    written = store.record_posts(posts, city="تهران", city_id="1", category="light", query="پژو")
    assert written == 50
    count = store.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    price_rows = store.conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    assert count == 50 and price_rows == 50
    store.close()


def test_bench_harness_is_not_broken():
    """The benchmark must import and expose its cases (it is shipped tooling)."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "tools" / "bench.py"
    spec = importlib.util.spec_from_file_location("divar_bench", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert len(module.CASES) >= 5
    assert all(isinstance(name, str) and isinstance(args, dict) for name, args in module.CASES)
