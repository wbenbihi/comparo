"""Render a resolved request as a runnable ``curl`` command.

Whether the credentials are real or masked is decided upstream by the resolver's
sink — this module only renders whatever the :class:`ResolvedRequest` already
holds, so the same code serves both the masked preview and the real copy.
"""

import json
import shlex
from urllib.parse import urlencode

from comparo.core.resolve import ResolvedRequest


def to_curl(resolved: ResolvedRequest) -> str:
    """Render *resolved* as a multi-line ``curl`` command.

    Args:
        resolved: The request to render; secrets are already masked or real
            depending on the sink it was resolved with.

    Returns:
        A ``curl`` invocation, one flag per line for readability.
    """
    url = resolved.url
    if resolved.query:
        query = urlencode({key: str(value) for key, value in resolved.query.items()})
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    lines = [f"curl -X {resolved.method} {shlex.quote(url)}"]
    has_content_type = any(key.lower() == "content-type" for key, _ in resolved.headers)
    for key, value in resolved.headers:
        lines.append(f"-H {shlex.quote(f'{key}: {value}')}")
    if resolved.body is not None:
        if not has_content_type:
            lines.append(f"-H {shlex.quote('content-type: application/json')}")
        lines.append(f"--data {shlex.quote(json.dumps(resolved.body, ensure_ascii=False))}")
    return " \\\n  ".join(lines)
