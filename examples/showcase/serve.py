"""Two deterministic local servers that exercise every Diff and Run result state.

Run ``python examples/showcase/serve.py``, then point the showcase project at
the pair from another terminal::

    comparo tui  --config examples/showcase
    comparo diff --config examples/showcase/comparo.yaml
    comparo run  --config examples/showcase/comparo.yaml
    comparo exec execution.release-gate --config examples/showcase/comparo.yaml

BASELINE listens on 127.0.0.1:8091 and CANDIDATE on 127.0.0.1:8092, from one
process. Every response is deterministic — no randomness, and no wall-clock
values except the fields that exist to demonstrate volatility (the automatic
``Date`` header and the ``x-request-id`` counter, both silenced by rules). The
sides differ only where a scenario needs them to:

- ``/health``      identical — the clean cell; schema + header assertions pass
- ``/quote``       price (number) and taxRate (string) drift, one bug x 3 plans
- ``/echo-config`` echoes the query — the drift is in what WE sent (outbound)
- ``/inventory``   ``count`` is 3 vs "3" — type drift
- ``/profile``     ``plan`` dropped, ``betaFlags`` added — shape drift
- ``/rounding``    240.0 vs 240.004 — tolerance ±0.01 ABSORBED (still same)
- ``/fees``        12.5 vs 14.0 — tolerance ±0.01 BROKEN
- ``/version``     ``x-quote-version`` response header drifts
- ``/flaky``       200 vs 503 with the same body — the $status drift
- ``/legacy``      candidate truncates mid-response — one-side transport error
- ``/dead``        both sides truncate — the both-sides error
- ``/events``      SSE: full envelope, one drifted event, one missing event
- ``/page``        HTML with a text change — the outline renderer's drift
- ``/logo``        identical PNG bytes
- ``/stamp``       differing PNG bytes — binary drift
- ``/slow-lane``   candidate sleeps 300 ms — the latency regression
- ``/slo``         both sides sleep 300 ms — the warn advisory breaks everywhere
- ``/contract``    ``renewsOn`` missing on both sides — schema assertion fails
- ``/whoami``      echoes the bearer token back — the redaction demo
- ``/parked``      identical — deselect it at Prepare for the not-run roster
- ``/checkout``    POST echo of a fixed order — assertion FAIL (999 vs 240.0)

Stop with Ctrl-C.
"""

import itertools
import json
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import parse_qsl
from urllib.parse import urlsplit

_BASELINE_PORT = 8091
_CANDIDATE_PORT = 8092

_PLAN_PRICES = {"basic": 240.0, "pro": 480.0, "scale": 960.0}

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_LOGO = _PNG_MAGIC + bytes(range(48)) + b"\x00" * 16
_STAMP_BASELINE = _PNG_MAGIC + b"IHDR" + bytes(range(32)) + b"\xaa" * 24
_STAMP_CANDIDATE = _PNG_MAGIC + b"IHDR" + bytes(range(32)) + b"\xbb" * 24

#: Per-call counter behind ``x-request-id`` and ``$.meta.nonce`` — deterministic
#: within a process yet different on every call, which is the point: it stands in
#: for the correlation ids and nonces real services emit, so the built-in
#: volatile-header rule and the user's ``$.meta.nonce`` ignore have work to do.
_serial = itertools.count(1)


def _sse_body(side: str) -> str:
    """The SSE feed for *side*: envelope fields, one drift, one missing event.

    The frames exercise the whole envelope — ``retry``, ``id``, named events, an
    unnamed event (a renderer shows the spec default *message*), and multi-line
    ``data``. The candidate drifts the third event's price and drops the trailing
    ``done`` event, so the sequence diff shows a per-event drift AND a length cut.
    """
    price = "118.0" if side == "candidate" else "100.25"
    frames = [
        'retry: 10000\nid: 1\nevent: connected\ndata: {"proto": "sse"}\n\n',
        'id: 2\nevent: tick\ndata: {"seq": 2, "price": 99.5}\n\n',
        f'id: 3\nevent: tick\ndata: {{"seq": 3, "price": {price}}}\n\n',
        "data: heartbeat\ndata: still-alive\n\n",
    ]
    if side == "baseline":
        frames.append('id: 5\nevent: done\ndata: {"events": 5}\n\n')
    return "".join(frames)


