"""Hosted (HTTP) entrypoint for the ServiceNow incident MCP server.

Serves the same tools defined in ``server.py`` over the MCP Streamable HTTP
transport, protected by a bearer token. Run with::

    uvicorn app:app --host 0.0.0.0 --port 8000

The MCP endpoint is exposed at ``/mcp``. A health check lives at ``/healthz``.

Environment variables:
    SERVICENOW_INSTANCE_URL, SERVICENOW_USERNAME, SERVICENOW_PASSWORD
        ServiceNow connection (see server.py / .env.example).
    MCP_BEARER_TOKEN
        Shared secret. If set, every request to /mcp must include
        ``Authorization: Bearer <token>``. If unset, the endpoint is OPEN
        (a warning is logged) — only do that for throwaway testing.
"""

from __future__ import annotations

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from server import mcp

logger = logging.getLogger("servicenow-mcp.http")

BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN")
MCP_PATH = "/mcp"


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require ``Authorization: Bearer <token>`` on MCP requests."""

    async def dispatch(self, request: Request, call_next):
        # Health checks and anything outside the MCP path are unprotected.
        if not request.url.path.startswith(MCP_PATH):
            return await call_next(request)

        if not BEARER_TOKEN:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or token != BEARER_TOKEN:
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


async def healthz(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


# Run statelessly so the server behaves well behind a hosting proxy / single
# replica without sticky sessions.
mcp.settings.stateless_http = True

# Build the Streamable HTTP app (this carries the MCP session-manager
# lifespan), then attach our health route and bearer-auth middleware directly.
app = mcp.streamable_http_app()
app.router.routes.insert(0, Route("/healthz", healthz))
app.add_middleware(BearerAuthMiddleware)

if not BEARER_TOKEN:
    logger.warning(
        "MCP_BEARER_TOKEN is not set — the /mcp endpoint is UNAUTHENTICATED."
    )
