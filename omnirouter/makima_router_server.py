"""
makima_router_server.py — OpenAI-compatible tier router for the Makima WebUI.

Exposes the 3-tier cost-first array (app/makima_router.py route engine) behind an
OpenAI-compatible /v1/chat/completions + /v1/models surface so the Hermes default
profile (Makima dashboard / WebUI) can be pointed at it as a `custom` provider.
Every request traverses Tier 0 (NVIDIA NIM free / Groq free / local Ollama) FIRST
and only climbs to Tier 1/2 when the query calls for it or Tier 0 fails over.

Endpoints:
  GET  /v1/models                -> tier catalog (ready/blocked flags)
  POST /v1/chat/completions      -> routes a completion through the tier engine

Run:  venv/bin/python makima_router_server.py  (default 127.0.0.1:12850)
Env (all optional): MAKIMA_ROUTER_HOST, MAKIMA_ROUTER_PORT, and the same tier
provider keys the router reads (NVIDIA_NIM API KEY, GROQ, MISTRAL, OPENROUTER,
OLLAMA_URL).
"""
import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import makima_router as MR  # noqa: E402  (tier route engine)

logger = logging.getLogger("makima_router_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [makima-router] %(levelname)s %(message)s")

HOST = os.getenv("MAKIMA_ROUTER_HOST", "127.0.0.1")
PORT = int(os.getenv("MAKIMA_ROUTER_PORT", "12850"))


def tier_catalog() -> List[Dict[str, str]]:
    """Public /v1/models parity view (ready tiers first, cheapest first)."""
    return MR.list_models()


def _route_for(prompt: str, explicit: Optional[str]) -> Dict[str, Any]:
    """Resolve the upstream via the router engine (tier0-first fallthrough)."""
    sel = MR.route_model(prompt or "hello", explicit)
    sel.pop("key", None)  # never leak keys on the wire
    return sel


async def chat_completion(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run a chat completion through the tier engine with failover across the
    alive upstream list (404/5xx/timeout on one slug -> next in tier)."""
    # Normalize OpenAI request into the router's caller shape
    messages: List[Dict] = payload.get("messages") or []
    prompt = ""
    for m in reversed(messages):
        if m.get("role") in ("user", "system") and isinstance(m.get("content"), str):
            prompt = m["content"]
            break
    explicit = str(payload.get("model") or "") or None

    # If a named model was requested, honor it only if already in the array;
    # otherwise treat it as the tier hint (tier0/tier1/tier2/local/...).
    if explicit and explicit not in ("auto", "tier0", "tier1", "tier2", "local",
                                     "free", "ambient", "tool", "workhorse", "deep"):
        known = {p["id"] for p in tier_catalog()}
        if explicit not in known:
            explicit = None  # unknown slug -> normal tier routing (not a 404)

    # Candidate upstream order: requested tier first, then cheaper, then others,
    # just like route engine -> but we walk ALL alive upstreams for failover.
    sel = _route_for(prompt, explicit)
    first = (sel.get("name"), sel.get("model"))
    order = [sel]  # primary
    seen = {sel.get("name")}
    # append the rest (cheapest-first tiers) as failover candidates
    for tname in ("tier0", "tier1", "tier2"):
        for up in MR.TIERS.get(tname, []):
            if up.get("name") not in seen and MR.provider_ready(up) and not up.get("requires_approval"):
                order.append(dict(up))
                seen.add(up.get("name"))

    last_err = None
    for up in order:
        key = up.get("key", "")
        url = up.get("url", "")
        model = up.get("model", "")
        # local keyless is fine; remote requires a key
        if not up.get("local") and not key:
            continue
        body = {"model": model, "messages": messages, "stream": False,
                **(payload.get("temperature") is not None and {"temperature": payload.get("temperature")} or {})}
        headers = {"Content-Type": "application/json",
                   **({"Authorization": f"Bearer {key}"} if key else {})}
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(url, headers=headers, json=body)
            if r.status_code >= 400:
                raise httpx.HTTPStatusError(f"{model}: HTTP {r.status_code}", request=None, response=r)
            data = r.json()
            chosen = data["choices"][0]["message"]["content"]
            return {
                "id": f"makima-tier-{up['name']}",
                "object": "chat.completion",
                "model": model,
                "tier": up.get("tier", ""),
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": chosen}}],
                "usage": data.get("usage", {}),
                "makima": {"route": up["name"], "tier": up.get("tier", "")},
            }
        except Exception as e:  # noqa: BLE001 - any upstream failure -> fail over
            last_err = f"{model}: {e}"
            logger.warning("tier failover %s -> %s failed: %s", up.get("name"), model, e)
            continue

    raise RuntimeError(f"no alive Makima tier upstream (primary={first}); last error: {last_err}")


# --- minimal JSON-RPC-style routing for a small stdlib server -----------------
# We use FastAPI if available (the Makima venv has it); fall back to a tiny
# stdlib handler so this also runs anywhere.

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    app = FastAPI(title="Makima Tier Router", version="1.0.0")

    @app.get("/v1/models")
    async def models():
        return JSONResponse({"object": "list", "data": [
            {"id": m["id"], "object": "model", "owned_by": "makima-tier-router",
             "tier": m["tier"], "ready": m["ready"], "ceiling_blocked": m["ceiling_blocked"],
             "vision": m["vision"], "model": m["model"]} for m in tier_catalog()]})

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": {"message": "invalid json", "type": "invalid_request_error", "code": 400}}, status_code=400)
        if payload.get("stream"):
            return StreamingResponse(_sse_completion(payload), media_type="text/event-stream")
        try:
            result = await chat_completion(payload)
            return JSONResponse(result)
        except Exception as e:  # noqa: BLE001
            logger.error("chat completion failed: %s", e)
            return JSONResponse({"error": {"message": str(e), "type": "upstream_error", "code": 503}}, status_code=503)

    async def _sse_completion(payload: Dict[str, Any]):
        try:
            result = await chat_completion(payload)
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'upstream_error', 'code': 503}})}\n\n"
            yield "data: [DONE]\n\n"
            return
        content = result["choices"][0]["message"]["content"]
        model = result.get("model", "")
        rid = result["id"]
        # role chunk
        yield f"data: {json.dumps({'id': rid, 'object': 'chat.completion.chunk', 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"
        # content chunk(s)
        for i in range(0, len(content) or 1, 64):
            piece = content[i:i + 64]
            yield f"data: {json.dumps({'id': rid, 'object': 'chat.completion.chunk', 'model': model, 'choices': [{'index': 0, 'delta': {'content': piece}, 'finish_reason': None}]})}\n\n"
        # finish chunk
        yield f"data: {json.dumps({'id': rid, 'object': 'chat.completion.chunk', 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"

    def main():
        import uvicorn
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")

except ImportError:
    # stdlib fallback
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, obj: dict):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/v1/models"):
                self._send(200, {"object": "list", "data": [
                    {"id": m["id"], "object": "model", "tier": m["tier"], "ready": m["ready"],
                     "ceiling_blocked": m["ceiling_blocked"], "model": m["model"]} for m in tier_catalog()]})
            else:
                self._send(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if not self.path.startswith("/v1/chat/completions"):
                self._send(404, {"error": {"message": "not found"}}); return
            n = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                result = asyncio.run(chat_completion(payload))
                self._send(200, result)
            except Exception as e:  # noqa: BLE001
                self._send(503, {"error": {"message": str(e), "type": "upstream_error", "code": 503}})

    def main():
        ThreadingHTTPServer((HOST, PORT), _Handler).serve_forever()


if __name__ == "__main__":
    main()