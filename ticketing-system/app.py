"""
ServiceNow-style Incident Management API.

A lightweight simulator that exposes REST endpoints compatible with
ServiceNow's Table API conventions for the ``incident`` table.
"""

import os
import sqlite3
import textwrap
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import markdown as md
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

DB_PATH = os.getenv("TICKETING_DB_PATH", "/tmp/ticketing/incidents.db")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _ensure_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _connect() as conn:
        conn.execute(textwrap.dedent("""\
            CREATE TABLE IF NOT EXISTS incidents (
                number         TEXT PRIMARY KEY,
                state          TEXT NOT NULL DEFAULT 'New',
                impact         INTEGER NOT NULL DEFAULT 3,
                urgency        INTEGER NOT NULL DEFAULT 3,
                priority       INTEGER NOT NULL DEFAULT 5,
                short_description TEXT NOT NULL,
                description    TEXT NOT NULL DEFAULT '',
                category       TEXT NOT NULL DEFAULT 'Application',
                subcategory    TEXT NOT NULL DEFAULT '',
                assignment_group TEXT NOT NULL DEFAULT '',
                assigned_to    TEXT NOT NULL DEFAULT '',
                caller_id      TEXT NOT NULL DEFAULT 'ocp-troubleshooter',
                opened_at      TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                resolved_at    TEXT,
                close_notes    TEXT NOT NULL DEFAULT ''
            )
        """))
        conn.execute(textwrap.dedent("""\
            CREATE TABLE IF NOT EXISTS work_notes (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                incident  TEXT NOT NULL REFERENCES incidents(number),
                note      TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """))


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _next_number() -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT number FROM incidents ORDER BY number DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return "INC0000001"
    seq = int(row["number"][3:]) + 1
    return f"INC{seq:07d}"


def _priority(impact: int, urgency: int) -> int:
    matrix = {
        (1, 1): 1, (1, 2): 2, (1, 3): 3,
        (2, 1): 2, (2, 2): 3, (2, 3): 4,
        (3, 1): 3, (3, 2): 4, (3, 3): 5,
    }
    return matrix.get((impact, urgency), 5)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class IncidentCreate(BaseModel):
    short_description: str
    description: str = ""
    impact: int = Field(default=3, ge=1, le=3)
    urgency: int = Field(default=3, ge=1, le=3)
    category: str = "Application"
    subcategory: str = ""
    assignment_group: str = ""
    assigned_to: str = ""
    caller_id: str = "ocp-troubleshooter"


class IncidentUpdate(BaseModel):
    state: Optional[str] = None
    impact: Optional[int] = Field(default=None, ge=1, le=3)
    urgency: Optional[int] = Field(default=None, ge=1, le=3)
    short_description: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    assignment_group: Optional[str] = None
    assigned_to: Optional[str] = None
    close_notes: Optional[str] = None


class WorkNoteCreate(BaseModel):
    note: str


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ServiceNow Incident Simulator",
    version="1.0.0",
    description="A lightweight ServiceNow Table API simulator for the incident table.",
)

VALID_STATES = {"New", "In Progress", "On Hold", "Resolved", "Closed"}


@app.on_event("startup")
def startup():
    _ensure_db()


# ── Create ─────────────────────────────────────────────────────────────────

@app.post("/api/incidents", status_code=201)
def create_incident(body: IncidentCreate):
    now = _now()
    number = _next_number()
    priority = _priority(body.impact, body.urgency)

    with _connect() as conn:
        conn.execute(
            textwrap.dedent("""\
                INSERT INTO incidents
                    (number, impact, urgency, priority,
                     short_description, description, category, subcategory,
                     assignment_group, assigned_to, caller_id,
                     opened_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """),
            (
                number, body.impact, body.urgency, priority,
                body.short_description, body.description,
                body.category, body.subcategory,
                body.assignment_group, body.assigned_to, body.caller_id,
                now, now,
            ),
        )
    return _get_incident(number)


# ── List ───────────────────────────────────────────────────────────────────

@app.get("/api/incidents")
def list_incidents(
    state: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    priority: Optional[int] = Query(default=None, ge=1, le=5),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    clauses, params = [], []
    if state:
        clauses.append("state = ?")
        params.append(state)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if priority:
        clauses.append("priority = ?")
        params.append(priority)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT * FROM incidents {where} ORDER BY opened_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM incidents {where}", params[:-2]
        ).fetchone()[0]

    return {"result": [dict(r) for r in rows], "total": total}


# ── Get one ────────────────────────────────────────────────────────────────

@app.get("/api/incidents/{number}")
def get_incident(number: str):
    return _get_incident(number)


