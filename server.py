"""ServiceNow MCP server — incident management.

Exposes ServiceNow incident operations as MCP tools over the Table API,
authenticated with a local ServiceNow login (username/password basic auth).

Configure via environment variables (see .env.example):
    SERVICENOW_INSTANCE_URL   e.g. https://inmorphisservicespvtltddemo16.service-now.com
    SERVICENOW_USERNAME
    SERVICENOW_PASSWORD
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

try:
    # Load credentials from a .env file sitting next to this script, so the
    # MCP client config doesn't need secrets inlined.
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ModuleNotFoundError:
    pass

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

INSTANCE_URL = os.environ.get(
    "SERVICENOW_INSTANCE_URL",
    "https://inmorphisservicespvtltddemo16.service-now.com",
).rstrip("/")
USERNAME = os.environ.get("SERVICENOW_USERNAME")
PASSWORD = os.environ.get("SERVICENOW_PASSWORD")

TABLE = "incident"
TABLE_API = f"{INSTANCE_URL}/api/now/table/{TABLE}"

# Fields returned by default. Using display values keeps references (caller,
# assignment_group, state, ...) human-readable instead of raw sys_ids.
DEFAULT_FIELDS = (
    "number,sys_id,short_description,description,state,priority,urgency,"
    "impact,category,assignment_group,assigned_to,caller_id,opened_at,"
    "opened_by,sys_created_on,sys_updated_on,close_code,close_notes,"
    "work_notes,comments"
)

# Common state name -> ServiceNow numeric value, for convenience.
STATE_MAP = {
    "new": "1",
    "in progress": "2",
    "on hold": "3",
    "resolved": "6",
    "closed": "7",
    "canceled": "8",
    "cancelled": "8",
}

mcp = FastMCP("servicenow-incidents")


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #

def _auth() -> tuple[str, str]:
    if not USERNAME or not PASSWORD:
        raise RuntimeError(
            "Missing credentials: set SERVICENOW_USERNAME and "
            "SERVICENOW_PASSWORD environment variables."
        )
    return (USERNAME, PASSWORD)


def _client() -> httpx.Client:
    return httpx.Client(
        auth=_auth(),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30.0,
    )


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    detail = resp.text
    try:
        body = resp.json()
        detail = body.get("error", {}).get("message") or detail
    except Exception:
        pass
    raise RuntimeError(f"ServiceNow API error {resp.status_code}: {detail}")


def _normalize_state(value: str) -> str:
    """Accept either a numeric state or a friendly name like 'in progress'."""
    v = str(value).strip()
    return STATE_MAP.get(v.lower(), v)


def _get_records(params: dict[str, Any]) -> list[dict[str, Any]]:
    with _client() as client:
        resp = client.get(TABLE_API, params=params)
    _raise_for_status(resp)
    return resp.json().get("result", [])


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

@mcp.tool()
def create_incident(
    short_description: str,
    description: str = "",
    caller_id: str = "",
    category: str = "",
    priority: str = "",
    urgency: str = "",
    impact: str = "",
    assignment_group: str = "",
    assigned_to: str = "",
) -> dict[str, Any]:
    """Create a new ServiceNow incident.

    Args:
        short_description: One-line summary of the incident (required).
        description: Full description / details.
        caller_id: sys_id, user_name, or email of the caller.
        category: Incident category (e.g. 'inquiry', 'software', 'hardware').
        priority: 1 (Critical) .. 5 (Planning).
        urgency: 1 (High) .. 3 (Low).
        impact: 1 (High) .. 3 (Low).
        assignment_group: sys_id or name of the assignment group.
        assigned_to: sys_id, user_name, or email of the assignee.

    Returns:
        The created incident record (display values).
    """
    payload: dict[str, Any] = {"short_description": short_description}
    for key, val in {
        "description": description,
        "caller_id": caller_id,
        "category": category,
        "priority": priority,
        "urgency": urgency,
        "impact": impact,
        "assignment_group": assignment_group,
        "assigned_to": assigned_to,
    }.items():
        if val:
            payload[key] = val

    with _client() as client:
        resp = client.post(
            TABLE_API,
            params={"sysparm_display_value": "all", "sysparm_fields": DEFAULT_FIELDS},
            json=payload,
        )
    _raise_for_status(resp)
    return resp.json().get("result", {})


@mcp.tool()
def get_incident(identifier: str) -> dict[str, Any]:
    """Fetch a single incident by its number (e.g. 'INC0010001') or sys_id.

    Args:
        identifier: Incident number (INC...) or 32-char sys_id.

    Returns:
        The incident record, or an error message if not found.
    """
    ident = identifier.strip()
    if ident.upper().startswith("INC"):
        query = f"number={ident.upper()}"
    else:
        query = f"sys_id={ident}"

    records = _get_records(
        {
            "sysparm_query": query,
            "sysparm_display_value": "all",
            "sysparm_fields": DEFAULT_FIELDS,
            "sysparm_limit": 1,
        }
    )
    if not records:
        return {"error": f"No incident found for '{identifier}'."}
    return records[0]


@mcp.tool()
def search_incidents(
    query: str = "",
    limit: int = 20,
    order_by: str = "-sys_updated_on",
) -> dict[str, Any]:
    """Search incidents using a ServiceNow encoded query.

    Args:
        query: ServiceNow encoded query, e.g.
            "active=true^priority=1" or "assigned_toISEMPTY^state=2".
            Leave empty to list the most recent incidents.
        limit: Max number of records to return (default 20).
        order_by: Field to sort by. Prefix with '-' for descending
            (default '-sys_updated_on').

    Returns:
        Dict with 'count' and 'incidents'.
    """
    sysparm_query = query.strip()
    if order_by:
        field = order_by.lstrip("-")
        direction = "ORDERBYDESC" if order_by.startswith("-") else "ORDERBY"
        clause = f"{direction}{field}"
        sysparm_query = f"{sysparm_query}^{clause}" if sysparm_query else clause

    records = _get_records(
        {
            "sysparm_query": sysparm_query,
            "sysparm_display_value": "all",
            "sysparm_fields": DEFAULT_FIELDS,
            "sysparm_limit": max(1, min(limit, 100)),
        }
    )
    return {"count": len(records), "incidents": records}


@mcp.tool()
def update_incident(identifier: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Update fields on an existing incident.

    Args:
        identifier: Incident number (INC...) or sys_id.
        fields: Map of field name -> new value. The 'state' value may be a
            name ('in progress', 'on hold', ...) or a numeric code.

    Returns:
        The updated incident record.
    """
    sys_id = _resolve_sys_id(identifier)
    if sys_id is None:
        return {"error": f"No incident found for '{identifier}'."}

    payload = dict(fields)
    if "state" in payload:
        payload["state"] = _normalize_state(payload["state"])

    with _client() as client:
        resp = client.patch(
            f"{TABLE_API}/{sys_id}",
            params={"sysparm_display_value": "all", "sysparm_fields": DEFAULT_FIELDS},
            json=payload,
        )
    _raise_for_status(resp)
    return resp.json().get("result", {})


