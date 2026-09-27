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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

# Never start a model, so they stay reachable during a hold.
OPEN_PATHS = {"/v1/models", "/models", "/health", "/metrics", "/running"}
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
}
# Longer than llama-swap's healthCheckTimeout (3600s): a cold start blocks the
# request, it doesn't fail it.
UPSTREAM_TIMEOUT_SECONDS = 3900


class Hold:
    """The Window hold: an in-memory lease that lapses on its own after `ttl`
    seconds unless renewed. A gate restart drops it; the next heartbeat
    re-establishes it."""

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
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
    except (ValueError, AttributeError):
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


def make_servers(
    upstream: str,
    public_addr: tuple[str, int],
    admin_addr: tuple[str, int],
    hold: Hold,
    generation_models: set[str],
) -> tuple[ThreadingHTTPServer, ThreadingHTTPServer]:
    class Public(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _handle(self) -> None:
            body = _read_body(self)
            path = self.path.split("?", 1)[0]
            if hold.active() and not allowed_during_hold(path, body, generation_models):
                msg = b'{"error":{"message":"Generation window in progress; the LLM is unavailable","type":"window_hold"}}'
                self.send_response(503)
                self.send_header("Retry-After", str(max(1, hold.remaining())))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            self._proxy(body)

        def _proxy(self, body: bytes) -> None:
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
            headers["X-Forwarded-For"] = self.client_address[0]
            conn = http.client.HTTPConnection(upstream, timeout=UPSTREAM_TIMEOUT_SECONDS)
            try:
                conn.request(self.command, self.path, body=body or None, headers=headers)
                resp = conn.getresponse()
                self.send_response(resp.status, resp.reason)
                for k, v in resp.getheaders():
                    if k.lower() not in HOP_BY_HOP:
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
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True  # client went away; dropping upstream cancels it
            except OSError as e:
                self.send_error(502, f"llama-swap unreachable: {e}")
            finally:
                conn.close()

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle

    class Admin(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _handle(self) -> None:
            _read_body(self)
            if self.path.split("?", 1)[0] != "/hold":
                self.send_error(404)
                return
            if self.command in ("POST", "PUT"):
                hold.acquire()  # PUT also re-opens a hold a gate restart dropped
            elif self.command == "DELETE":
                hold.release()
            payload = json.dumps({"active": hold.active(), "expires_in": hold.remaining()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

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
