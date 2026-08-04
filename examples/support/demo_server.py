#!/usr/bin/env python3
"""Deterministic loopback HTTP service shared by Comparo example projects."""

from __future__ import annotations

import argparse
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import Final
from typing import override
from urllib.parse import parse_qs
from urllib.parse import urlsplit

_LOGGER: Final = logging.getLogger("comparo.examples.demo-server")
_ENVIRONMENTS: Final = frozenset({"baseline", "candidate", "development"})
_MAXIMUM_REQUEST_BYTES: Final = 1_000_000


class DemoRequestHandler(BaseHTTPRequestHandler):
    """Serve stable JSON fixtures under an Environment-prefixed path."""

    server_version = "ComparoExample/1"
    sys_version = ""

    def do_GET(self) -> None:
        """Respond to one supported read-only demo endpoint."""
        parsed = urlsplit(self.path)
        segments = tuple(segment for segment in parsed.path.split("/") if segment)
        if len(segments) != 2 or segments[0] not in _ENVIRONMENTS:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "unknown demo route", "expected": "/{environment}/{endpoint}"},
            )
            return
        environment, endpoint = segments
        if endpoint == "events":
            self._send_sse(environment)
            return
        query = parse_qs(parsed.query, keep_blank_values=True)
        status, payload = _response(environment, endpoint, query, self.headers)
        self._send_json(status, payload)

    def do_POST(self) -> None:
        """Echo bounded request-body metadata for body-lowering examples."""
        parsed = urlsplit(self.path)
        segments = tuple(segment for segment in parsed.path.split("/") if segment)
        if len(segments) != 2 or segments[0] not in _ENVIRONMENTS or segments[1] != "echo":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown demo mutation route"})
            return
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
            return
        if not 0 <= length <= _MAXIMUM_REQUEST_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body too large"})
            return
        body = self.rfile.read(length)
        media_type = self.headers.get("Content-Type", "application/octet-stream")
        payload: object | None = None
        if media_type.startswith("application/json"):
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON body"})
                return
        self._send_json(
            HTTPStatus.OK,
            {
                "environment": segments[0],
                "method": "POST",
                "contentType": media_type,
                "bodyBytes": len(body),
                "json": payload,
            },
        )

    @override
    def log_message(self, message: str, *args: object) -> None:
        """Route request logs through the standard logging boundary."""
        _LOGGER.info("%s - %s", self.client_address[0], message % args)

    def _send_json(self, status: HTTPStatus, payload: object) -> None:
        """Write one deterministic bounded JSON response."""
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, environment: str) -> None:
        """Write deterministic JSON-bearing SSE with Environment-specific chunking."""
        tokens = {
            "baseline": ("hello", " world"),
            "candidate": ("hel", "lo", " world"),
            "development": ("hello world",),
        }[environment]
        records = b"".join(
            (
                f"id: {index}\nevent: delta\ndata: "
                + json.dumps({"index": index, "text": token}, separators=(",", ":"))
                + "\n\n"
            ).encode()
            for index, token in enumerate(tokens)
        )
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(records)))
        self.end_headers()
        self.wfile.write(records)


def _response(
    environment: str,
    endpoint: str,
    query: dict[str, list[str]],
    headers: object,
) -> tuple[HTTPStatus, object]:
    """Return the deliberate behavior for one demo request."""
    if endpoint == "health":
        return HTTPStatus.OK, {
            "service": "comparo-demo",
            "status": "ok",
            "contractVersion": 1,
        }
    if endpoint == "release":
        versions = {
            "baseline": ("2.4.0", "stable"),
            "candidate": ("2.5.0-rc.1", "release-candidate"),
            "development": ("2.6.0-dev", "development"),
        }
        version, channel = versions[environment]
        return HTTPStatus.OK, {
            "service": "comparo-demo",
            "version": version,
            "channel": channel,
            "contractVersion": 1,
        }
    if endpoint == "whoami":
        request_headers = headers if hasattr(headers, "get") else {}
        return HTTPStatus.OK, {
            "environment": _query_value(query, "environment", "missing"),
            "label": request_headers.get("X-Demo-Label", "missing"),
        }
    if endpoint == "degraded":
        if environment == "candidate":
            return HTTPStatus.SERVICE_UNAVAILABLE, {
                "status": "degraded",
                "dependency": "inventory",
            }
        return HTTPStatus.OK, {"status": "ok", "dependency": "inventory"}
    if endpoint == "catalog":
        region = _query_value(query, "region", "eu")
        plan = _query_value(query, "plan", "free")
        locale = _query_value(query, "locale", "en")
        stock = 8 if environment == "candidate" and region == "eu" and plan == "pro" else 10
        return HTTPStatus.OK, {
            "contractVersion": 1,
            "query": {"locale": locale, "plan": plan, "region": region},
            "items": [
                {"name": _localized_name(locale), "sku": "starter", "stock": stock},
                {"name": "Team", "sku": "team", "stock": 4},
            ],
        }
    return HTTPStatus.NOT_FOUND, {"error": "unknown demo endpoint", "endpoint": endpoint}


def _query_value(query: dict[str, list[str]], name: str, default: str) -> str:
    """Select the first query occurrence or a stable default."""
    values = query.get(name)
    return default if not values else values[0]


def _localized_name(locale: str) -> str:
    """Return a stable localized fixture name."""
    return {"en": "Starter", "fr": "Débutant"}.get(locale, "Starter")


def _port(value: str) -> int:
    """Parse a non-privileged TCP port for the demo server."""
    port = int(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def _arguments() -> argparse.Namespace:
    """Parse the deliberately small server interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Loopback address to bind.")
    parser.add_argument("--port", type=_port, default=18080, help="TCP port to bind.")
    return parser.parse_args()


def main() -> None:
    """Run the demo service until interrupted."""
    arguments = _arguments()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with ThreadingHTTPServer((arguments.host, arguments.port), DemoRequestHandler) as server:
        host, port = server.server_address[:2]
        _LOGGER.info("Comparo demo server listening on http://%s:%s", host, port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            _LOGGER.info("Comparo demo server stopped")


if __name__ == "__main__":
    main()
