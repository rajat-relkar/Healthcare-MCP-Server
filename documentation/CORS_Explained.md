# CORS middleware, step by step

How `server.py` lets a web page talk to the MCP server, and why. CORS only matters for the
browser client (`client/index.html`). Claude Desktop, Claude Code and Python clients are not
browsers and never check it.

The code lives in `run_http_with_cors()` in `server.py` and runs only when you pass `--cors-origin`.

---

## 1. The problem it solves

Browsers enforce the **same-origin policy**: a page may only *read* responses from the origin it
was loaded from. An origin is **scheme + host + port**.

| | Origin |
|---|---|
| The web page (`client/index.html`) | `http://localhost:3000` |
| The MCP server | `http://127.0.0.1:8000` |

The host and port both differ, so these are different origins. The browser lets the page *send*
the request but blocks it from *reading* the response. This protects you: otherwise any website
you visit could read data from servers running on your machine, such as the patient database.

## 2. CORS is the opt-in

**CORS** (Cross-Origin Resource Sharing) is how a server tells the browser which other origins may
read its responses, using HTTP headers such as:

```
Access-Control-Allow-Origin: http://localhost:3000
```

No such header, no access. Scripts, `curl`, Claude Desktop and Claude Code don't enforce CORS,
which is why the server worked for them before the web client existed.

## 3. The preflight request

The client sends `Content-Type: application/json` and custom headers such as `Mcp-Session-Id`.
The browser treats that as a "non-simple" request and asks permission first with an `OPTIONS`
request, called the **preflight**:

```
Browser  ── OPTIONS /mcp                                   ──▶  Server
            Origin: http://localhost:3000
            Access-Control-Request-Method: POST
            Access-Control-Request-Headers: content-type, mcp-session-id

Server   ── 200 OK                                         ──▶  Browser
            Access-Control-Allow-Origin: http://localhost:3000
            Access-Control-Allow-Methods: GET, POST, DELETE, OPTIONS
            Access-Control-Allow-Headers: ...
```

Only if the answer allows it does the browser send the real `POST`.

Without CORS handling, the server answers the preflight with **405 Method Not Allowed**. The
browser gives up and the real request is never sent. To JavaScript this looks the same as the
server being down.

## 4. What "middleware" means

Middleware is code that sits in front of your app and sees every request and response:

```
request ──▶ [CORS middleware] ──▶ MCP app ──▶ [CORS middleware] ──▶ response
```

`CORSMiddleware` does two jobs:

1. It answers `OPTIONS` preflights itself, so the MCP code never sees them.
2. It adds the `Access-Control-*` headers to every normal response.

It is attached with one call, and the MCP code stays unchanged.

## 5. The settings, line by line

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "Mcp-Session-Id", "MCP-Protocol-Version", "Last-Event-ID"],
    expose_headers=["Mcp-Session-Id"],
)
```

| Setting | Meaning |
|---|---|
| `allow_origins` | The exact pages allowed in, taken from `--cors-origin`. Never `*`. |
| `allow_methods` | HTTP verbs the page may use. MCP uses `POST` for requests, `GET` for the event stream and `DELETE` to end a session. |
| `allow_headers` | Request headers the page may send. These are the ones MCP uses. |
| `expose_headers` | Response headers JavaScript is allowed to read. By default it can't read custom ones. The server returns the session id in `Mcp-Session-Id` and every later request must send it back, so it has to be exposed. Without it the client can connect but can't continue the session. |

## 6. Why not just allow `*`?

`allow_origins=["*"]` would let **any website you open** read patient data from the server while it
is running. `server.py` rejects it on startup. You must name each allowed origin:

```powershell
python server.py --transport streamable-http --cors-origin http://localhost:3000
```

`http://localhost:3000` and `http://127.0.0.1:3000` are different origins. Pass the one in the
browser's address bar, and repeat `--cors-origin` to allow several.

## 7. The second, separate check

CORS is enforced by the **browser**. The MCP SDK also has its own server-side check against
DNS-rebinding attacks. It looks at the `Host` and `Origin` headers and rejects anything not on its
allow-list with `403 Invalid Origin header`. It already allows `localhost` and `127.0.0.1`, so
`server.py` also adds your `--cors-origin` values to it through `TransportSecuritySettings`.

**An origin must be allowed in both places.**

## 8. What CORS is *not*

CORS protects people visiting other websites. It is **not authentication**. Anything that isn't a
browser (`curl`, a script) can still call the endpoint. A server exposed beyond your own machine
needs real authentication.

## Summary

| Question | Answer |
|---|---|
| Who enforces CORS? | The browser, not the server |
| What does the server do? | Sends headers saying which origins may read its responses |
| What does the middleware do? | Answers preflight `OPTIONS` requests and adds those headers automatically |
| Who needs it here? | Only `client/index.html` |
| Is it security for the data? | No. It stops other websites from reading it through your browser, nothing more. |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Browser client says "Can't reach the server" | The server isn't running, or the browser blocked the request (CORS). Both look the same to JavaScript. Check the server is up and `--cors-origin` matches the address bar exactly. |
| `405 Method Not Allowed` on `OPTIONS` | The server was started without `--cors-origin`. |
| `403 Invalid Origin header` | The origin isn't in the SDK's allow-list. Pass it with `--cors-origin`. |
| Page opened by double-clicking the file | A `file://` page has the origin `null`, which the server refuses. Open it through `http://localhost:3000`. |
| `--cors-origin` rejected on startup | It needs an exact origin like `http://localhost:3000`, with no `*`. |
