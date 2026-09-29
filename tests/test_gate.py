import http.client
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

import pytest

from gate.gate import Hold, make_servers

TTL = 1800
LLM = "qwen3.8-flash-next"
GENERATION = "comfyui"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_hold_is_inactive_until_opened_and_after_release():
    clock = FakeClock()
    hold = Hold(ttl=TTL, clock=clock)
    assert not hold.active()

    hold.open()
    assert hold.active()
    assert hold.remaining() == TTL

    hold.release()
    assert not hold.active()


def test_hold_lapses_after_ttl_without_renewal():
    clock = FakeClock()
    hold = Hold(ttl=TTL, clock=clock)
    hold.open()

    clock.now += TTL - 1
    assert hold.active()
    clock.now += 1
    assert not hold.active()
    assert hold.remaining() == 0


def test_renewal_extends_the_lease_from_now():
    clock = FakeClock()
    hold = Hold(ttl=TTL, clock=clock)
    hold.open()

    clock.now += TTL - 10
    hold.open()
    clock.now += TTL - 1
    assert hold.active()


# --- through the HTTP interface, with a fake llama-swap behind the gate ---


class FakeLlamaSwap(BaseHTTPRequestHandler):
    """Echoes what it received; `/stream` sends two SSE events with a pause
    the test controls, to prove the gate doesn't buffer responses."""

    protocol_version = "HTTP/1.1"
    seen: list[tuple[str, str, bytes]] = []
    release_second_event = threading.Event()

    def _handle(self) -> None:
        if self.headers.get("Upgrade", "").lower() == "websocket":
            # Accept the upgrade, then echo raw bytes until the client closes.
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            while data := self.request.recv(1024):
                self.request.sendall(data)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        FakeLlamaSwap.seen.append((self.command, self.path, body))
        if self.path == "/stalls-mid-stream":
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self._chunk(b"data: one\n\n")
            FakeLlamaSwap.release_second_event.wait(timeout=5)
            return
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self._chunk(b"data: one\n\n")
            FakeLlamaSwap.release_second_event.wait(timeout=5)
            self._chunk(b"data: two\n\n")
            self._chunk(b"")
            return
        payload = json.dumps(
            {"method": self.command, "path": self.path, "body": body.decode()}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _chunk(self, data: bytes) -> None:
        self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
        self.wfile.flush()

    do_GET = do_POST = do_PUT = do_DELETE = _handle

    def log_message(self, *args) -> None:
        pass


def _serve(server) -> None:
    threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()


class Gate:
    def __init__(self, public_port: int, admin_port: int, hold: Hold) -> None:
        self.public_port = public_port
        self.admin_port = admin_port
        self.hold = hold

    def request(self, method, path, body=None, admin=False):
        port = self.admin_port if admin else self.public_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        conn.request(method, path, body=data)
        return conn.getresponse()

    def chat(self, model):
        return self.request(
            "POST",
            "/v1/chat/completions",
            {"model": model, "messages": [{"role": "user", "content": "hi"}]},
        )


@pytest.fixture
def gate() -> Iterator[Gate]:
    FakeLlamaSwap.seen = []
    FakeLlamaSwap.release_second_event.clear()
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeLlamaSwap)
    _serve(upstream)
    hold = Hold(ttl=TTL)
    public, admin = make_servers(
        upstream=f"127.0.0.1:{upstream.server_address[1]}",
        public_addr=("127.0.0.1", 0),
        admin_addr=("127.0.0.1", 0),
        hold=hold,
        generation_models={GENERATION},
        upstream_timeout=0.5,
        host_prefixes={"comfyui.test": "/upstream/comfyui"},
    )
    _serve(public)
    _serve(admin)
    yield Gate(public.server_address[1], admin.server_address[1], hold)
    for server in (public, admin, upstream):
        server.shutdown()
        server.server_close()


def test_without_a_hold_requests_pass_through_unchanged(gate):
    resp = gate.chat(LLM)
    assert resp.status == 200
    echoed = json.loads(resp.read())
    assert echoed["path"] == "/v1/chat/completions"
    assert json.loads(echoed["body"])["model"] == LLM


def test_query_strings_are_forwarded(gate):
    resp = gate.request("GET", "/upstream/x/health?full=1")
    assert json.loads(resp.read())["path"] == "/upstream/x/health?full=1"


def test_streamed_responses_are_not_buffered(gate):
    resp = gate.request("POST", "/stream", {"model": LLM, "stream": True})
    assert resp.status == 200
    first = resp.read1()
    assert first == b"data: one\n\n"  # arrived before the upstream finished
    FakeLlamaSwap.release_second_event.set()
    assert resp.read() == b"data: two\n\n"


def test_an_active_hold_refuses_llm_requests_with_retry_after(gate):
    gate.hold.open()
    resp = gate.chat(LLM)
    assert resp.status == 503
    assert 0 < int(resp.getheader("Retry-After")) <= TTL
    resp.read()
    assert FakeLlamaSwap.seen == []  # never reached llama-swap


@pytest.mark.parametrize(
    "path",
    [
        "/upstream/qwen3.8-flash-next/health",
        "/upstream/qwen3.8-flash-next/metrics",
        "/v1/embeddings",
        "/completion",
        "/props?model=qwen3.8-flash-next",
        "/comfyui/prompt",
    ],
)
def test_an_active_hold_refuses_every_route_that_can_start_an_llm(gate, path):
    gate.hold.open()
    resp = gate.request("POST", path, {"model": LLM})
    assert resp.status == 503
    resp.read()


