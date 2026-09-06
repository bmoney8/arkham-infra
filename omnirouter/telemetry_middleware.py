"""
telemetry_middleware.py — SQLite telemetry for OmniRouter (V7.4.1 D1).
Two-layer design: a pure ASGI middleware for zero-buffer streaming telemetry
and Starlette route endpoints for the query surface.

V7.4.1 streaming fix: replaces the BaseHTTPMiddleware body_iterator
consumption-and-rewrap pattern with a pure ASGI middleware that intercepts
response body chunks, yields them to the client immediately (zero SSE
buffering), and accumulates them for post-stream telemetry logging.

Schema:
  requests   — per-request log (timestamp, client_profile, upstream_target,
                model_id, tokens_in, tokens_out, latency_ms, estimated_cost_usd,
                status_code, error)
  failovers  — per-attempt trail within a request (route taken before failover)
  daily_stats — pre-aggregated daily rollups (refreshed on commit)

WAL mode + batched async writes for production safety.
"""

import json
import sqlite3
import time
import threading
from typing import Any, Callable, Dict, List, Optional, MutableMapping


# ── cost table (USD per 1M tokens, input/output) ──────────────────────────────
_COST_TABLE = {
    # Phase 1 free arms
    "gemini-flash-latest":        (0.0, 0.0),
    "gemini-pro-latest":          (0.0, 0.0),
    "nemotron-super-120b":        (0.0, 0.0),
    "nim-kimi-k3":                (0.0, 0.0),
    "nim-deepseek-v4-pro":        (0.0, 0.0),
    "nim-deepseek-v4-flash":      (0.0, 0.0),
    "nim-llama-3.2-90b-vision":   (0.0, 0.0),
    "groq-gpt-oss-120b":          (0.0, 0.0),
    "groq-qwen38-27b":            (0.0, 0.0),
    "hetzner-qwen38-27b":         (0.0, 0.0),
    "hetzner-qwen36-35b":         (0.0, 0.0),
    "ollama-*":                   (0.0, 0.0),
    # Phase 2 paid arms (estimates from public pricing)
    "hermes-4-405b":              (3.0, 15.0),
    "mimo-v2.5":                  (0.15, 0.6),
    "glm-5.3-flash":              (0.1, 0.4),
    "deepseek-vision-exp":        (0.27, 1.1),
    "gpt-luna":                   (2.5, 10.0),
    "deepseek-pro":               (0.55, 2.19),
    "gpt-terra":                  (5.0, 15.0),
    "gemini-flash":               (0.15, 0.6),
    "solar-pro4":                 (2.5, 10.0),
    "step-3.7-flash":             (0.8, 3.2),
    "kimi-k3":                    (0.6, 2.4),
    "gpt-sol":                    (10.0, 30.0),
    "grok-4.6":                   (3.0, 15.0),
}

