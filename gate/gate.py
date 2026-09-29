"""gate: the Spark's front door on :8000, in front of llama-swap (ADR 0019).

Proxies everything to llama-swap, except while a Window hold is active: then
only the open paths and generation-group models get through, and everything
else gets a fast 503 with Retry-After. The hold is managed through a separate
admin API that listens on 127.0.0.1 only.

Stdlib only, so compose can run it on a stock python image with this file
bind-mounted - no image build, no dependencies.
"""

import http.client
import json
import math
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

# Never start a model, so they stay reachable during a hold.
OPEN_PATHS = {"/v1/models", "/models", "/health", "/metrics", "/running"}
# Hop-by-hop headers, plus the body framing ones: the gate re-frames every
# response itself (Content-Length or its own chunking).
NOT_FORWARDED = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
    "server", "date",  # send_response adds the gate's own
}
# Longer than llama-swap's healthCheckTimeout (3600s): a cold start blocks the
# request, it doesn't fail it.
UPSTREAM_TIMEOUT_SECONDS = 3900


def _pipe(source: socket.socket, sink: socket.socket) -> None:
    """Copies bytes until `source` closes, then half-closes `sink`."""
    try:
        while data := source.recv(65536):
            sink.sendall(data)
    except OSError:
        pass
    finally:
        try:
            sink.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class Hold:
    """The Window hold: an in-memory lease that lapses on its own after `ttl`
    seconds unless renewed. A gate restart drops it; the next heartbeat
    re-establishes it."""

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def open(self) -> None:
        """Open or renew: the lease runs `ttl` seconds from now."""
        with self._lock:
            self._expires_at = self._clock() + self._ttl

    def release(self) -> None:
        with self._lock:
            self._expires_at = 0.0

    def remaining(self) -> int:
        """Whole seconds left on the lease, 0 when inactive."""
        with self._lock:
            return max(0, math.ceil(self._expires_at - self._clock()))

    def active(self) -> bool:
        return self.remaining() > 0


def allowed_during_hold(path: str, body: bytes, generation_models: set[str]) -> bool:
    """Fail closed: llama-swap starts models from many routes (/v1/*, /v/*,
    /completion, /props, /comfyui/*, /upstream/*), so rather than list the
    ones to block, list the ones to let through."""
    if path in OPEN_PATHS:
        return True
    if path.startswith("/upstream/"):
        return any(path == f"/upstream/{m}" or path.startswith(f"/upstream/{m}/") for m in generation_models)
    try:
        return json.loads(body).get("model") in generation_models
    except (ValueError, AttributeError, TypeError):  # bad JSON, not an object, unhashable model
        return False


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    if handler.headers.get("Transfer-Encoding", "").lower() == "chunked":
        chunks = []
        while size := int(handler.rfile.readline().split(b";")[0], 16):
            chunks.append(handler.rfile.read(size))
            handler.rfile.readline()
        while handler.rfile.readline() not in (b"\r\n", b"\n", b""):
            pass  # trailers
        return b"".join(chunks)
    return handler.rfile.read(int(handler.headers.get("Content-Length", 0)))


