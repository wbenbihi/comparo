#!/usr/bin/env python3
r"""Two tiny local Server-Sent Events servers for the comparo streaming example.

comparo has no bundled SSE endpoint to point at (httpbin/postman-echo only do
chunked JSON, not ``text/event-stream``), and public SSE endpoints are flaky and
usually never close. So this script stands up two *finite* SSE feeds on localhost
that emit a fixed sequence of events and then close the connection — reproducible,
offline, and safe to read to completion.

Run it, then in another terminal:

    comparo diff --config examples/sse-project/comparo.yaml \\
        --baseline stable --candidate canary

The two feeds are identical except for one event, so the event-sequence diff
reports exactly which event drifted (``$[2]`` here).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer

STABLE: list[dict[str, object]] = [
    {"seq": 1, "type": "connected"},
    {"seq": 2, "type": "tick", "value": "alpha"},
    {"seq": 3, "type": "tick", "value": "bravo"},
    {"seq": 4, "type": "done"},
]
# The canary feed drifts on exactly one event (index 2) so the diff has something
# to catch; everything else matches the stable feed.
CANARY: list[dict[str, object]] = [dict(event) for event in STABLE]
CANARY[2] = {"seq": 3, "type": "tick", "value": "BRAVO-DRIFTED"}


def _handler(events: list[dict[str, object]]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for event in events:
                # A full SSE frame: an id, an event name, and a data payload. comparo
                # captures all three (plus retry and multi-line data) per the spec.
                frame = f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n"
                self.wfile.write(frame.encode())
                self.wfile.flush()
            # A finite stream: returning here closes the connection, so comparo
            # reads it to completion with no idle timeout needed.

        def log_message(self, *_: object) -> None:
            pass  # keep the console quiet

    return Handler


def serve(port: int, events: list[dict[str, object]], label: str) -> None:
    """Serve *events* as a finite SSE feed on ``127.0.0.1:port`` forever."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(events))
    print(f"  {label:7} http://127.0.0.1:{port}  ({len(events)} SSE events, then closes)")
    server.serve_forever()


if __name__ == "__main__":
    print("comparo SSE example — two local event streams (Ctrl-C to stop):")
    threading.Thread(target=serve, args=(8421, CANARY, "canary"), daemon=True).start()
    serve(8420, STABLE, "stable")