class _Handler(BaseHTTPRequestHandler):
    """One showcase side; the class attribute ``side`` picks its behavior."""

    side: ClassVar[str] = "baseline"
    protocol_version = "HTTP/1.1"
    # Identical on both sides on purpose: the Server header is compared (it is
    # not in the volatile built-ins), and it has no scenario of its own.
    server_version = "comparo-showcase/1"
    sys_version = ""

    @property
    def _candidate(self) -> bool:
        return self.side == "candidate"

    def do_GET(self) -> None:
        """Dispatch a GET to its scenario route."""
        parts = urlsplit(self.path)
        routes: dict[str, Callable[[dict[str, str]], None]] = {
            "/health": self._health,
            "/quote": self._quote,
            "/echo-config": self._echo_config,
            "/inventory": self._inventory,
            "/profile": self._profile,
            "/rounding": self._rounding,
            "/fees": self._fees,
            "/version": self._version,
            "/flaky": self._flaky,
            "/legacy": self._legacy,
            "/dead": self._dead,
            "/events": self._events,
            "/page": self._page,
            "/logo": self._logo,
            "/stamp": self._stamp,
            "/slow-lane": self._slow_lane,
            "/slo": self._slo,
            "/contract": self._contract,
            "/whoami": self._whoami,
            "/parked": self._parked,
        }
        route = routes.get(parts.path)
        if route is None:
            self._json(404, {"error": f"no route {parts.path}"})
            return
        route(dict(parse_qsl(parts.query)))

    def do_POST(self) -> None:
        """Dispatch a POST — only ``/checkout`` exists."""
        if urlsplit(self.path).path != "/checkout":
            self._json(404, {"error": "no such route"})
            return
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        try:
            posted = json.loads(raw) if raw else {}
        except ValueError:
            self._json(400, {"error": "invalid JSON"})
            return
        order = posted.get("order") if isinstance(posted, dict) else None
        # Echo the posted order verbatim; assert.order expects total == 999
        # against the 240.0 the request sends, so the FAIL is deterministic.
        self._json(200, {"order": order, "state": "confirmed"}, [("x-service-tier", "gold")])

    # ── GET routes, one per scenario ────────────────────────────────────────
    def _health(self, query: dict[str, str]) -> None:
        self._json(200, {"status": "ok", "region": "local"})

    def _quote(self, query: dict[str, str]) -> None:
        plan = query.get("plan", "basic")
        price = _PLAN_PRICES.get(plan, 240.0) + (6.0 if self._candidate else 0.0)
        body = {
            "quote": {
                "plan": plan,
                "currency": "USD",
                "price": round(price, 2),
                "taxRate": "0.25" if self._candidate else "0.20",
            },
            # Different on every call — the $.meta.nonce ignore rule silences it.
            "meta": {"nonce": f"n-{next(_serial):06d}"},
        }
        self._json(200, body)

    def _echo_config(self, query: dict[str, str]) -> None:
        # The sides answer identically; any drift here was SENT by the client
        # (the environments inject different TAX_RATE values into the query).
        self._json(200, {"received": dict(sorted(query.items()))})

    def _inventory(self, query: dict[str, str]) -> None:
        count: object = "3" if self._candidate else 3
        self._json(200, {"item": "widget", "count": count})

    def _profile(self, query: dict[str, str]) -> None:
        user: dict[str, object] = {"name": "Ada", "quota": 5}
        if self._candidate:
            user["betaFlags"] = ["beta-1"]  # the extra key
        else:
            user["plan"] = "pro"  # the key the candidate loses
        self._json(200, {"user": user})

    def _rounding(self, query: dict[str, str]) -> None:
        rounded = 240.004 if self._candidate else 240.0  # inside the ±0.01 band
        self._json(200, {"totals": {"rounded": rounded, "currency": "USD"}})

    def _fees(self, query: dict[str, str]) -> None:
        fee = 14.0 if self._candidate else 12.5  # far outside the ±0.01 band
        self._json(200, {"totals": {"fee": fee, "currency": "USD"}})

    def _version(self, query: dict[str, str]) -> None:
        version = "2025-01" if self._candidate else "2024-08"
        self._json(200, {"service": "quote-api"}, [("x-quote-version", version)])

    def _flaky(self, query: dict[str, str]) -> None:
        status = 503 if self._candidate else 200
        self._json(status, {"service": "flaky", "body": "identical on both sides"})

    def _legacy(self, query: dict[str, str]) -> None:
        if self._candidate:
            self._truncate()
            return
        self._json(200, {"quote": {"plan": "legacy", "subtotal": 180.0}})

    def _dead(self, query: dict[str, str]) -> None:
        self._truncate()

    def _events(self, query: dict[str, str]) -> None:
        self._raw(200, _sse_body(self.side).encode(), "text/event-stream")

    def _page(self, query: dict[str, str]) -> None:
        note = "Checkout is degraded." if self._candidate else "All systems operational."
        html = (
            "<html><head><title>Showcase order</title></head><body>"
            "<h1>Showcase order</h1>"
            f"<p>{note}</p>"
            "<table><tr><td>api</td><td>up</td></tr></table>"
            "</body></html>"
        )
        self._raw(200, html.encode(), "text/html; charset=utf-8")

    def _logo(self, query: dict[str, str]) -> None:
        self._raw(200, _LOGO, "image/png")

    def _stamp(self, query: dict[str, str]) -> None:
        self._raw(200, _STAMP_CANDIDATE if self._candidate else _STAMP_BASELINE, "image/png")

    def _slow_lane(self, query: dict[str, str]) -> None:
        if self._candidate:
            time.sleep(0.3)  # trips the request's `latency <= 150ms` warn rule
        self._json(200, {"lane": "express", "sloMs": 150})

    def _slo(self, query: dict[str, str]) -> None:
        time.sleep(0.3)  # slower than the 150 ms warn SLO on EVERY side
        self._json(200, {"slo": "150ms", "note": "deliberately slow everywhere"})

    def _contract(self, query: dict[str, str]) -> None:
        # schema.contract requires contract.renewsOn — absent here, on both sides.
        self._json(200, {"contract": {"id": "c-1"}})

    def _whoami(self, query: dict[str, str]) -> None:
        token = self.headers.get("authorization", "").removeprefix("Bearer ").strip()
        body = {"authenticated": bool(token), "tokenEcho": token}
        self._json(200, body, [("x-echoed-token", token)])

    def _parked(self, query: dict[str, str]) -> None:
        self._json(200, {"parked": True})

    # ── plumbing ────────────────────────────────────────────────────────────
    def _truncate(self) -> None:
        """Promise a body, send part of it, and hang up — an immediate transport error.

        The client sees a protocol violation (not a status, not a slow read
        timeout), so the error cell appears instantly and deterministically.
        """
        self.connection.sendall(b"HTTP/1.1 200 OK\r\ncontent-length: 100\r\n\r\npartial")
        self.connection.shutdown(socket.SHUT_RDWR)  # force the FIN out now
        self.close_connection = True

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
        # Volatile per call — silenced by the built-in x-request-id rule.
        self.send_header("x-request-id", f"req-{next(_serial):06d}")
        # Drifts per SIDE — silenced by the user's $headers.x-build-id rule.
        self.send_header("x-build-id", "bld-2025.01" if self._candidate else "bld-2024.08")
        for name, value in extra or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        """Keep the terminal quiet."""


def main() -> None:
    """Serve BASELINE on :8091 and CANDIDATE on :8092 until Ctrl-C."""
    servers: list[ThreadingHTTPServer] = []
    for side, port in (("baseline", _BASELINE_PORT), ("candidate", _CANDIDATE_PORT)):
        handler = type(f"_{side.title()}Handler", (_Handler,), {"side": side})
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        print(f"{side:9} http://127.0.0.1:{port}")
    print("Ctrl-C stops both.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        for server in servers:
            server.shutdown()


if __name__ == "__main__":
    main()