def _get_incident(number: str) -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM incidents WHERE number = ?", (number,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"Incident {number} not found")
        notes = conn.execute(
            "SELECT * FROM work_notes WHERE incident = ? ORDER BY created_at",
            (number,),
        ).fetchall()
    incident = dict(row)
    incident["work_notes"] = [dict(n) for n in notes]
    return incident


# ── Update ─────────────────────────────────────────────────────────────────

@app.patch("/api/incidents/{number}")
def update_incident(number: str, body: IncidentUpdate):
    _get_incident(number)  # 404 if missing

    sets, params = [], []
    for field, value in body.model_dump(exclude_none=True).items():
        sets.append(f"{field} = ?")
        params.append(value)

    if not sets:
        return _get_incident(number)

    with _connect() as conn:
        row = conn.execute(
            "SELECT impact, urgency FROM incidents WHERE number = ?", (number,)
        ).fetchone()
        new_impact = body.impact if body.impact is not None else row["impact"]
        new_urgency = body.urgency if body.urgency is not None else row["urgency"]
        sets.append("priority = ?")
        params.append(_priority(new_impact, new_urgency))

        now = _now()
        sets.append("updated_at = ?")
        params.append(now)

        if body.state in ("Resolved", "Closed"):
            sets.append("resolved_at = ?")
            params.append(now)

        params.append(number)
        conn.execute(
            f"UPDATE incidents SET {', '.join(sets)} WHERE number = ?", params
        )

    return _get_incident(number)


# ── Work notes ─────────────────────────────────────────────────────────────

@app.post("/api/incidents/{number}/notes", status_code=201)
def add_work_note(number: str, body: WorkNoteCreate):
    _get_incident(number)  # 404 if missing
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO work_notes (incident, note, created_at) VALUES (?,?,?)",
            (number, body.note, now),
        )
        conn.execute(
            "UPDATE incidents SET updated_at = ? WHERE number = ?", (now, number)
        )
    return _get_incident(number)


# ── UI helpers ────────────────────────────────────────────────────────────

_STATE_COLOR = {
    "New": "#0d6efd",
    "In Progress": "#e8a317",
    "On Hold": "#6c757d",
    "Resolved": "#198754",
    "Closed": "#495057",
}

_PRIORITY_LABEL = {
    1: "1 — Critical",
    2: "2 — High",
    3: "3 — Moderate",
    4: "4 — Low",
    5: "5 — Planning",
}

_IMPACT_LABEL = {1: "1 — High", 2: "2 — Medium", 3: "3 — Low"}
_URGENCY_LABEL = _IMPACT_LABEL

