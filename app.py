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

from mcp.server.transport_security import TransportSecuritySettings

from server import mcp

logger = logging.getLogger("servicenow-mcp.http")

BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN")
MCP_PATH = "/mcp"


def _extract_token(request: Request) -> str | None:
    """Pull the token from any of the common places a client might put it.

    Accepts, in order:
      * Authorization: Bearer <token>   (standard)
      * Authorization: <token>          (raw, no scheme — some UIs do this)
      * Authorization: Bearer Bearer <token>  (UI double-prefixes)
      * X-API-Key: <token>
      * ?token=<token> or ?api_key=<token>  (query string)
    """
    header = request.headers.get("authorization", "").strip()
    if header:
        # Strip any number of leading "bearer " prefixes, case-insensitively.
        while header.lower().startswith("bearer "):
            header = header[len("bearer ") :].strip()
        if header:
            return header

    xkey = request.headers.get("x-api-key", "").strip()
    if xkey:
        return xkey

    q = request.query_params.get("token") or request.query_params.get("api_key")
    if q:
        return q.strip()

    return None


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Guard MCP requests with a shared token (multiple header formats OK)."""

    async def dispatch(self, request: Request, call_next):
        # Health checks and anything outside the MCP path are unprotected.
        if not request.url.path.startswith(MCP_PATH):
            return await call_next(request)

        if not BEARER_TOKEN:
            return await call_next(request)

        if _extract_token(request) != BEARER_TOKEN:
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

# DNS-rebinding protection only trusts localhost by default, which 421s behind
# a hosting proxy. Trust the public hostname instead. On Render,
# RENDER_EXTERNAL_HOSTNAME is provided automatically; MCP_ALLOWED_HOSTS can
# override (comma-separated). If neither is set, fall back to disabling the
# check (the bearer token still guards the endpoint).
_allowed_hosts: list[str] = []
if os.environ.get("MCP_ALLOWED_HOSTS"):
    _allowed_hosts = [
        h.strip() for h in os.environ["MCP_ALLOWED_HOSTS"].split(",") if h.strip()
    ]
elif os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    _allowed_hosts = [os.environ["RENDER_EXTERNAL_HOSTNAME"]]

if _allowed_hosts:
    _allowed_hosts += ["localhost", "127.0.0.1", "localhost:8000", "127.0.0.1:8000"]
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
        allowed_origins=["*"],
    )
else:
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )

# Build the Streamable HTTP app (this carries the MCP session-manager
# lifespan), then attach our health route and bearer-auth middleware directly.
app = mcp.streamable_http_app()
app.router.routes.insert(0, Route("/healthz", healthz))
app.add_middleware(BearerAuthMiddleware)

if not BEARER_TOKEN:
    logger.warning(
        "MCP_BEARER_TOKEN is not set — the /mcp endpoint is UNAUTHENTICATED."
    )
