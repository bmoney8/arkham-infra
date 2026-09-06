"""V7.9 D1 verification gate — pytest suite for the OmniRouter telemetry
streaming-disconnect hotfix (spinfix) in telemetry_middleware.py.

Regression surface (V7.8 total-spin incident, see
makima-homelab-ops/references/v78b-resume-run-lessons.md):

  Pre-fix receive_replay() returned http.request with the same body on EVERY
  call. Starlette's StreamingResponse.listen_for_disconnect() loops
  `message = await receive()` until it sees http.disconnect — with an
  infinite-request receive it busy-polls forever, pinning the event loop on
  any streamed completion. The spinfix returns http.disconnect after the
  single body replay. These tests lock that contract in.

NOTE: TelemetryDB(":memory:") is unusable by design (each sqlite3.connect
opens a PRIVATE :memory: db, so _init_db's tables are invisible to the
logging connections). All tests here use tmp_path file DBs.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from starlette.responses import StreamingResponse
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "omnirouter"))

from telemetry_middleware import TelemetryDB, TelemetryMiddleware  # noqa: E402


# ── pure-ASGI receive_replay contract ────────────────────────────────────────


def _captured_receive_replay(tmp_path):
    """Reconstruct the middleware's receive_replay exactly as __call__ builds
    it, by driving a minimal scope through the real middleware with a stub
    app that captures the receive callable it is handed."""
    captured = {}

    async def stub_app(scope, receive, send):
        captured["receive"] = receive

    async def noop_send(message):
        pass

    db = TelemetryDB(str(tmp_path / "tel.db"))
    mw = TelemetryMiddleware(stub_app, db)
    scope = {
        "type": "http",
        "path": "/v1/chat/completions",
        "headers": [],
        "method": "POST",
    }
    body = json.dumps({"model": "mimo-v2.5", "stream": True}).encode()

    async def replay_source():
        yield {"type": "http.request", "body": body, "more_body": False}

    async def run():
        gen = replay_source()

        async def receive():
            return await gen.__anext__()

        await mw(scope, receive, noop_send)

    asyncio.run(run())
    return captured["receive"]


def test_receive_replay_first_call_returns_body(tmp_path):
    receive = _captured_receive_replay(tmp_path)
    msg = asyncio.run(receive())
    assert msg["type"] == "http.request"
    assert msg["body"]  # non-empty body replayed


def test_receive_replay_second_call_returns_disconnect_SPINFIX(tmp_path):
    """THE regression test: pre-spinfix this returned another http.request,
    which starved listen_for_disconnect into a busy spin. Must now be
    http.disconnect."""
    receive = _captured_receive_replay(tmp_path)
    asyncio.run(receive())  # first call = body
    for _ in range(10):  # every subsequent call, not just the 2nd
        msg = asyncio.run(receive())
        assert msg["type"] == "http.disconnect"


def test_listen_for_disconnect_terminates_on_spinfix_receive(tmp_path):
    """End-to-end proof: starlette's own listen_for_disconnect must EXIT
    against the middleware-provided receive (would hang pre-fix)."""
    receive = _captured_receive_replay(tmp_path)
    asyncio.run(receive())  # consume the body replay

    async def scenario():
        await asyncio.wait_for(
            StreamingResponse(iter([])).listen_for_disconnect(receive),
            timeout=2.0,
        )

    asyncio.run(scenario())  # raises TimeoutError (test fail) if it spins


# ── full-stack regression: streamed completion through the middleware ────────


def _make_client(tmp_path):
    """Starlette app with an SSE endpoint wrapped in TelemetryMiddleware."""
    db = TelemetryDB(str(tmp_path / "tel.db"))

    async def sse(request):
        # Single JSON body chunk — the only shape the middleware's token
        # extraction actually parses (SSE "data: ..." text fails json.loads
        # silently and logs tokens_in/tokens_out as 0; production telemetry.db
        # confirms 0 tokens on every streamed row, so this is pre-existing
        # behavior, NOT a spinfix regression).
        body = json.dumps({
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            "model": "mimo-v2.5",
            "saitama": {"route": "phase1", "tier": "free"},
        }).encode()

        async def gen():
            yield body

        return StreamingResponse(gen(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/v1/chat/completions", sse, methods=["POST"])])
    return TestClient(TelemetryMiddleware(app, db)), db


def test_streamed_completion_completes_and_logs(tmp_path):
    """Full-stack: a streaming request must complete (not spin), return the
    untouched SSE body, and land exactly one telemetry row."""
    client, db = _make_client(tmp_path)
    r = client.post(
        "/v1/chat/completions",
        json={"model": "mimo-v2.5", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    assert b"prompt_tokens" in r.content
    rows = db.query_recent()
    assert len(rows) == 1
    assert rows[0]["tokens_in"] == 11 and rows[0]["tokens_out"] == 7
    assert rows[0]["model_id"] == "mimo-v2.5"
    assert rows[0]["upstream_target"] == "phase1"
    assert rows[0]["status_code"] == 200


def test_non_chat_path_passes_through_untouched(tmp_path):
    db = TelemetryDB(str(tmp_path / "tel.db"))

    async def health(request):
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/health", health, methods=["GET"])])
    client = TestClient(TelemetryMiddleware(app, db))
    r = client.get("/health")
    assert r.status_code == 200
    assert db.query_recent() == []  # no telemetry row for pass-through paths


def test_spinfix_present_in_deployed_source():
    """Static guard: the deployed-source copy carries the spinfix marker and
    returns http.disconnect from receive_replay."""
    src = (Path(__file__).resolve().parent.parent / "omnirouter" / "telemetry_middleware.py").read_text()
    assert "http.disconnect" in src
    assert "V7.8 spinfix" in src


def test_deployed_source_matches_live_saitama_copy():
    """Artifact authority: the repo copy must be byte-identical to the live
    file running on saitama (/workspace/omnirouter/telemetry_middleware.py).
    md5 of the live file is pinned here; a mismatch = deploy drift."""
    import hashlib
    LIVE_MD5 = "0e92b5950fa7aba41a6d4bf4b23c11ca"
    src = (Path(__file__).resolve().parent.parent / "omnirouter" / "telemetry_middleware.py").read_bytes()
    assert hashlib.md5(src).hexdigest() == LIVE_MD5
