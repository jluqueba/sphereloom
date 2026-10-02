"""Transport selection and its guard rails.

stdio is the default because it opens no socket at all: the client starts the server as a
child process and nothing else on the machine, or the network, can reach it.

The HTTP transport exists for clients that need it, but it never starts unauthenticated,
never binds beyond loopback without an explicit acknowledgement, and always enables DNS
rebinding protection. Those invariants are checked in configuration, so an unsafe
combination cannot reach a bound socket.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from sphereloom.config import Settings, Transport
from sphereloom.logging import get_logger
from sphereloom.security.http_auth import BearerTokenMiddleware
from sphereloom.server.context import AppContext

logger = get_logger("transport")

HTTP_PATH = "/mcp"


def run(server: MCPServer[AppContext], settings: Settings) -> None:
    """Serve the MCP server over the configured transport. Blocks until shutdown."""
    if settings.transport is Transport.STDIO:
        logger.info("serving over stdio")
        server.run("stdio")
        return

    _run_http(server, settings)


def _run_http(server: MCPServer[AppContext], settings: Settings) -> None:
    # Imported lazily: a stdio deployment should not pay for the web stack.
    import uvicorn

    if settings.http_token is None:  # pragma: no cover - guaranteed by Settings validation
        message = "The HTTP transport requires SPHERELOOM_HTTP_TOKEN."
        raise ValueError(message)

    if not settings.is_loopback_host:
        logger.warning(
            "HTTP transport is bound beyond loopback; camera control is reachable from "
            "your network and is protected only by the bearer token",
            extra={"context": {"port": settings.http_port}},
        )

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{settings.http_host}:{settings.http_port}"],
        allowed_origins=[],
    )
    app = server.streamable_http_app(
        streamable_http_path=HTTP_PATH,
        transport_security=security,
        host=settings.http_host,
    )
    guarded = BearerTokenMiddleware(app, token=settings.http_token.get_secret_value())

    logger.info(
        "serving over authenticated http",
        extra={"context": {"port": settings.http_port, "path": HTTP_PATH}},
    )
    uvicorn.run(
        guarded,
        host=settings.http_host,
        port=settings.http_port,
        log_config=None,
    )
