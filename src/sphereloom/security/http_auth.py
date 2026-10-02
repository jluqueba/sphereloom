"""Bearer token authentication for the opt-in HTTP transport.

The camera's own API has no authentication at all: anything that can reach the camera's
access point can command it. SphereLoom cannot fix that, but it must not make it worse by
opening a second, unauthenticated door onto the operator's machine.

Comparison is constant-time so a token cannot be recovered by timing rejections.
"""

from __future__ import annotations

import hmac
import json

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_UNAUTHORIZED_BODY = json.dumps(
    {
        "error": {
            "code": "permission_denied",
            "message": (
                "Missing or invalid bearer token. The SphereLoom HTTP transport requires "
                "the token configured in SPHERELOOM_HTTP_TOKEN."
            ),
            "retryable": False,
        }
    }
).encode()


class BearerTokenMiddleware:
    """Pure ASGI middleware that rejects requests without the configured bearer token."""

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        if self._is_authorised(Headers(scope=scope)):
            await self._app(scope, receive, send)
            return

        await self._reject(send)

    def _is_authorised(self, headers: Headers) -> bool:
        scheme, _, presented = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(presented.strip(), self._token)

    async def _reject(self, send: Send) -> None:
        start: Message = {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_UNAUTHORIZED_BODY)).encode()),
                (b"www-authenticate", b"Bearer"),
            ],
        }
        await send(start)
        await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})