@mcp.tool()
def add_comment(
    identifier: str, text: str, work_note: bool = False
) -> dict[str, Any]:
    """Add a customer-visible comment (or internal work note) to an incident.

    Args:
        identifier: Incident number (INC...) or sys_id.
        text: The comment / note body.
        work_note: If True, add an internal work note instead of a public
            comment (default False).

    Returns:
        The updated incident record.
    """
    field = "work_notes" if work_note else "comments"
    return update_incident(identifier, {field: text})


@mcp.tool()
def resolve_incident(
    identifier: str,
    close_notes: str,
    close_code: str = "Solved (Permanently)",
) -> dict[str, Any]:
    """Resolve an incident (sets state to Resolved with a resolution note).

    Args:
        identifier: Incident number (INC...) or sys_id.
        close_notes: Resolution / closure notes (required).
        close_code: Resolution code (default 'Solved (Permanently)').

    Returns:
        The updated incident record.
    """
    return update_incident(
        identifier,
        {
            "state": STATE_MAP["resolved"],
            "close_code": close_code,
            "close_notes": close_notes,
        },
    )


# --------------------------------------------------------------------------- #
# Internal
# --------------------------------------------------------------------------- #

def _resolve_sys_id(identifier: str) -> str | None:
    ident = identifier.strip()
    if not ident.upper().startswith("INC") and len(ident) == 32:
        return ident
    records = _get_records(
        {
            "sysparm_query": f"number={ident.upper()}",
            "sysparm_fields": "sys_id",
            "sysparm_limit": 1,
        }
    )
    if records:
        return records[0]["sys_id"]
    return None


if __name__ == "__main__":
    mcp.run()