_CSS = """\
:root { --navy: #1a1a2e; --bg: #f4f5f7; --card: #fff; --border: #dfe1e6; --text: #172b4d; --muted: #6b778c; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); }
a { color: #0052cc; text-decoration: none; }
a:hover { text-decoration: underline; }
.topbar { background: var(--navy); color: #fff; padding: 0 2rem; height: 48px; display: flex; align-items: center; gap: 1.5rem; }
.topbar .brand { font-weight: 700; font-size: 1.05rem; letter-spacing: .3px; }
.topbar a { color: #c2c7d0; font-size: .9rem; }
.topbar a:hover { color: #fff; text-decoration: none; }
.container { max-width: 1280px; margin: 0 auto; padding: 1.5rem 2rem; }
.stats { display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
.stat { background: var(--card); padding: 1rem 1.5rem; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1); min-width: 140px; }
.stat .value { font-size: 1.8rem; font-weight: 700; color: var(--navy); }
.stat .label { color: var(--muted); font-size: .85rem; margin-top: 2px; }
table { width: 100%; border-collapse: collapse; background: var(--card); box-shadow: 0 1px 3px rgba(0,0,0,.1); border-radius: 8px; overflow: hidden; }
th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); }
th { background: var(--navy); color: #fff; font-weight: 600; font-size: .85rem; text-transform: uppercase; letter-spacing: .4px; }
tr:hover { background: #f0f4ff; }
td.number { font-weight: 600; }
.badge { display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: .8rem; font-weight: 600; color: #fff; }
.breadcrumb { font-size: .85rem; color: var(--muted); margin-bottom: 1rem; }
.page-header { display: flex; align-items: center; gap: 1rem; margin-bottom: .25rem; flex-wrap: wrap; }
.page-header h1 { font-size: 1.5rem; }
.card { background: var(--card); border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 1.5rem; }
.card-header { padding: 12px 20px; border-bottom: 1px solid var(--border); font-weight: 700; font-size: .95rem; color: var(--navy); }
.card-body { padding: 16px 20px; }
.field-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
.field { padding: 10px 20px; border-bottom: 1px solid var(--border); }
.field:nth-child(odd) { border-right: 1px solid var(--border); }
.field-label { font-size: .75rem; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); margin-bottom: 3px; }
.field-value { font-size: .95rem; word-break: break-word; }
.field-value.empty { color: var(--muted); font-style: italic; }
.field.full-width { grid-column: 1 / -1; border-right: none; }
.desc-box { white-space: pre-wrap; font-size: .9rem; line-height: 1.6; background: #f8f9fa; padding: 14px 18px; border-radius: 6px; max-height: 500px; overflow-y: auto; }
.desc-box.md { white-space: normal; }
.desc-box.md h1, .desc-box.md h2, .desc-box.md h3, .desc-box.md h4 { margin: 1em 0 .5em; color: var(--navy); }
.desc-box.md h1 { font-size: 1.3rem; border-bottom: 1px solid var(--border); padding-bottom: .3em; }
.desc-box.md h2 { font-size: 1.15rem; border-bottom: 1px solid var(--border); padding-bottom: .25em; }
.desc-box.md h3 { font-size: 1.05rem; }
.desc-box.md p { margin: 0 0 .75em; }
.desc-box.md ul, .desc-box.md ol { margin: 0 0 .75em; padding-left: 1.5em; }
.desc-box.md li { margin-bottom: .25em; }
.desc-box.md code { background: #e9ecef; padding: 2px 5px; border-radius: 3px; font-size: .85em; }
.desc-box.md pre { background: #1e293b; color: #e2e8f0; padding: 12px 16px; border-radius: 6px; overflow-x: auto; margin: 0 0 .75em; }
.desc-box.md pre code { background: none; padding: 0; color: inherit; font-size: .85em; }
.desc-box.md table { border-collapse: collapse; width: 100%; margin: 0 0 .75em; }
.desc-box.md th, .desc-box.md td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; font-size: .85rem; }
.desc-box.md th { background: #e9ecef; font-weight: 600; }
.desc-box.md blockquote { border-left: 3px solid var(--blue); margin: 0 0 .75em; padding: 4px 12px; color: var(--muted); }
.desc-box.md hr { border: none; border-top: 1px solid var(--border); margin: 1em 0; }
.desc-box.md > :first-child { margin-top: 0; }
.desc-box.md > :last-child { margin-bottom: 0; }
.activity-item { padding: 14px 20px; border-bottom: 1px solid var(--border); }
.activity-item:last-child { border-bottom: none; }
.activity-meta { font-size: .8rem; color: var(--muted); margin-bottom: 6px; }
.activity-text { font-size: .9rem; line-height: 1.5; white-space: pre-wrap; }
.no-data { padding: 20px; color: var(--muted); text-align: center; font-style: italic; }
"""


def _badge(state: str) -> str:
    color = _STATE_COLOR.get(state, "#6c757d")
    return f'<span class="badge" style="background:{color}">{state}</span>'


def _fv(val, fallback="—") -> str:
    from html import escape as _esc
    if val is None or str(val).strip() == "":
        return f'<span class="field-value empty">{fallback}</span>'
    return f'<span class="field-value">{_esc(str(val))}</span>'


def _topbar() -> str:
    return (
        '<div class="topbar">'
        '<span class="brand">Incident Management</span>'
        '<a href="/">Dashboard</a>'
        '<a href="/docs" target="_blank">API Docs</a>'
        '</div>'
    )


# ── Dashboard ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM incidents ORDER BY opened_at DESC LIMIT 100"
        ).fetchall()

    from html import escape as _esc

    table_rows = ""
    for r in rows:
        table_rows += (
            "<tr>"
            f'<td class="number"><a href="/incidents/{r["number"]}">{r["number"]}</a></td>'
            f"<td>{_badge(r['state'])}</td>"
            f"<td>{_PRIORITY_LABEL.get(r['priority'], r['priority'])}</td>"
            f"<td>{_esc(r['category'])}</td>"
            f'<td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{_esc(r["short_description"])}</td>'
            f"<td>{_esc(r['caller_id'])}</td>"
            f"<td>{r['opened_at']}</td>"
            f"<td>{r['updated_at']}</td>"
            "</tr>"
        )

    total = len(rows)
    new = sum(1 for r in rows if r["state"] == "New")
    wip = sum(1 for r in rows if r["state"] == "In Progress")
    done = sum(1 for r in rows if r["state"] in ("Resolved", "Closed"))

    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Incident Dashboard</title>"
        f"<style>{_CSS}</style></head><body>"
        f"{_topbar()}"
        '<div class="container">'
        '<h1 style="margin:1rem 0 .75rem;font-size:1.4rem">All Incidents</h1>'
        '<div class="stats">'
        f'<div class="stat"><div class="value">{total}</div><div class="label">Total</div></div>'
        f'<div class="stat"><div class="value">{new}</div><div class="label">New</div></div>'
        f'<div class="stat"><div class="value">{wip}</div><div class="label">In Progress</div></div>'
        f'<div class="stat"><div class="value">{done}</div><div class="label">Resolved / Closed</div></div>'
        "</div>"
        "<table><thead><tr>"
        "<th>Number</th><th>State</th><th>Priority</th><th>Category</th>"
        "<th>Short Description</th><th>Caller</th><th>Opened</th><th>Updated</th>"
        "</tr></thead><tbody>"
        + (table_rows if table_rows else '<tr><td colspan="8" class="no-data">No incidents yet</td></tr>')
        + "</tbody></table></div></body></html>"
    )


