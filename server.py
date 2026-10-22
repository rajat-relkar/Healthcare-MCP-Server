"""
server.py - Entry point of the MCP server (the "MCP Server" box in the diagram).

How a request flows through the whole project:

    You (prompt) -> AI agent app (MCP client) -> LLM decides which tool to use
        -> MCP client sends "tools/call" to THIS server
        -> server.py routes it to a function in tools/
        -> the tool reads patients.db through db.py
        -> the result goes back to the client -> LLM -> your answer

Run it:
    python server.py                              # stdio (local) - the default
    python server.py --transport streamable-http  # HTTP (remote) on http://127.0.0.1:8000/mcp

    # HTTP + let the browser client in client/ talk to it (see client/index.html):
    python server.py --transport streamable-http --cors-origin http://localhost:3000
"""

import argparse
import sys

import uvicorn
from starlette.middleware.cors import CORSMiddleware

# In MCP SDK v2 the server class is called MCPServer. (In v1 it was named
# FastMCP, and most older tutorials still use that name.)
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from db import DB_PATH
from tools import register_all

# The server object. `name` is what the client shows to the user, and
# `instructions` is a short note the client gives to the LLM about this server.
mcp = MCPServer(
    "patient-db",
    instructions=(
        "Read-only access to a synthetic healthcare database (patients, visits, diagnoses, "
        "medications, lab results). Use search_patients to find a patient_id, "
        "get_database_schema before writing SQL, and the patient tools for details, "
        "risk summaries and readmissions."
    ),
)

# Attach every tool from the tools/ package to the server. After this line the
# server can answer "List All Tools" and "Call Tool" requests from clients.
register_all(mcp)

# Addresses that always count as "this machine" for the SDK's built-in Host/Origin check.
LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def run_http_with_cors(host: str, port: int, origins: list[str]) -> None:
    """
    Streamable HTTP plus CORS, so a web page served from one of `origins` can call us.

    Why this is needed: a browser only lets a page read a response from ANOTHER
    origin (scheme + host + port) if that server opts in with CORS headers.
    The client page lives on http://localhost:3000 and this server on
    http://127.0.0.1:8000, so they are different origins. Without CORS the
    browser first sends an "OPTIONS" preflight, gets "405 Method Not Allowed",
    and never sends the real request. Non-browser clients (Claude Desktop,
    Claude Code, Python) don't enforce CORS, which is why we didn't need it before.
    """
    # The SDK has its own, separate Origin check (protection against DNS-rebinding
    # attacks). Out of the box it only accepts localhost origins, so any other
    # origin we allow with CORS below must also be listed here.
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=LOCAL_HOSTS + [f"{host}:*"],
        allowed_origins=LOCAL_ORIGINS + origins,
    )
    app = mcp.streamable_http_app(host=host, transport_security=security)

    # The CORS middleware answers the browser's preflight and adds the
    # Access-Control-* headers to every response.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,                    # only the origins you listed - never "*"
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "Mcp-Session-Id", "MCP-Protocol-Version", "Last-Event-ID"],
        # By default JavaScript can't read custom response headers. The session id
        # comes back in a header, and every later request must send it, so expose it.
        expose_headers=["Mcp-Session-Id"],
    )
    uvicorn.run(app, host=host, port=port)


def main() -> None:
    parser = argparse.ArgumentParser(description="Patient database MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="stdio = local process (default); streamable-http = remote HTTP; sse = deprecated HTTP",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP transports only")
    parser.add_argument("--port", type=int, default=8000, help="HTTP transports only")
    parser.add_argument(
        "--cors-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="streamable-http only: let a web page from this exact origin call the server "
             "(repeat for several), e.g. http://localhost:3000",
    )
    args = parser.parse_args()

    origins = [origin.rstrip("/") for origin in args.cors_origin]
    if origins and args.transport != "streamable-http":
        parser.error("--cors-origin only works with --transport streamable-http")
    if any(origin == "*" or not origin.startswith(("http://", "https://")) for origin in origins):
        # A wildcard would let ANY website you visit read patient data from this server.
        parser.error("--cors-origin must be an exact origin such as http://localhost:3000 (no '*')")

    # Fail fast with a readable message instead of failing later, inside a tool call.
    if not DB_PATH.exists():
        sys.exit(f"Database not found at {DB_PATH}. Run 'python seed_data.py' first.")

    # With stdio the client talks to us through stdin/stdout, so NEVER print()
    # to stdout in this server: it would corrupt the protocol messages.
    # Use logging or print(..., file=sys.stderr) if you need debug output.
    if args.transport == "stdio":
        mcp.run("stdio")
    elif origins:
        run_http_with_cors(args.host, args.port, origins)
    else:
        # Binds to 127.0.0.1 (this machine only) by default. Patient data should
        # not be exposed on a network without authentication in front of it.
        mcp.run(args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
