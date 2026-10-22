# MCP concepts, mapped to this project

This explains the ideas behind the "Building a Custom MCP Server" diagram and where each one
lives in the code.

## 1. What MCP is

The **Model Context Protocol** is a standard way for an AI application to use outside tools and
data. Without it, every app would need custom glue code for every database or API. With it, you
write one *server*, and any MCP-capable *client* can use it.

| Role | In the diagram | In this project |
|---|---|---|
| **Host / client** | "AI Agent Application" (+ its LLM) | Claude Desktop, Claude Code, the MCP Inspector, or your own agent |
| **Server** | "MCP Server" | `server.py` and the `tools/` package |
| **Data source** | "Patient Records" | `patients.db`, reached only through `db.py` |

The **LLM never touches the database**. It can only ask the client to call a tool; the server
runs the tool and returns the result.

## 2. The message flow

1. You type a prompt: *"What's the risk level of patient 12?"*
2. The client asks the server **List All Tools** (`tools/list`). The server replies with each
   tool's name, description and input schema. The client shows these to the LLM.
3. The LLM decides `get_patient_risk_summary` fits and produces `{"patient_id": 12}`.
4. The client sends **Call Tool** (`tools/call`) to the server.
5. The server runs the matching Python function, which reads SQLite and returns a result.
6. The client gives the result to the LLM, which writes the final answer for you.

Steps 2 and 4 are the two arrows labelled "List All Tools" and "Call Tool" in the diagram.

## 3. How code becomes a tool

```python
@mcp.tool()
def get_patient_risk_summary(patient_id: int) -> dict:
    """Calculate a risk score ... """
```

| Part of the function | Becomes |
|---|---|
| function name | the tool name |
| **docstring** | the description the LLM reads to decide *when* to use it |
| parameters + type hints | the JSON input schema; wrong types are rejected before your code runs |
| return value | the result (a `dict` is sent as structured JSON) |

Good docstrings matter more than anything else here: they are the LLM's only documentation.

## 4. Transports (the arrows in the diagram)

A transport is how client and server exchange messages. The tools themselves don't change.

| Transport | Use | Command |
|---|---|---|
| **stdio** | Local. The client launches the server as a child process and talks over stdin/stdout. | `python server.py` |
| **Streamable HTTP** | Remote. The server runs as a web service at one URL (`/mcp`). Current standard for remote servers. | `python server.py --transport streamable-http` |
| **SSE** | Older HTTP transport, **deprecated** — kept for old clients only. | `python server.py --transport sse` |

`uvicorn` (web server) and `starlette` (web framework) in `requirements.txt` are what the HTTP
transports run on. The SDK uses them for you, and `server.py` uses them directly to add CORS.

Because stdout *is* the protocol channel in stdio mode, never `print()` to it.

### What a Streamable HTTP session looks like

Every message is an HTTP `POST` of one JSON-RPC message to `/mcp`:

| Step | Request | Response |
|---|---|---|
| 1 | `initialize` | Result, plus an **`Mcp-Session-Id` header** that every later request must send back |
| 2 | `notifications/initialized` | `202 Accepted`, empty body (a notification has no `id`, so no reply) |
| 3 | `tools/list` | The tools: name, description, input schema |
| 4 | `tools/call` with `name` + `arguments` | The result: text `content` and usually `structuredContent` |

Replies arrive either as plain JSON or as a Server-Sent Events stream (`event: message` /
`data: {...}`); a client has to accept both. `client/index.html` is a complete, readable
implementation of this (the `McpClient` class and `parseMessages`, about 130 commented lines).

### Browsers need CORS (the other clients don't)

A browser lets a web page read a response from a *different origin* (scheme + host + port) only
if the server opts in with **CORS** headers. The client page at `http://localhost:3000` and the
server at `http://127.0.0.1:8000` are different origins. Because the request carries
`Content-Type: application/json`, the browser first sends an `OPTIONS` **preflight**; a server
without CORS answers `405` and the browser never sends the real request.

Claude Desktop, Claude Code and Python clients are not browsers and never enforce CORS, which is
why the server worked for them without it.

`--cors-origin ORIGIN` (HTTP only) makes `server.py` do three things:

1. Answer the preflight and add `Access-Control-Allow-Origin` — for the listed origins only.
2. Expose the `Mcp-Session-Id` header. JavaScript can't read custom response headers unless the
   server lists them in `Access-Control-Expose-Headers`.
3. Add the origin to the SDK's own **Origin/Host check** (DNS-rebinding protection). That check is
   separate from CORS; for a loopback server it already allows `localhost` and `127.0.0.1`, and
   refuses any other `Origin` with `403 Invalid Origin header`.

CORS is enforced by the *browser*, not the server. It protects people visiting other websites;
it is not authentication. Anything that isn't a browser (`curl`, a script) can still call the
endpoint, so a server exposed beyond your own machine needs real authentication.

## 5. The three MCP primitives

| Primitive | Meaning | Used here? |
|---|---|---|
| **Tools** | Actions the LLM can call | Yes — all six |
| **Resources** | Read-only data the app can load (files, records) | No |
| **Prompts** | Reusable prompt templates | No |

Possible next steps: expose the schema as a *resource*, or add a "clinical summary" *prompt*.

## 6. Errors

- `raise ToolError("message")` — a failure you anticipated (unknown patient, bad SQL). The client
  gets `isError: true` with your message, so the LLM can correct itself and retry.
- Any other exception is treated as a crash: the client sees only `Error executing tool <name>`
  and the traceback goes to the server log. This hides internals on purpose.
- Wrong argument types (e.g. `patient_id="abc"`) are rejected automatically from the type hints.

## 7. Security design

| Risk | Protection |
|---|---|
| LLM modifies or deletes data | `db.py` opens SQLite with `mode=ro`; the engine refuses every write. A test with `WITH x AS (SELECT 1) DELETE FROM Patients` — which passes a text filter — is rejected by SQLite itself. |
| SQL injection via parameters | Values are always passed as `?` placeholders, never pasted into SQL strings. |
| Huge results flooding the LLM | `run_sql_query` returns at most 100 rows and reports `truncated`. |
| Network exposure | HTTP mode binds to `127.0.0.1` by default. Add authentication before exposing it. |
| A website you visit reads patient data from your local server | CORS is off unless you pass `--cors-origin`, which takes exact origins only; `*` is rejected. The SDK also refuses unknown `Origin` headers. |
| Database content running as script in the browser client | `client/index.html` builds the page from text nodes only (never `innerHTML`), so a value like `<img onerror=...>` in the database is displayed, not executed. |
| Real patient data (PHI) | This project uses synthetic data. Real data needs access control, audit logging and an LLM provider covered by the right agreements (e.g. HIPAA BAA). |

`run_sql_query` can still read *any* table, so don't put it on a database containing data the
user shouldn't see.

## 8. SDK version note

`mcp` 2.x renamed `FastMCP` to `MCPServer` (`from mcp.server.mcpserver import MCPServer`) and
changed error handling (`ToolError`, above). Older tutorials show `from mcp.server.fastmcp import
FastMCP` (v1). To use v1 code instead, pin `mcp[cli]<2` — but this project's code targets v2.
