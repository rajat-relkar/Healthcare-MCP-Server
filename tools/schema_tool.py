"""
schema_tool.py - Lets the LLM discover what is in the database.

Why this exists: the LLM cannot see your database. Before it can write a
correct SQL query (see run_sql_query in query_tool.py) it needs to know the
table and column names. This tool tells it.
"""

from contextlib import closing
from typing import Any

from db import get_connection


def register(mcp) -> None:
    # @mcp.tool() turns the function below into an MCP tool. Three things are
    # taken from the function automatically and sent to the client when it
    # asks "List All Tools":
    #   * the tool name        -> the function name
    #   * the description      -> the docstring (the LLM reads this to decide
    #                             WHEN to call the tool, so write it for the LLM)
    #   * the input schema     -> the parameter names and type hints
    @mcp.tool()
    def get_database_schema() -> dict[str, Any]:
        """
        List every table in the patient database with its columns and row count.
        Call this first when you need to know what data is available or before
        writing a SQL query.
        """
        schema: dict[str, Any] = {}
        with closing(get_connection()) as conn:
            # sqlite_master is SQLite's built-in catalogue of tables.
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()

            for row in tables:
                name = row["name"]   # comes from sqlite_master, not from the user, so it is safe to use in SQL
                # PRAGMA table_info returns one row per column:
                # (cid, name, type, notnull, default_value, pk)
                columns = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
                count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                schema[name] = {
                    "row_count": count,
                    "columns": [
                        {
                            "name": col["name"],
                            "type": col["type"],
                            "primary_key": bool(col["pk"]),
                            "required": bool(col["notnull"]),
                        }
                        for col in columns
                    ],
                }
        return schema
