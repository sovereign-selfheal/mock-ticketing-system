"""
Ticketing System MCP Server.

A FastMCP server that exposes ServiceNow-style incident management tools
against the in-cluster ticketing-system REST API.  Designed to be deployed
as a standalone service on OpenShift AI and registered in the MCP catalog.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import requests
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TICKETING_API_URL = os.getenv(
    "TICKETING_API_URL",
    "http://ticketing-system.troubleshooting-agents-demo.svc:8080",
)

mcp = FastMCP(
    "ticketing-mcp",
    host="0.0.0.0",
    port=int(os.getenv("MCP_PORT", "8080")),
)


def _api(method: str, path: str, body: dict | None = None, params: dict | None = None) -> str:
    url = f"{TICKETING_API_URL}{path}"
    try:
        resp = requests.request(method, url, json=body, params=params, timeout=10)
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)
    except requests.RequestException as exc:
        return f"Ticketing API error: {exc}"


@mcp.tool()
def create_incident(
    short_description: str,
    description: str = "",
    impact: int = 3,
    urgency: int = 3,
    category: str = "Application",
    subcategory: str = "",
    assignment_group: str = "",
    assigned_to: str = "",
    caller_id: str = "ocp-troubleshooter",
) -> str:
    """
    Create a new incident in the ticketing system.

    Returns the created incident with its auto-generated INC number,
    calculated priority, and timestamps.

    Args:
        short_description: Brief one-line summary of the issue.
        description:       Full detailed description or diagnosis report.
        impact:            1 (High), 2 (Medium), 3 (Low). Affects priority calculation.
        urgency:           1 (High), 2 (Medium), 3 (Low). Affects priority calculation.
        category:          Incident category — Application, Infrastructure, Network, or Database.
        subcategory:       Optional subcategory for further classification.
        assignment_group:  Team responsible for resolving the incident.
        assigned_to:       Individual assigned to the incident.
        caller_id:         Who reported the incident (default: ocp-troubleshooter).
    """
    return _api("POST", "/api/incidents", body={
        "short_description": short_description,
        "description": description,
        "impact": impact,
        "urgency": urgency,
        "category": category,
        "subcategory": subcategory,
        "assignment_group": assignment_group,
        "assigned_to": assigned_to,
        "caller_id": caller_id,
    })


@mcp.tool()
def list_incidents(
    state: Optional[str] = None,
    category: Optional[str] = None,
    priority: Optional[int] = None,
    limit: int = 20,
) -> str:
    """
    List incidents from the ticketing system with optional filters.

    Args:
        state:    Filter by state — New, In Progress, On Hold, Resolved, or Closed.
        category: Filter by category — Application, Infrastructure, Network, or Database.
        priority: Filter by priority level (1=Critical … 5=Planning).
        limit:    Maximum number of incidents to return (default: 20).
    """
    params: dict = {"limit": limit}
    if state:
        params["state"] = state
    if category:
        params["category"] = category
    if priority:
        params["priority"] = priority
    return _api("GET", "/api/incidents", params=params)


@mcp.tool()
def get_incident(number: str) -> str:
    """
    Get full details of a single incident, including all work notes.

    Args:
        number: The incident number (e.g. INC0000001).
    """
    return _api("GET", f"/api/incidents/{number}")


@mcp.tool()
def update_incident(
    number: str,
    state: Optional[str] = None,
    impact: Optional[int] = None,
    urgency: Optional[int] = None,
    short_description: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
    assignment_group: Optional[str] = None,
    assigned_to: Optional[str] = None,
    close_notes: Optional[str] = None,
) -> str:
    """
    Update fields on an existing incident.

    Only the fields you provide will be changed; others remain untouched.
    Setting state to 'Resolved' or 'Closed' automatically records resolved_at.

    Args:
        number:            The incident number to update (e.g. INC0000001).
        state:             New state — New, In Progress, On Hold, Resolved, or Closed.
        impact:            Updated impact: 1 (High), 2 (Medium), 3 (Low).
        urgency:           Updated urgency: 1 (High), 2 (Medium), 3 (Low).
        short_description: Updated summary.
        description:       Updated full description.
        category:          Updated category.
        assignment_group:  Updated assignment group.
        assigned_to:       Updated assignee.
        close_notes:       Resolution notes (typically set when closing).
    """
    body = {}
    for field in (
        "state", "impact", "urgency", "short_description",
        "description", "category", "assignment_group",
        "assigned_to", "close_notes",
    ):
        value = locals()[field]
        if value is not None:
            body[field] = value
    return _api("PATCH", f"/api/incidents/{number}", body=body)


@mcp.tool()
def add_work_note(number: str, note: str) -> str:
    """
    Append a work note to an existing incident.

    Work notes are timestamped comments that document investigation
    progress, findings, or actions taken.

    Args:
        number: The incident number (e.g. INC0000001).
        note:   The work note text to add.
    """
    return _api("POST", f"/api/incidents/{number}/notes", body={"note": note})


if __name__ == "__main__":
    port = int(os.getenv("MCP_PORT", "8080"))
    logger.info("Starting Ticketing MCP server on port %d", port)
    mcp.run(transport="streamable-http")