def _path(handler: BaseHTTPRequestHandler) -> str:
    return handler.path.split("?", 1)[0]


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: object, headers: dict[str, str] = {}) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(status)
    for k, v in {**headers, "Content-Type": "application/json", "Content-Length": str(len(body))}.items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def make_servers(
    upstream: str,
    public_addr: tuple[str, int],
    admin_addr: tuple[str, int],
    hold: Hold,
    generation_models: set[str],
    upstream_timeout: float = UPSTREAM_TIMEOUT_SECONDS,
) -> tuple[ThreadingHTTPServer, ThreadingHTTPServer]:
    class Public(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _handle(self) -> None:
            body = _read_body(self)
            if hold.active() and not allowed_during_hold(_path(self), body, generation_models):
                error = {"message": "Window hold active: the LLM is unavailable", "type": "window_hold"}
                _send_json(self, 503, {"error": error}, {"Retry-After": str(max(1, hold.remaining()))})
                return
            if self.headers.get("Upgrade", "").lower() == "websocket":
                self._tunnel()
            else:
                self._proxy(body)

        def _tunnel(self) -> None:
            """WebSocket (ComfyUI's UI): replay the handshake upstream, then relay
            raw bytes both ways until either side closes."""
            try:
                upstream_sock = socket.create_connection(_addr(upstream), timeout=10)
            except OSError as e:
                self.send_error(502, f"llama-swap unreachable: {e}")
                return
            upstream_sock.settimeout(None)
            self.close_connection = True
            lines = [f"{self.command} {self.path} HTTP/1.1"]
            lines += [f"{k}: {v}" for k, v in self.headers.items() if k.lower() != "x-forwarded-for"]
            lines.append(f"X-Forwarded-For: {self.client_address[0]}")
            try:
                upstream_sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
                back = threading.Thread(target=_pipe, args=(upstream_sock, self.connection), daemon=True)
                back.start()
                _pipe(self.connection, upstream_sock)
                back.join()
            finally:
                upstream_sock.close()

        def _proxy(self, body: bytes) -> None:
            headers = {k: v for k, v in self.headers.items() if k.lower() not in NOT_FORWARDED}
            headers["X-Forwarded-For"] = self.client_address[0]
            conn = http.client.HTTPConnection(upstream, timeout=upstream_timeout)
            started = False
            try:
                conn.request(self.command, self.path, body=body or None, headers=headers)
                resp = conn.getresponse()
                self.send_response(resp.status, resp.reason)
                started = True
                for k, v in resp.getheaders():
                    if k.lower() not in NOT_FORWARDED:
                        self.send_header(k, v)
                length = resp.getheader("Content-Length")
                if length is not None:
                    self.send_header("Content-Length", length)
                    self.end_headers()
                    self.wfile.write(resp.read())
                    return
                # Unknown length (SSE streams): re-chunk as it arrives.
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                while chunk := resp.read1(65536):
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.write(b"0\r\n\r\n")
            except (OSError, http.client.HTTPException) as e:
                if started:
                    # Mid-response (upstream died, or the client went away):
                    # all we can do is cut it short. Dropping the upstream
                    # connection cancels the request in llama-swap.
                    self.close_connection = True
                else:
                    self.send_error(502, f"llama-swap unreachable: {e}")
            finally:
                conn.close()

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle

    class Admin(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _handle(self) -> None:
            _read_body(self)
            if _path(self) != "/hold":
                self.send_error(404)
                return
            if self.command in ("POST", "PUT"):
                hold.open()  # PUT renews, and re-opens a hold a gate restart dropped
            elif self.command == "DELETE":
                hold.release()
            _send_json(self, 200, {"active": hold.active(), "expires_in": hold.remaining()})

        do_GET = do_POST = do_PUT = do_DELETE = _handle

    return ThreadingHTTPServer(public_addr, Public), ThreadingHTTPServer(admin_addr, Admin)


def _addr(value: str) -> tuple[str, int]:
    host, port = value.rsplit(":", 1)
    return host, int(port)


def main() -> None:
    generation = {m for m in os.environ.get("GENERATION_MODELS", "").split(",") if m}
    public, admin = make_servers(
        upstream=os.environ.get("GATE_UPSTREAM", "127.0.0.1:8080"),
        public_addr=_addr(os.environ.get("GATE_LISTEN", "0.0.0.0:8000")),
        admin_addr=_addr(os.environ.get("GATE_ADMIN_LISTEN", "127.0.0.1:8001")),
        hold=Hold(ttl=float(os.environ.get("HOLD_TTL_SECONDS", "1800"))),
        generation_models=generation,
    )
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    print(f"gate: public {public.server_address}, admin {admin.server_address}, generation models {sorted(generation)}", flush=True)
    public.serve_forever()


if __name__ == "__main__":
    main()
