"""
saitama_gateway.py — Omnirouter-tiered gateway (Saitama mesh central).
V5.7.0 — Nous Portal Direct Routing + V5.7.1 deep-thinking passthrough (V6.1.1, 2026-08-30).

Implements the vault "Omnirouter Tiered routing Openrouter.md" tier stack behind
an OpenAI-compatible /v1 surface bound to the private Headscale mesh interface
(100.64.0.0/10). Provider keys are read ONLY from the process env (the operator
stages them in a 0600 .env on Saitama; none are in code or git).

V5.7.0 Tiers (per vault spec 2026-08-28 revision):
  Tier 0 (Primary Orchestration, Execution & Cheap Base):
                     NousResearch/hermes-4-405b (via Nous Portal inference-api),
                     xiaomi/mimo-v2.5 (high-speed baseline, lightweight triage),
                     z-ai/glm-5.3-flash (complex reasoning, heavy SWE diffs)
  Tier 1 (Low-Latency Streaming & Logic Criticism):
                     deepseek/deepseek-v4-flash-vision-exp (via Nous Portal),
                     openai/gpt-5.6-luna (logic criticism, schema checks ~$0.17)
  Tier 2 (Smarter Utility & Repo Engineering):
                     deepseek/deepseek-v4-pro (via Nous Portal),
                     openai/gpt-5.6-terra, google/gemini-3.7-flash
  Tier 3 (Heavy Hitters & Closers):
                     moonshotai/kimi-k3 (via Nous Portal),
                     openai/gpt-5.6-sol, x-ai/grok-4.6 (via Nous Portal)
  Multimodal Fallback Chain (OpenRouter):
                     xiaomi/mimo-v2.5, z-ai/glm-5.3-flash, google/gemini-3.7-flash,
                     deepseek/deepseek-v4-flash-vision-exp
  BYOK Perception (passthrough, keys staged 0600 on Saitama only):
                     Groq (Whisper STT /v1/audio/transcriptions)

Endpoints:
  GET  /v1/models                    tier catalog (ready/blocked flags)
  POST /v1/chat/completions          routed completion through cascade + failover
  GET  /health                       liveness for mesh connectivity probes

Run:  .venv/bin/python saitama_gateway.py   (binds 100.64.0.3:8000)
Env:  SAITAMA_GW_HOST (default 100.64.0.3), SAITAMA_GW_PORT (default 8000),
      OPENROUTER_API_KEY (master key, staged in .env only),
      NOUS_API_KEY (Nous Portal key, staged in .env only)
"""
import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger("saitama-gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [saitama-gw] %(levelname)s %(message)s")

