"""
tools package - every MCP tool this server offers lives in one module here.

Each module exposes a register(mcp) function. server.py calls register_all(),
which hands the server object to each module so it can attach its tools.
To add a new tool later: create a new module with a register(mcp) function,
then add it to the list below.
"""

from . import query_tool, readmission, risk_summary, schema_tool

_MODULES = (schema_tool, query_tool, risk_summary, readmission)


def register_all(mcp) -> None:
    for module in _MODULES:
        module.register(mcp)
