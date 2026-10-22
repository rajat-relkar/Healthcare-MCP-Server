"""
db.py - Database access layer.

Every tool in this project talks to SQLite through this one file. Keeping all
the connection logic here means:
  * the database path is defined in exactly one place, and
  * every connection is opened READ-ONLY, so the LLM can never modify or
    delete patient records through this server (seed_data.py is the only
    code that writes, and it is run by you, not by the LLM).
"""

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

# The database file lives next to this script by default. If your MCP client
# starts the server from a different folder you can point to the file with the
# PATIENT_DB_PATH environment variable instead.
DB_PATH = Path(os.environ.get("PATIENT_DB_PATH", Path(__file__).parent / "patients.db"))


def get_connection() -> sqlite3.Connection:
    """Open a read-only connection to the patient database."""
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found at {DB_PATH}. Run 'python seed_data.py' first to create it."
        )

    # "mode=ro" is a SQLite URI option: the engine itself refuses any write
    # (INSERT / UPDATE / DELETE / DROP ...). This is a much stronger guarantee
    # than trying to filter dangerous SQL text ourselves.
    conn = sqlite3.connect(f"{DB_PATH.resolve().as_uri()}?mode=ro", uri=True)

    # Row objects let us read columns by name; we turn them into dicts below.
    conn.row_factory = sqlite3.Row
    return conn


def query(sql: str, params: tuple[Any, ...] = (), limit: int | None = None) -> list[dict[str, Any]]:
    """
    Run a SELECT and return the rows as a list of dicts, e.g.
        [{"patient_id": 1, "first_name": "Asha"}, ...]
    dicts convert straight to JSON, which is what MCP sends back to the client.

    sql    - the query text. Always use "?" placeholders for values...
    params - ...and pass the values here. SQLite fills them in safely, which
             prevents SQL injection.
    limit  - optional cap on how many rows to fetch.
    """
    # NOTE: "with sqlite3.connect(...)" only manages transactions, it does NOT
    # close the connection. contextlib.closing() guarantees conn.close().
    with closing(get_connection()) as conn:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchmany(limit) if limit else cursor.fetchall()
        return [dict(row) for row in rows]


def query_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Like query() but returns just the first row, or None if nothing matched."""
    rows = query(sql, params, limit=1)
    return rows[0] if rows else None


# Reusable SQL snippet: a patient's age in whole years, calculated by SQLite.
# We use it in several tools so age is always computed the same way.
AGE_SQL = "CAST((julianday('now') - julianday(date_of_birth)) / 365.25 AS INTEGER)"