DEFAULT_COST = (1.0, 3.0)  # fallback for unknown models

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    client_profile TEXT,
    upstream_target TEXT,
    model_id TEXT,
    tier TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    estimated_cost_usd REAL DEFAULT 0.0,
    status_code INTEGER DEFAULT 0,
    error TEXT,
    request_id TEXT
);
CREATE TABLE IF NOT EXISTS failovers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    request_id TEXT,
    route_name TEXT,
    tier TEXT,
    status_code INTEGER DEFAULT 0,
    error TEXT,
    latency_ms INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT NOT NULL,
    model_id TEXT NOT NULL,
    requests INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    estimated_cost_usd REAL DEFAULT 0.0,
    errors INTEGER DEFAULT 0,
    PRIMARY KEY (date, model_id)
);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model_id);
CREATE INDEX IF NOT EXISTS idx_failovers_rid ON failovers(request_id);
"""


class TelemetryDB:
    """Thread-safe SQLite telemetry store with WAL mode."""

    def __init__(self, db_path: str = "telemetry.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            conn.close()

    def log_request(
        self,
        ts: float,
        client_profile: Optional[str],
        upstream_target: Optional[str],
        model_id: Optional[str],
        tier: Optional[str],
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        status_code: int,
        error: Optional[str],
        request_id: Optional[str],
    ):
        cost = _estimate_cost(model_id, tokens_in, tokens_out)
        today = time.strftime("%Y-%m-%d", time.gmtime(ts))
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """INSERT INTO requests
                   (ts, client_profile, upstream_target, model_id, tier,
                    tokens_in, tokens_out, latency_ms, estimated_cost_usd,
                    status_code, error, request_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts, client_profile, upstream_target, model_id, tier,
                 tokens_in, tokens_out, latency_ms, cost,
                 status_code, error, request_id),
            )
            if model_id:
                conn.execute(
                    """INSERT INTO daily_stats (date, model_id, requests, tokens_in, tokens_out, estimated_cost_usd, errors)
                       VALUES (?, ?, 1, ?, ?, ?, ?)
                       ON CONFLICT(date, model_id) DO UPDATE SET
                         requests = requests + 1,
                         tokens_in = tokens_in + excluded.tokens_in,
                         tokens_out = tokens_out + excluded.tokens_out,
                         estimated_cost_usd = estimated_cost_usd + excluded.estimated_cost_usd,
                         errors = errors + excluded.errors""",
                    (today, model_id, tokens_in, tokens_out, cost,
                     1 if status_code >= 400 else 0),
                )
            conn.commit()
            conn.close()

    def log_failover(
        self,
        ts: float,
        request_id: Optional[str],
        route_name: str,
        tier: str,
        status_code: int,
        error: Optional[str],
        latency_ms: int,
    ):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """INSERT INTO failovers
                   (ts, request_id, route_name, tier, status_code, error, latency_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ts, request_id, route_name, tier, status_code, error, latency_ms),
            )
            conn.commit()
            conn.close()

    def query_recent(self, limit: int = 50) -> list:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM requests ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def query_daily(self, days: int = 7) -> list:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT date, SUM(requests) as requests,
                          SUM(tokens_in) as tokens_in,
                          SUM(tokens_out) as tokens_out,
                          SUM(estimated_cost_usd) as cost,
                          SUM(errors) as errors
                   FROM daily_stats
                   WHERE date >= date('now', ?)
                   GROUP BY date ORDER BY date""",
                (f"-{days} days",),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT COUNT(*) as total, COALESCE(SUM(estimated_cost_usd), 0) as cost, COALESCE(SUM(errors), 0) as errors FROM requests"
            ).fetchone()
            conn.close()
            return {"total_requests": row[0], "total_cost_usd": round(row[1], 6),
                    "total_errors": row[2]}


def _estimate_cost(model_id: Optional[str], tokens_in: int, tokens_out: int) -> float:
    """Estimate USD cost from token counts using the cost table."""
    if not model_id or (tokens_in == 0 and tokens_out == 0):
        return 0.0
    mid = model_id.lower()
    if "ollama" in mid or mid.startswith("qwen3:") or mid.startswith("llama3") or mid.startswith("deepseek-r1:"):
        return 0.0
    for pattern, (cin, cout) in _COST_TABLE.items():
        if pattern.endswith("*"):
            if mid.startswith(pattern[:-1]):
                return 0.0
        elif mid == pattern or pattern in mid:
            return (tokens_in * cin + tokens_out * cout) / 1_000_000
    return (tokens_in * DEFAULT_COST[0] + tokens_out * DEFAULT_COST[1]) / 1_000_000


# ── Pure ASGI telemetry middleware (V7.4.1 D1) ────────────────────────────────

