"""Two tiny local servers that exercise EVERY state of the Diff/Run results.

Run:  python examples/showcase/serve.py
Then diff `baseline ⇄ candidate` from the showcase project. Stop with Ctrl-C.

baseline :8091 and candidate :8092 differ deliberately:
- /quote      JSON drift ($.quote.taxRate on every plan), a tolerance-absorbed
              total on plan=basic, an x-api-version HEADER drift, and volatile
              x-request-id headers (silenced by the built-in rules)
- /checkout   identical — the clean cell
- /feed       a chunked-JSON stream whose third event drifts ($[2].price);
              every event carries a ts the profile ignores
- /legacy     candidate drops the connection — the transport-error cell
- /unstable   candidate answers 503 — the $status drift
- /status     identical HTML (the outline renderer; contains "operational")
- /logo       identical binary (the sha256/hex renderer)
- /history    identical — deselect it at Prepare to see the ⊘ roster
"""

import json
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer

_PLANS = {"basic": 240.0, "pro": 480.0, "scale": 960.0}


class _Handler(BaseHTTPRequestHandler):
    side = "baseline"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        route = {
            "/quote": self._quote,
            "/feed": self._feed,
            "/legacy": self._legacy,
            "/unstable": self._unstable,
            "/status": self._status_page,
            "/logo": self._logo,
            "/history": self._history,
        }.get(path)
        if route is None:
            self._json(404, {"error": "no such route"})
            return
        route(dict(pair.split("=", 1) for pair in query.split("&") if "=" in pair))

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        if self.path.partition("?")[0] == "/checkout":
            self._json(200, {"order": {"id": "ord-1", "state": "confirmed", "total": 240.0}})
            return
        self._json(404, {"error": "no such route"})

    # ── routes ───────────────────────────────────────────────────────────────
    def _quote(self, query: dict[str, str]) -> None:
        plan = query.get("plan", "basic")
        subtotal = _PLANS.get(plan, 240.0)
        candidate = self.side == "candidate"
        total = subtotal + (0.004 if candidate and plan == "basic" else 0.0)
        body = {
            "quote": {
                "plan": plan,
                "currency": "USD",
                "subtotal": subtotal,
                "taxRate": "0.25" if candidate else "0.20",
                "total": round(total, 3),
            },
            "meta": {"requestId": uuid.uuid4().hex, "generatedAt": time.time()},
        }
        self._json(200, body, extra=[("x-api-version", "2025-01" if candidate else "2024-08")])

    def _feed(self, query: dict[str, str]) -> None:
        prices = [98.4, 98.75, 104.25 if self.side == "candidate" else 99.1, 104.3, 104.55]
        lines = [
            json.dumps({"seq": n + 1, "symbol": "WIDGET-1", "price": price, "ts": time.time()})
            for n, price in enumerate(prices)
        ]
        payload = "\n".join(lines).encode()
        self._raw(200, payload, "application/x-ndjson")

    def _legacy(self, query: dict[str, str]) -> None:
        if self.side == "candidate":
            # Promise a body and hang up mid-send — an immediate transport error
            # (RemoteProtocolError), not a status and not a slow read timeout.
            self.connection.sendall(b"HTTP/1.1 200 OK\r\ncontent-length: 100\r\n\r\npartial")
            self.connection.shutdown(socket.SHUT_RDWR)  # force the FIN out now
            self.close_connection = True
            return
        self._json(200, {"quote": {"plan": "legacy", "subtotal": 180.0}})

    def _unstable(self, query: dict[str, str]) -> None:
        if self.side == "candidate":
            self._json(503, {"ok": False, "reason": "over capacity"})
            return
        self._json(200, {"ok": True})

    def _status_page(self, query: dict[str, str]) -> None:
        html = (
            "<html><head><title>Showcase status</title></head><body>"
            "<nav><h1>Service status</h1></nav>"
            "<main><p>All systems operational.</p>"
            "<table><tr><td>api</td><td>up</td></tr><tr><td>feed</td><td>up</td></tr></table>"
            "</main></body></html>"
        )
        self._raw(200, html.encode(), "text/html; charset=utf-8")

    def _logo(self, query: dict[str, str]) -> None:
        blob = b"\x89PNG\r\n\x1a\n" + bytes(range(64)) + b"\x00" * 32
        self._raw(200, blob, "image/png")

    def _history(self, query: dict[str, str]) -> None:
        self._json(200, {"quotes": [{"id": "q-1", "total": 240.0}]})

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _json(self, status: int, body: object, extra: list[tuple[str, str]] | None = None) -> None:
        self._raw(status, json.dumps(body).encode(), "application/json", extra)

    def _raw(
        self,
        status: int,
        payload: bytes,
        content_type: str,
        extra: list[tuple[str, str]] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(payload)))
        self.send_header("x-request-id", uuid.uuid4().hex)  # volatile — built-in ignore
        for name, value in extra or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # keep the terminal quiet


def main() -> None:
    """Serve baseline on :8091 and candidate on :8092 until Ctrl-C."""
    servers = []
    for side, port, version in (
        ("baseline", 8091, "showcase-a/1.0"),
        ("candidate", 8092, "showcase-b/1.1"),
    ):
        handler = type(f"_{side.title()}", (_Handler,), {"side": side, "server_version": version})
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        print(f"{side}  http://127.0.0.1:{port}")
    print("Ctrl-C stops both.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        for server in servers:
            server.shutdown()


if __name__ == "__main__":
    main()