HOST = os.getenv("SAITAMA_GW_HOST", "100.64.0.3")
PORT = int(os.getenv("SAITAMA_GW_PORT", "8000"))
OR = "https://openrouter.ai/api/v1/chat/completions"
NOUS_PORTAL_URL = "https://inference-api.nousresearch.com/v1/chat/completions"
GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def env(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    if not v:
        return ""
    v = v.strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        v = v[1:-1]
    return v.strip()


# --- tiered stack from the vault Omnirouter spec (V5.7 Tiering) ------------
# Models prefixed with "Nous Portal" route directly to inference-api.nousresearch.com
# using NOUS_API_KEY. All others route via OpenRouter using OPENROUTER_API_KEY.
_nous_key = env("NOUS_API_KEY")
_or_key = env("OPENROUTER_API_KEY")

TIERS: Dict[str, List[Dict[str, Any]]] = {
    "tier0": [
        # V6.3.4 D4.2: upstream Nous Portal route for hermes-4-405b was RETIRED
        # (upstream 404 "model has been retired" on every request, 57/57 failures).
        # Alias kept in tier0, routed via OpenRouter (nousresearch/hermes-4-405b).
        {"name": "hermes-4-405b", "url": OR, "key": _or_key,
         "model": "nousresearch/hermes-4-405b", "local": False,
         "provider": "openrouter"},
        {"name": "mimo-v2.5", "url": OR, "key": _or_key,
         "model": "xiaomi/mimo-v2.5", "local": False, "vision": True},
        {"name": "glm-5.3-flash", "url": OR, "key": _or_key,
         "model": "z-ai/glm-5.3-flash", "local": False, "vision": True},
    ],
    "tier1": [
        {"name": "deepseek-vision-exp", "url": NOUS_PORTAL_URL, "key": _nous_key,
         "model": "deepseek/deepseek-v4-flash-vision-exp", "local": False,
         "vision": True, "provider": "nous-portal",
         "fallback_url": OR, "fallback_key": _or_key},
        {"name": "gpt-luna", "url": OR, "key": _or_key,
         "model": "openai/gpt-5.6-luna", "local": False},
    ],
    "tier2": [
        {"name": "deepseek-pro", "url": NOUS_PORTAL_URL, "key": _nous_key,
         "model": "deepseek/deepseek-v4-pro", "local": False, "provider": "nous-portal",
         "fallback_url": OR, "fallback_key": _or_key},
        {"name": "gpt-terra", "url": OR, "key": _or_key,
         "model": "openai/gpt-5.6-terra", "local": False},
        {"name": "gemini-flash", "url": OR, "key": _or_key,
         "model": "google/gemini-3.7-flash", "local": False, "vision": True},
    ],
    "tier3": [
        {"name": "kimi-k3", "url": NOUS_PORTAL_URL, "key": _nous_key,
         "model": "moonshotai/kimi-k3", "local": False, "provider": "nous-portal",
         "fallback_url": OR, "fallback_key": _or_key},
        {"name": "gpt-sol", "url": OR, "key": _or_key,
         "model": "openai/gpt-5.6-sol", "local": False},
        {"name": "grok-4.6", "url": NOUS_PORTAL_URL, "key": _nous_key,
         "model": "x-ai/grok-4.6", "local": False, "provider": "nous-portal",
         "fallback_url": OR, "fallback_key": _or_key},
    ],
    "vision": [
        {"name": "mimo-vision", "url": OR, "key": _or_key,
         "model": "xiaomi/mimo-v2.5", "local": False, "vision": True},
        {"name": "glm-flash-vision", "url": OR, "key": _or_key,
         "model": "z-ai/glm-5.3-flash", "local": False, "vision": True},
        {"name": "gemini-flash-vision", "url": OR, "key": _or_key,
         "model": "google/gemini-3.7-flash", "local": False, "vision": True},
        {"name": "deepseek-vision-exp", "url": NOUS_PORTAL_URL, "key": _nous_key,
         "model": "deepseek/deepseek-v4-flash-vision-exp", "local": False,
         "vision": True, "provider": "nous-portal",
         "fallback_url": OR, "fallback_key": _or_key},
    ],
}

ORDER = ["tier0", "tier1", "tier2", "tier3"]

logger.info("V5.7.0 boot: NOUS_API_KEY=%s, OPENROUTER_API_KEY=%s",
            "staged" if _nous_key else "MISSING",
            "staged" if _or_key else "MISSING")
if _nous_key:
    logger.info("Nous Portal direct routing enabled: %s", NOUS_PORTAL_URL)


def provider_ready(up: Dict[str, Any]) -> bool:
    if up.get("local"):
        return True
    return bool(up.get("key"))


def select(tier: str, explicit: Optional[str] = None) -> Dict[str, Any]:
    """Pick the first ready provider in the requested tier, else cheaper tiers."""
    if explicit:
        low = explicit.lower()
        for tname, provs in TIERS.items():
            for up in provs:
                if low in (up.get("name", ""), str(up.get("model", ""))):
                    if provider_ready(up):
                        s = dict(up); s["tier"] = tname; return s
    order = [tier] + [x for x in ORDER if x != tier] if tier in ORDER else ORDER
    for t in order:
        for up in TIERS.get(t, []):
            if provider_ready(up):
                s = dict(up); s["tier"] = t
                return s
    raise RuntimeError("no ready provider (set OPENROUTER_API_KEY or NOUS_API_KEY)")


def classify(prompt: str) -> str:
    p = (prompt or "").lower()
    if any(w in p for w in ("sweeping refactor", "migration", "audit", "ultrathink")):
        return "tier3"
    if any(w in p for w in ("scrape", "search", "fetch", "extract", "parse", "ocr", "vision", "image")):
        return "tier2"
    return "tier0"  # cheap-base default (hermes-4-405b / mimo / glm)


_DEEP_CUES = ("think deeply", "deep think", "think harder", "ultrathink",
              "think step by step", "reason step by step", "reason carefully",
              "chain of thought")


def deep_thinking_requested(messages: List[Dict]) -> bool:
    """True when the caller asks for deliberate reasoning (V6.1.1 deep-thinking)."""
    txt = " ".join(str(m.get("content", "")) for m in messages
                   if isinstance(m.get("content"), str)).lower()
    return any(c in txt for c in _DEEP_CUES)


def catalog() -> List[Dict[str, str]]:
    out = []
    for t, provs in TIERS.items():
        for up in provs:
            out.append({"id": up["name"], "tier": t, "model": str(up.get("model", "")),
                        "ready": str(provider_ready(up)).lower(),
                        "vision": str(bool(up.get("vision"))).lower(),
                        "provider": up.get("provider", "openrouter")})
    return out


async def _post(up: Dict[str, Any], body: Dict) -> Any:
    """POST to upstream with fallback support for Nous Portal models."""
    import httpx
    url = up["url"]
    key = up.get("key", "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        async with asyncio.timeout(150):
            async with httpx.AsyncClient(timeout=150) as client:
                r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        return r.json()
    except Exception as e:
        # If this is a Nous Portal request and it fails, try OpenRouter fallback
        if up.get("fallback_url") and up.get("fallback_key"):
            logger.warning("Nous Portal %s failed (%s), falling back to OpenRouter", up["name"], e)
            fb_url = up["fallback_url"]
            fb_key = up["fallback_key"]
            fb_headers = {"Content-Type": "application/json", "Authorization": f"Bearer {fb_key}"}
            fb_body = {k: v for k, v in body.items()
                       if k not in ("enable_thinking", "reasoning_effort")}
            async with asyncio.timeout(150):
                async with httpx.AsyncClient(timeout=150) as client:
                    r = await client.post(fb_url, headers=fb_headers, json=fb_body)
            if r.status_code >= 400:
                raise RuntimeError(f"OpenRouter fallback also failed: HTTP {r.status_code}")
            data = r.json()
            data["_fallback"] = "openrouter"
            return data
        raise


async def completion(messages: List[Dict], explicit: Optional[str],
                     vision: bool = False, tools: Optional[List[Dict]] = None,
                     tool_choice: Optional[Any] = None,
                     deep_thinking: bool = False) -> Dict[str, Any]:
    prompt = "".join(str(m.get("content", "")) for m in reversed(messages)
                     if m.get("role") in ("user", "system")) or "hello"
    tier = "vision" if vision else (explicit if explicit in TIERS else classify(prompt))
    sel = select(tier, explicit)
    last_err = None
    # failover: primary tier providers first (cheapest in tier), then other tiers
    seen = {sel.get("name")}
    candidates = [sel]
    primary_provs = TIERS.get(tier, [])
    for up in primary_provs:
        if up.get("name") not in seen and provider_ready(up):
            candidates.append(up); seen.add(up.get("name"))
    for t in ORDER:
        if t == tier or t == "vision":
            continue
        for up in TIERS.get(t, []):
            if up.get("name") not in seen and provider_ready(up):
                candidates.append(up); seen.add(up.get("name"))
    for up in candidates:
        body = {"model": up["model"], "messages": messages, "stream": False}
        if tools:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if deep_thinking and up.get("provider") == "nous-portal":
            # accepted by inference-api.nousresearch.com — probe-verified 200 (V6.1.1)
            body["enable_thinking"] = True
            body["reasoning_effort"] = "high"
        try:
            data = await _post(up, body)
            choice = data["choices"][0]
            msg = choice.get("message", {}) or {}
            provider_info = up.get("provider", "openrouter")
            if data.get("_fallback"):
                provider_info = f"nous-portal->openrouter"
            return {"id": f"saitama-gw-{up['name']}", "object": "chat.completion",
                    "model": up["model"], "tier": up.get("tier", tier),
                    "choices": [{"index": 0,
                                 "finish_reason": choice.get("finish_reason", "stop"),
                                 "message": {"role": "assistant",
                                             "content": msg.get("content"),
                                             "reasoning": msg.get("reasoning"),
                                             "tool_calls": msg.get("tool_calls")}}],
                    "usage": data.get("usage", {}),
                    "saitama": {"route": up["name"], "tier": up.get("tier", tier),
                                "provider": provider_info,
                                "deep_thinking": bool(deep_thinking and up.get("provider") == "nous-portal")}}
        except Exception as e:
            last_err = f"{up['name']}: {e}"
            logger.warning("failover %s failed: %s", up["name"], e)
    raise RuntimeError(f"all upstreams failed; last={last_err}")


def temp_payload():
    return {}


# --- HTTP surface -----------------------------------------------------------
try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    app = FastAPI(title="Saitama Omnirouter Gateway", version="5.7.1")

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok", "gateway": "saitama", "version": "5.7.1",
                             "host": HOST, "port": PORT,
                             "nous_portal": "enabled" if _nous_key else "disabled"})

    @app.get("/v1/models")
    async def models():
        return JSONResponse({"object": "list", "data": catalog()})

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request):
        """Groq BYOK STT passthrough (vault V5.4 spec: STT/Audio = Groq Whisper)."""
        groq_key = env("GROQ_API_KEY")
        if not groq_key:
            return JSONResponse({"error": {"message": "GROQ_API_KEY not staged on gateway",
                                           "type": "config_error", "code": 503}},
                                status_code=503)
        import httpx
        ctype = request.headers.get("content-type", "")
        if "multipart/form-data" not in ctype:
            return JSONResponse({"error": {"message": "expected multipart/form-data",
                                           "type": "invalid_request_error", "code": 400}}, status_code=400)
        form = await request.form()
        up = form.get("file")
        if up is None:
            return JSONResponse({"error": {"message": "missing file field",
                                           "type": "invalid_request_error", "code": 400}}, status_code=400)
        data = await up.read()
        model = str(form.get("model") or "whisper-large-v3")
        files = {"file": (up.filename or "audio.bin", data, up.content_type or "application/octet-stream")}
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(GROQ_STT_URL, headers={"Authorization": f"Bearer {groq_key}"},
                                      data={"model": model}, files=files)
            return JSONResponse(r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse({"error": {"message": str(e), "type": "upstream_error", "code": 503}}, status_code=503)

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": {"message": "invalid json", "type": "invalid_request_error", "code": 400}}, status_code=400)
        mm = payload.get("messages") or []
        is_vision = any(
            "data:image/" in str(m.get("content", ""))
            or "http" in str(m.get("content", "")).lower()
            or (isinstance(m.get("content"), list)
                and any(part.get("type") == "image_url" for part in m.get("content", [])))
            for m in mm
        )
        explicit = str(payload.get("model") or "") or None
        want_stream = bool(payload.get("stream", False))
        deep_thinking = bool(payload.get("enable_thinking") or payload.get("reasoning_effort")) \
            or deep_thinking_requested(mm)
        try:
            result = await completion(mm, explicit, vision=is_vision,
                                      tools=payload.get("tools"),
                                      tool_choice=payload.get("tool_choice"),
                                      deep_thinking=deep_thinking)
            if not want_stream:
                return JSONResponse(result)
            # SSE streaming for OpenAI-compatible clients
            from fastapi.responses import StreamingResponse
            content = result["choices"][0]["message"].get("content") or ""
            reasoning_txt = result["choices"][0]["message"].get("reasoning") or ""
            tool_calls = result["choices"][0]["message"].get("tool_calls") or []
            finish = result["choices"][0].get("finish_reason") or "stop"
            rid = result.get("id", "saitama-gw")
            model = result.get("model", "auto")
            tier = result.get("tier", "")
            import time as _t
            async def gen():
                yield f"data: {json.dumps({'id': rid, 'object': 'chat.completion.chunk', 'created': int(_t.time()), 'model': model, 'tier': tier, 'saitama': result.get('saitama', {}) if isinstance(result.get('saitama'), dict) else {}, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"
                if reasoning_txt:
                    yield f"data: {json.dumps({'id': rid, 'object': 'chat.completion.chunk', 'created': int(_t.time()), 'model': model, 'tier': tier, 'choices': [{'index': 0, 'delta': {'reasoning': reasoning_txt}, 'finish_reason': None}]})}\n\n"
                if content:
                    yield f"data: {json.dumps({'id': rid, 'object': 'chat.completion.chunk', 'created': int(_t.time()), 'model': model, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]})}\n\n"
                for tc in tool_calls:
                    fn = tc.get("function", {}) or {}
                    delta = {"tool_calls": [{
                        "index": tc.get("index", 0),
                        "id": tc.get("id"),
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": fn.get("name"),
                            "arguments": fn.get("arguments", ""),
                        },
                    }]}
                    yield f"data: {json.dumps({'id': rid, 'object': 'chat.completion.chunk', 'created': int(_t.time()), 'model': model, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]})}\n\n"
                yield f"data: {json.dumps({'id': rid, 'object': 'chat.completion.chunk', 'created': int(_t.time()), 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        except Exception as e:
            return JSONResponse({"error": {"message": str(e), "type": "upstream_error", "code": 503}}, status_code=503)

    def main():
        import uvicorn
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
except ImportError:
    raise SystemExit("fastapi not installed: .venv/bin/pip install -q fastapi uvicorn httpx")

if __name__ == "__main__":
    main()