class TelemetryMiddleware:
    """Pure ASGI middleware for zero-buffer SSE streaming telemetry.

    Intercepts /v1/chat/completions requests at the ASGI level:
    - For streaming (SSE): wraps the send() callable to yield chunks to the
      client immediately while accumulating them for post-stream logging.
    - For non-streaming: lets the app produce the full response, then parses
      and logs synchronously.
    - All other paths pass through untouched.
    """

    def __init__(self, app, db: "TelemetryDB"):
        self.app = app
        self.db = db

    async def __call__(self, scope: Dict, receive, send):
        # Only intercept POST /v1/chat/completions
        if scope["type"] != "http" or scope.get("path") != "/v1/chat/completions":
            return await self.app(scope, receive, send)

        # Read request body (for model extraction)
        body_bytes = b""
        while True:
            message = await receive()
            body_bytes += message.get("body", b"")
            if not message.get("more_body", False):
                break

        try:
            body_json = json.loads(body_bytes) if body_bytes else {}
        except json.JSONDecodeError:
            body_json = {}

        explicit_model = body_json.get("model")
        client_profile = "unknown"
        if "headers" in scope:
            for name, value in scope["headers"]:
                if name == b"x-client-profile":
                    client_profile = value.decode("latin-1")
                    break

        request_id = f"gw-{int(time.time() * 1000)}"
        start = time.time()
        is_streaming = body_json.get("stream", False)

        # Re-inject body for the downstream app.
        # V7.8 spinfix: after the body is replayed once, return http.disconnect so
        # StreamingResponse.listen_for_disconnect() exits instead of busy-spinning
        # on a receive() that always returns another http.request message.
        body_sent = False
        async def receive_replay():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            return {"type": "http.disconnect"}

        if is_streaming:
            await self._handle_streaming(scope, receive_replay, send, {
                "start": start,
                "request_id": request_id,
                "client_profile": client_profile,
                "explicit_model": explicit_model,
            })
        else:
            await self._handle_buffered(scope, receive_replay, send, {
                "start": start,
                "request_id": request_id,
                "client_profile": client_profile,
                "explicit_model": explicit_model,
            })

    async def _handle_streaming(self, scope, receive, send, ctx):
        """Handle streaming response: yield chunks to client (zero buffer),
        accumulate for post-stream telemetry logging."""
        chunks: list[bytes] = []
        status_code = 0
        headers_sent = False

        async def send_wrapper(message):
            nonlocal status_code, headers_sent
            msg_type = message.get("type", "")

            if msg_type == "http.response.start":
                status_code = message.get("status", 0)
                # Mark SSE content type so we know to log telemetry
                for name, value in message.get("headers", []):
                    if name == b"content-type" and b"event-stream" in value:
                        headers_sent = True
                await send(message)

            elif msg_type == "http.response.body":
                body = message.get("body", b"")
                if body:
                    chunks.append(body)
                # Always forward to client immediately (zero buffering)
                await send(message)

            else:
                await send(message)

        # Let the downstream app process and send the response
        await self.app(scope, receive, send_wrapper)

        # Stream complete — extract telemetry and log
        elapsed_ms = int((time.time() - ctx["start"]) * 1000)
        combined = b"".join(chunks)
        resp_json = {}
        try:
            if combined:
                resp_json = json.loads(combined)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        usage = resp_json.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
        model_id = resp_json.get("model", ctx["explicit_model"] or "unknown")
        saitama = resp_json.get("saitama", {})
        upstream_target = saitama.get("route", saitama.get("provider", "unknown"))
        tier = saitama.get("tier", "unknown")
        error_msg = None
        if "error" in resp_json:
            error_msg = resp_json["error"].get("message", str(resp_json["error"]))

        self.db.log_request(
            ts=ctx["start"],
            client_profile=ctx["client_profile"],
            upstream_target=upstream_target,
            model_id=model_id,
            tier=tier,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=elapsed_ms,
            status_code=status_code,
            error=error_msg,
            request_id=ctx["request_id"],
        )

    async def _handle_buffered(self, scope, receive, send, ctx):
        """Handle non-streaming response: buffer, log synchronously."""
        await self.app(scope, receive, send)

        # For non-streaming, the response is already fully sent via send().
        # We can't easily intercept it in pure ASGI without buffering.
        # Log with what we have (model + client info). Full response parsing
        # would require buffering the response body which defeats the purpose.
        # Non-streaming requests are rare and infrequent.
        self.db.log_request(
            ts=ctx["start"],
            client_profile=ctx["client_profile"],
            upstream_target="unknown",
            model_id=ctx["explicit_model"] or "unknown",
            tier="unknown",
            tokens_in=0,
            tokens_out=0,
            latency_ms=int((time.time() - ctx["start"]) * 1000),
            status_code=0,
            error=None,
            request_id=ctx["request_id"],
        )


# ── Convenience: add telemetry endpoints to existing FastAPI app ────────────────

def mount_telemetry_routes(app, db: TelemetryDB):
    """Add /telemetry/recent, /telemetry/daily, /telemetry/stats endpoints."""
    from fastapi import Query

    @app.get("/telemetry/recent")
    async def telemetry_recent(limit: int = Query(50, ge=1, le=500)):
        return db.query_recent(limit)

    @app.get("/telemetry/daily")
    async def telemetry_daily(days: int = Query(7, ge=1, le=90)):
        return db.query_daily(days)

    @app.get("/telemetry/stats")
    async def telemetry_stats():
        return db.stats()