def test_a_request_with_no_readable_model_is_refused_during_a_hold(gate):
    gate.hold.open()
    resp = gate.request("POST", "/v1/chat/completions", b"not json")
    assert resp.status == 503
    resp.read()


@pytest.mark.parametrize("path", ["/v1/models", "/models", "/health", "/metrics", "/running"])
def test_an_active_hold_leaves_the_open_paths_open(gate, path):
    gate.hold.open()
    resp = gate.request("GET", path)
    assert resp.status == 200
    resp.read()


def test_an_active_hold_lets_generation_group_models_through(gate):
    gate.hold.open()
    by_body = gate.chat(GENERATION)
    assert by_body.status == 200
    by_body.read()
    by_path = gate.request("GET", f"/upstream/{GENERATION}/system_stats")
    assert by_path.status == 200
    by_path.read()


def test_admin_api_opens_renews_reports_and_releases_the_hold(gate):
    opened = gate.request("POST", "/hold", admin=True)
    assert opened.status == 200
    assert json.loads(opened.read()) == {"active": True, "expires_in": TTL}

    assert gate.chat(LLM).status == 503

    renewed = gate.request("PUT", "/hold", admin=True)
    assert renewed.status == 200
    assert json.loads(renewed.read())["active"] is True

    status = gate.request("GET", "/hold", admin=True)
    assert json.loads(status.read())["active"] is True

    released = gate.request("DELETE", "/hold", admin=True)
    assert released.status == 200
    assert json.loads(released.read()) == {"active": False, "expires_in": 0}

    resp = gate.chat(LLM)
    assert resp.status == 200
    resp.read()


def test_renewing_after_a_gate_restart_reestablishes_the_hold(gate):
    # A restart starts with no hold; the next heartbeat (PUT) must bring it back.
    renewed = gate.request("PUT", "/hold", admin=True)
    assert renewed.status == 200
    renewed.read()
    assert gate.hold.active()


def test_the_admin_api_is_not_served_on_the_public_port(gate):
    resp = gate.request("POST", "/hold")
    resp.read()
    assert not gate.hold.active()
    assert FakeLlamaSwap.seen[-1][1] == "/hold"  # just proxied like anything else


def test_admin_api_rejects_unknown_paths(gate):
    resp = gate.request("GET", "/nope", admin=True)
    assert resp.status == 404
    resp.read()


def test_an_upstream_stalling_mid_stream_truncates_rather_than_corrupting(gate):
    sock = socket.create_connection(("127.0.0.1", gate.public_port), timeout=5)
    sock.sendall(b"GET /stalls-mid-stream HTTP/1.1\r\nHost: x\r\n\r\n")
    raw = b""
    while chunk := sock.recv(65536):  # the gate closes once its upstream read times out
        raw += chunk
    FakeLlamaSwap.release_second_event.set()
    assert raw.count(b"HTTP/1.1 ") == 1  # no 502 status line spliced into the stream
    assert raw.count(b"\r\nDate: ") == 1
    assert b"data: one" in raw


def test_a_non_string_model_is_refused_during_a_hold(gate):
    gate.hold.open()
    resp = gate.request("POST", "/v1/chat/completions", {"model": ["a", "b"]})
    assert resp.status == 503
    resp.read()


def _websocket_handshake(port: int, path: str) -> tuple[socket.socket, bytes]:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(
        f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
        "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n".encode()
    )
    response = b""
    while b"\r\n\r\n" not in response:
        response += sock.recv(1024)
    return sock, response


def test_websocket_upgrades_are_tunnelled_to_llama_swap(gate):
    sock, response = _websocket_handshake(gate.public_port, "/upstream/comfyui/ws")
    assert response.startswith(b"HTTP/1.1 101")

    sock.sendall(b"ping")
    assert sock.recv(1024) == b"ping"
    sock.close()


def test_websocket_upgrades_to_the_llm_are_refused_during_a_hold(gate):
    gate.request("POST", "/hold", admin=True)

    sock, response = _websocket_handshake(gate.public_port, "/upstream/qwen3.8-flash-next/ws")
    assert response.startswith(b"HTTP/1.1 503")
    sock.close()


def test_websocket_upgrades_to_generation_models_pass_during_a_hold(gate):
    gate.request("POST", "/hold", admin=True)

    sock, response = _websocket_handshake(gate.public_port, "/upstream/comfyui/ws")
    assert response.startswith(b"HTTP/1.1 101")
    sock.close()


def _get_with_host(gate, host, path):
    conn = http.client.HTTPConnection("127.0.0.1", gate.public_port, timeout=5)
    conn.request("GET", path, headers={"Host": host})
    return conn.getresponse()


def test_a_prefixed_host_is_sent_to_its_model_keeping_the_raw_path(gate):
    _get_with_host(gate, "comfyui.test", "/api/userdata/workflows%2Fa.json?overwrite=true").read()

    assert FakeLlamaSwap.seen[-1][1] == "/upstream/comfyui/api/userdata/workflows%2Fa.json?overwrite=true"


def test_other_hosts_are_not_prefixed(gate):
    _get_with_host(gate, "dgx.test", "/v1/models").read()

    assert FakeLlamaSwap.seen[-1][1] == "/v1/models"


def test_a_prefixed_host_to_a_generation_model_passes_a_hold(gate):
    gate.request("POST", "/hold", admin=True)

    assert _get_with_host(gate, "comfyui.test", "/anything").status == 200


def test_an_already_prefixed_path_is_not_prefixed_twice(gate):
    _get_with_host(gate, "comfyui.test", "/upstream/comfyui/api/queue").read()

    assert FakeLlamaSwap.seen[-1][1] == "/upstream/comfyui/api/queue"
