"""
storage.py — persists a finished plan so it can be reopened via a
shareable link, e.g. https://your-app-url/?plan=abc123.

Uses a local SQLite file rather than an external service, so no extra
account, API key, or paid database is needed to make sharing real. The
trade-off: on hosting with an ephemeral filesystem (some free-tier
container platforms wipe local disk on every redeploy/restart), saved
plans won't survive a redeploy. See the README for options if that matters
to you — pointing MEETHALFWAY_DB_PATH at a mounted volume, or swapping
this module for a hosted database, without changing anything in app.py.

By default the database file lives in the user's home directory
(~/.meethalfway/), deliberately NOT next to app.py. Streamlit's dev-server
file watcher monitors the app's own directory for changes to trigger
hot-reloads; writing a frequently-changing file there (every finished
search writes a new row) risks being picked up as a "source changed"
event, which is a plausible cause of results seeming to reset unexpectedly
mid-session. Keeping the database outside that directory avoids the
question entirely, at zero cost.
"""

import json
import os
import secrets
import sqlite3
import time
from typing import Optional

try:
    _default_dir = os.path.join(os.path.expanduser("~"), ".meethalfway")
    os.makedirs(_default_dir, exist_ok=True)
    _default_path = os.path.join(_default_dir, "meethalfway.db")
except OSError:
    # Fall back to sitting next to the app if the home directory isn't
    # writable for some reason (e.g. certain locked-down containers).
    _default_path = os.path.join(os.path.dirname(__file__), "meethalfway.db")

DB_PATH = os.getenv("MEETHALFWAY_DB_PATH", _default_path)


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            data TEXT NOT NULL
        )
        """
    )
    return conn


def init_db() -> None:
    """Ensure the plans table exists. Safe and cheap to call on every run."""
    _get_conn().close()


def save_plan(data: dict) -> str:
    """
    Persist a plan (a JSON-serializable dict) and return its short id.
    Coordinates stored as lists (JSON has no tuple type) — every function
    that reads them back (optimization.py, routing.py, folium) only ever
    indexes into them positionally, so this round-trips without any
    special-casing.
    """
    plan_id = secrets.token_urlsafe(6)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO plans (id, created_at, data) VALUES (?, ?, ?)",
            (plan_id, time.time(), json.dumps(data)),
        )
        conn.commit()
    finally:
        conn.close()
    return plan_id


def load_plan(plan_id: str) -> Optional[dict]:
    """Look up a previously saved plan by id. Returns None if not found."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT data FROM plans WHERE id = ?", (plan_id,)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None