# ── Incident detail view ─────────────────────────────────────────────────

@app.get("/incidents/{number}", response_class=HTMLResponse)
def incident_detail_view(number: str):
    from html import escape as _esc

    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM incidents WHERE number = ?", (number,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"Incident {number} not found")
        notes = conn.execute(
            "SELECT * FROM work_notes WHERE incident = ? ORDER BY created_at DESC",
            (number,),
        ).fetchall()

    r = dict(row)

    def _desc_box(text: str, empty_msg: str = "None") -> str:
        if text and text.strip():
            rendered = md.markdown(
                text,
                extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
            )
            return f'<div class="desc-box md">{rendered}</div>'
        return f'<div class="desc-box empty" style="color:var(--muted);font-style:italic">{empty_msg}</div>'

    notes_html = ""
    if notes:
        for n in notes:
            notes_html += (
                '<div class="activity-item">'
                f'<div class="activity-meta">{n["created_at"]} UTC</div>'
                f'<div class="activity-text">{_esc(n["note"])}</div>'
                "</div>"
            )
    else:
        notes_html = '<div class="no-data">No work notes yet</div>'

    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{r['number']} — Incident</title>"
        f"<style>{_CSS}</style></head><body>"
        f"{_topbar()}"
        '<div class="container">'
        # breadcrumb + header
        f'<div class="breadcrumb"><a href="/">Incidents</a> &rsaquo; {r["number"]}</div>'
        f'<div class="page-header"><h1>{r["number"]}</h1>{_badge(r["state"])}</div>'
        f'<p style="color:var(--muted);margin-bottom:1.25rem;font-size:.95rem">{_esc(r["short_description"])}</p>'
        # Details card
        '<div class="card"><div class="card-header">Details</div>'
        '<div class="field-grid">'
        f'<div class="field"><div class="field-label">Number</div>{_fv(r["number"])}</div>'
        f'<div class="field"><div class="field-label">State</div><span class="field-value">{_badge(r["state"])}</span></div>'
        f'<div class="field"><div class="field-label">Priority</div>{_fv(_PRIORITY_LABEL.get(r["priority"], r["priority"]))}</div>'
        f'<div class="field"><div class="field-label">Category</div>{_fv(r["category"])}</div>'
        f'<div class="field"><div class="field-label">Impact</div>{_fv(_IMPACT_LABEL.get(r["impact"], r["impact"]))}</div>'
        f'<div class="field"><div class="field-label">Urgency</div>{_fv(_URGENCY_LABEL.get(r["urgency"], r["urgency"]))}</div>'
        f'<div class="field"><div class="field-label">Subcategory</div>{_fv(r["subcategory"])}</div>'
        f'<div class="field"><div class="field-label">Caller</div>{_fv(r["caller_id"])}</div>'
        f'<div class="field full-width"><div class="field-label">Short Description</div>{_fv(r["short_description"])}</div>'
        "</div></div>"
        # Assignment card
        '<div class="card"><div class="card-header">Assignment</div>'
        '<div class="field-grid">'
        f'<div class="field"><div class="field-label">Assignment Group</div>{_fv(r["assignment_group"])}</div>'
        f'<div class="field"><div class="field-label">Assigned To</div>{_fv(r["assigned_to"])}</div>'
        "</div></div>"
        # Dates card
        '<div class="card"><div class="card-header">Dates</div>'
        '<div class="field-grid">'
        f'<div class="field"><div class="field-label">Opened</div>{_fv(r["opened_at"])}</div>'
        f'<div class="field"><div class="field-label">Updated</div>{_fv(r["updated_at"])}</div>'
        f'<div class="field"><div class="field-label">Resolved</div>{_fv(r["resolved_at"])}</div>'
        '<div class="field">&nbsp;</div>'
        "</div></div>"
        # Description card
        '<div class="card"><div class="card-header">Description</div>'
        f'<div class="card-body">{_desc_box(r["description"], "No description provided")}</div></div>'
        # Resolution card
        '<div class="card"><div class="card-header">Resolution Information</div>'
        f'<div class="card-body">{_desc_box(r["close_notes"], "No resolution notes")}</div></div>'
        # Activity card
        f'<div class="card"><div class="card-header">Activity · Work Notes ({len(notes)})</div>'
        f"{notes_html}</div>"
        "</div></body></html>"
    )


# ── Health endpoint ───────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}
