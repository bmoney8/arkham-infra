"""
saitama_gateway.py — Omnirouter functional-bucket gateway (Saitama mesh central).
V9.00-blueprint — OpenRouter-locked functional routing (Telephone Packet V9.00 D1, 2026-09-13).

SOURCE OF TRUTH: makima-vault/Makima files/Omnirouter Tiered routing Openrouter.md
Target: /workspace/omnirouter/saitama_gateway.py on Saitama (100.64.0.3:8000)

Provider policy: 100% OpenRouter for paid execution. ZERO Nous Portal dependencies
(no Nous URLs, no Nous keys anywhere in this process).

FUNCTIONAL BUCKETS (paid OpenRouter primary) — spec sections 1-6
  chat              B1 Default Multimodal Chat Ingestion (default entry gate)
  coding            B2 Coding & Terminal SWE
  agentic           B3 Autonomous Agentic & Cron Runner
  reasoning         B4 Deep Reasoning & Audit
  heavy_multimodal  B5 Heavy Multimodal (large docs & media)
  audio_edge        B6 Voice & Audio Edge (Edge TTS cloud / Piper local / Groq Whisper STT)
  free_local        Free / Local / Testing pool — sub-agents + 429/5xx failover ONLY

ROUTING LOGIC — spec sections 7-10
  7. Explicit match   : request names an exact model id / arm name -> route straight to it
  8. Functional alias : request names a bucket (chat|coding|agentic|reasoning|
                        heavy_multimodal) -> route to that bucket's primary
  9. Default fallback : no model or unknown id -> Bucket 1 Primary: deepseek/deepseek-v4.1-flash
 10. Error cascade    : 429/5xx/timeout walks bucket secondary/tertiary, then escalates
                        across OpenRouter, before hitting free/local fallbacks

Backward-compatibility aliases (existing mesh clients) — see ALIASES:
  phase1/free -> free_local ; phase2/paid/tier0 -> chat ; tier1 -> agentic ;
  tier2 -> coding ; tier3 -> reasoning ; auto/default -> chat.
  A leading "custom/" or "openrouter/" vendor prefix is stripped before matching, so
  `custom/deepseek-v4.1-flash` resolves explicitly to the chat bucket primary.

Provider keys are read ONLY from the process env (staged 0600 in the Saitama .env;
none are in code or git): OPENROUTER_API_KEY, GOOGLE_AI_STUDIO_KEY,
NVIDIA_NIM_API_KEY, GROQ_API_KEY, HETZNER_INFERENCE_TOKEN

Endpoints:
  GET  /health                       liveness + bucket readiness
  GET  /v1/models                    full catalog incl. bucket + readiness flags
  GET  /v1/buckets                   functional bucket manifest (routing contract)
  POST /v1/chat/completions          bucket-routed completion + cascade failover
  POST /v1/embeddings                NVIDIA NIM BYOK embeddings passthrough
  POST /v1/audio/speech              B6 TTS: Piper local (default) / Edge TTS cloud
  POST /v1/audio/transcriptions      B6 STT: Groq Whisper passthrough
  GET  /circuit-breaker/stats        failure/escalation telemetry

Prior art: V5.17 2-phase routing, V8.03-b/c fluid escalation, V7.11 D2 embeddings proxy.
"""
import asyncio
import hashlib
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("saitama-gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [saitama-gw] %(levelname)s %(message)s")

GATEWAY_VERSION = "9.04-waterfall"
logger.info("V9.04-waterfall: strict per-bucket tier waterfall (T1 free/BYOK entry, T2 on 429/TTFT>8s/schema trigger, T3 closer after two T2 failures or explicit multi-file production diff), SAAR session pinning holds the elevated tier, routing receipts to /var/log/omnirouter/routing.log; token-optimizer/schema pruning remains Hermes-side")
logger.info("V9.02-failfast: functional-bucket + payload modality gate + semantic embedding router + 4xx fail-fast; Nous Portal fully evicted; paid pool locked to OpenRouter")
logger.info("V9.03-saar: session-aware agentic routing — per-session arm affinity pins a multi-step tool loop to one upstream (KV-cache warm); advisory only, never blocks failover")

HOST = os.getenv("SAITAMA_GW_HOST", "100.64.0.3")
PORT = int(os.getenv("SAITAMA_GW_PORT", "8000"))

# --- upstreams ---------------------------------------------------------------
OR = "https://openrouter.ai/api/v1/chat/completions"
GOOGLE_CHAT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
NVIDIA_CHAT = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"
GROQ_CHAT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
HETZNER_CHAT = "https://inference.hetzner.com/api/v1/chat/completions"

# B6 audio edge endpoints
PIPER_SPEECH_URL = os.getenv("PIPER_SPEECH_URL", "http://100.64.0.3:8020/v1/audio/speech")
PIPER_DEFAULT_VOICE = os.getenv("PIPER_DEFAULT_VOICE", "en-GB-Alba-Medium")
EDGE_TTS_VOICES = ("en-AU-NatashaNeural", "en-AU-WilliamNeural", "en-US-JennyNeural")
EDGE_TTS_DEFAULT_VOICE = "en-AU-NatashaNeural"
# Spec: "local Whisper fallback on Genos .5:8645" — endpoint is configurable; absent today.
STT_FALLBACK_URL = os.getenv("STT_FALLBACK_URL", "http://100.64.0.5:8645/v1/audio/transcriptions")


def env(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    if not v:
        return ""
    v = v.strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        v = v[1:-1]
    return v.strip()


from telemetry_middleware import TelemetryDB, TelemetryMiddleware, mount_telemetry_routes

# --- provider keys (env only; 0600 .env on Saitama) --------------------------
_or_key = env("OPENROUTER_API_KEY")
_google_key = env("GOOGLE_AI_STUDIO_KEY")
_nvidia_key = env("NVIDIA_NIM_API_KEY")
_groq_key = env("GROQ_API_KEY")
_hetzner_key = env("HETZNER_INFERENCE_TOKEN")

# --- arm dict shape ----------------------------------------------------------
# {"name","url","key","model","local","provider","vision"?,"reasoning"?, "bucket"}


def _or(name: str, model: str, vision: bool = False, reasoning: bool = False) -> Dict[str, Any]:
    """Paid OpenRouter arm. Every paid arm is locked to OpenRouter (no alternate base)."""
    return {"name": name, "url": OR, "key": _or_key, "model": model, "local": False,
            "provider": "openrouter", "vision": vision, "reasoning": reasoning}


# --- FUNCTIONAL BUCKETS (paid OpenRouter primary) ----------------------------
BUCKETS: Dict[str, List[Dict[str, Any]]] = {
    # B1 — Default Multimodal Chat Ingestion (DEFAULT ENTRY GATE)
    "chat": [
        _or("deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash", vision=True),   # PRIMARY
        _or("mimo-v2.5", "xiaomi/mimo-v2.5", vision=True),                          # SECONDARY (omnimodal)
        _or("gpt-5.6-luna", "openai/gpt-5.6-luna", vision=True),                    # TERTIARY (text+image+file)
    ],
    # B2 — Coding & Terminal SWE
    "coding": [
        _or("gpt-5.6-sol", "openai/gpt-5.6-sol", vision=True),                      # PRIMARY
        _or("kimi-k3", "moonshotai/kimi-k3", vision=True),                          # SECONDARY
        _or("glm-5.3-flash", "z-ai/glm-5.3-flash", vision=True),                    # FAST DIFF/EXEC
    ],
    # B3 — Autonomous Agentic & Cron Runner
    "agentic": [
        _or("gpt-5.6-terra", "openai/gpt-5.6-terra", vision=True),                  # PRIMARY
        _or("qwen3.8-max", "qwen/qwen3.8-max-0902", vision=True),                   # SECONDARY (strict JSON)
    ],
    # B4 — Deep Reasoning & Audit
    "reasoning": [
        _or("glm-5.3", "z-ai/glm-5.3", reasoning=True),                             # PRIMARY
        _or("opus-5", "anthropic/claude-opus-5", reasoning=True),                   # SECURITY/PROTOCOL AUDIT
        _or("grok-4.6", "x-ai/grok-4.6", vision=True, reasoning=True),              # EDGE-CASE REASONING
    ],
    # B5 — Heavy Multimodal (large docs & media)
    "heavy_multimodal": [
        _or("gemini-3.8-flash", "google/gemini-3.8-flash", vision=True),            # PRIMARY (omnimodal)
    ],
}

# --- FREE / LOCAL / TESTING POOL (sub-agents + 429/5xx failover ONLY) --------
FREE_LOCAL: List[Dict[str, Any]] = [
    # NVIDIA NIM BYOK (promoted to front — no 429 quota spikes)
    {"name": "nemotron-super-120b", "url": NVIDIA_CHAT, "key": _nvidia_key,
     "model": "nvidia/nemotron-3-super-120b-a12b", "local": False, "provider": "nvidia-nim"},
    {"name": "nim-kimi-k3", "url": NVIDIA_CHAT, "key": _nvidia_key,
     "model": "moonshotai/kimi-k3", "local": False, "provider": "nvidia-nim"},
    {"name": "nim-deepseek-v4-flash", "url": NVIDIA_CHAT, "key": _nvidia_key,
     "model": "deepseek-ai/deepseek-v4-flash-0731", "local": False, "provider": "nvidia-nim"},
    {"name": "nim-llama-3.2-90b-vision", "url": NVIDIA_CHAT, "key": _nvidia_key,
     "model": "meta/llama-3.2-90b-vision-instruct", "local": False,
     "provider": "nvidia-nim", "vision": True},
    # Groq BYOK
    {"name": "groq-gpt-oss-120b", "url": GROQ_CHAT, "key": _groq_key,
     "model": "openai/gpt-oss-120b", "local": False, "provider": "groq"},
    {"name": "groq-qwen38-27b", "url": GROQ_CHAT, "key": _groq_key,
     "model": "qwen/qwen3.8-27b", "local": False, "provider": "groq"},
    # Hetzner Inference BYOK
    {"name": "hetzner-qwen38-27b", "url": HETZNER_CHAT, "key": _hetzner_key,
     "model": "Qwen3.8-27B", "local": False, "provider": "hetzner-inference"},
    {"name": "hetzner-qwen36-35b", "url": HETZNER_CHAT, "key": _hetzner_key,
     "model": "Qwen/Qwen3.6-35B-A3B-FP8", "local": False, "provider": "hetzner-inference"},
    # OpenRouter :free
    {"name": "nemotron-3-ultra-free", "url": OR, "key": _or_key,
     "model": "nvidia/nemotron-3-ultra-550b-a55b:free", "local": False, "provider": "openrouter-free"},
    {"name": "nemotron-3.5-lightning-free", "url": OR, "key": _or_key,
     "model": "nvidia/nemotron-3.5-lightning:free", "local": False, "provider": "openrouter-free"},
    # Ollama local (Psykos 100.64.0.2:11434)
    {"name": "ollama-qwen3-8b", "url": "http://100.64.0.2:11434/v1/chat/completions", "key": "",
     "model": "qwen3:8b", "local": True, "provider": "ollama-local"},
    {"name": "ollama-llama3.1-8b", "url": "http://100.64.0.2:11434/v1/chat/completions", "key": "",
     "model": "llama3.1:8b", "local": True, "provider": "ollama-local"},
    {"name": "ollama-deepseek-r1-8b", "url": "http://100.64.0.2:11434/v1/chat/completions", "key": "",
     "model": "deepseek-r1:8b", "local": True, "provider": "ollama-local"},
    {"name": "ollama-hermes3-8b", "url": "http://100.64.0.2:11434/v1/chat/completions", "key": "",
     "model": "hermes3:8b", "local": True, "provider": "ollama-local"},
    {"name": "ollama-llava-7b", "url": "http://100.64.0.2:11434/v1/chat/completions", "key": "",
     "model": "llava:7b", "local": True, "provider": "ollama-local", "vision": True},
    # Google AI Studio BYOK (demoted below nemotron — persistent 429 quota spikes)
    {"name": "gemini-flash-free", "url": GOOGLE_CHAT, "key": _google_key,
     "model": "gemini-flash-latest", "local": False,
     "provider": "google-ai-studio", "vision": True},
    {"name": "gemini-pro-free", "url": GOOGLE_CHAT, "key": _google_key,
     "model": "gemini-pro-latest", "local": False, "provider": "google-ai-studio"},
]

# Retired in V9.00-blueprint (probe-verified against the live OpenRouter catalog 2026-09-13):
#   meituan/longcat-2.0:free   -> EVICTED with the Nous Portal removal (Nous-hosted arm)
#   minimax/minimax-m3:free    -> no longer in the OpenRouter catalog (only paid
#                                 minimax/minimax-m3 exists) -> dropped from the free pool
# Catalog correction applied (spec text vs live catalog):
#   qwen/qwen3.8-max -> qwen/qwen3.8-max-0902  (bare id absent from the live catalog)

BUCKET_ORDER_PAID = ["chat", "coding", "agentic", "reasoning", "heavy_multimodal"]
DEFAULT_BUCKET = "chat"          # spec section 9: Bucket 1 Primary
DEFAULT_ARM = "deepseek-v4.1-flash"

# Strict per-bucket tiers; model IDs follow the operator's routing table.
# Provider availability still requires live catalog/probe verification before deploy.
_ARM_REGISTRY = {u['name']: u for u in FREE_LOCAL}
_ARM_REGISTRY.update({u['name']: u for pool in BUCKETS.values() for u in pool})
_ARM_REGISTRY.update({
    'union-alpha': _or('union-alpha', 'stealth/union-alpha', vision=True),
    'laguna-free': _or('laguna-free', 'poolside/laguna-s-2.1:free'),
    'muse-spark': _or('muse-spark', 'meta/muse-spark-1.3-contributor'),
    'solar-pro4': _or('solar-pro4', 'upstage/solar-pro4', reasoning=True),
})
for _free_name in ('union-alpha', 'laguna-free'):
    _ARM_REGISTRY[_free_name]['provider'] = 'openrouter-free'
TIER_NAMES = {
    'chat': [('union-alpha', 'nemotron-3.5-lightning-free', 'hetzner-qwen38-27b', 'hetzner-qwen36-35b', 'groq-gpt-oss-120b'), ('mimo-v2.5', 'deepseek-v4.1-flash'), ('glm-5.3-flash',)],
    'coding': [('laguna-free', 'union-alpha', 'nim-kimi-k3', 'nim-deepseek-v4-flash'), ('glm-5.3-flash',), ('gpt-5.6-sol', 'kimi-k3')],
    'agentic': [('union-alpha', 'nemotron-super-120b'), ('muse-spark', 'qwen3.8-max'), ('gpt-5.6-terra',)],
    'reasoning': [('nemotron-3-ultra-free',), ('solar-pro4', 'glm-5.3'), ('opus-5', 'grok-4.6')],
    'heavy_multimodal': [('gemini-flash-free', 'gemini-pro-free', 'nim-llama-3.2-90b-vision'), ('mimo-v2.5',), ('gemini-3.8-flash',)],
}
BUCKETS = {bucket: [dict(_ARM_REGISTRY[name], routing_tier=tier, tier=bucket)
                    for tier, names in enumerate(tiers, 1) for name in names]
           for bucket, tiers in TIER_NAMES.items()}
DEFAULT_ARM = 'union-alpha'
ALL_POOLS: Dict[str, List[Dict[str, Any]]] = dict(BUCKETS)
ALL_POOLS['free_local'] = [dict(u, routing_tier=1, tier='free_local') for u in FREE_LOCAL]


# --- functional alias map (spec section 8 + backward-compat shims) -----------
ALIASES: Dict[str, str] = {
    # canonical bucket names
    "chat": "chat", "coding": "coding", "agentic": "agentic",
    "reasoning": "reasoning", "heavy_multimodal": "heavy_multimodal",
    "audio_edge": "audio_edge", "free_local": "free_local",
    # descriptive synonyms
    "bucket1": "chat", "ingestion": "chat", "chat_ingestion": "chat",
    "coding_terminal": "coding", "terminal": "coding", "swe": "coding", "code": "coding",
    "agentic_cron": "agentic", "cron": "agentic", "autonomous": "agentic",
    "deep_reasoning": "reasoning", "deep": "reasoning", "audit": "reasoning",
    "heavy": "heavy_multimodal", "multimodal": "heavy_multimodal", "media": "heavy_multimodal",
    "audio": "audio_edge", "voice": "audio_edge", "tts": "audio_edge", "stt": "audio_edge",
    "free": "free_local", "local": "free_local", "testing": "free_local",
    # backward-compat (pre-V9.00 mesh clients)
    "default": "chat", "auto": "chat", "omni-auto": "chat", "unknown": "chat",
    "phase1": "free_local", "phase2": "chat", "paid": "chat",
    "tier0": "chat", "tier1": "agentic", "tier2": "coding", "tier3": "reasoning",
}


def _norm(s: Optional[str]) -> str:
    """Lowercase + strip vendor prefixes so `custom/x` and `openrouter/x` match arms."""
    t = (s or "").strip().lower()
    for pfx in ("custom/", "openrouter/"):
        if t.startswith(pfx):
            t = t[len(pfx):]
    return t


def provider_ready(up: Dict[str, Any]) -> bool:
    if up.get("local"):
        return True
    return bool(up.get("key"))


def find_explicit(requested: Optional[str]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Spec section 7 — exact model id / arm name (then vendor-stripped, then substring)."""
    r = _norm(requested)
    if not r:
        return None
    for pool, provs in ALL_POOLS.items():
        for up in provs:
            if r == _norm(up["name"]) or r == _norm(up["model"]):
                return pool, up
    for pool, provs in ALL_POOLS.items():
        for up in provs:
            if r == _norm(up["model"]).split("/")[-1]:
                return pool, up
    return None


class RoutingTrigger(RuntimeError):
    def __init__(self, trigger_type, detail=''):
        self.trigger_type = trigger_type
        super().__init__(detail or trigger_type)


def routing_event(session, bucket, trigger, source, target, latency):
    """Operator-requested receipt. Local file only when writable (tests: None)."""
    if os.getenv("SAITAMA_ROUTING_LOG") == "0":
        return
    try:
        from datetime import datetime, timezone
        fields = [datetime.now(timezone.utc).isoformat(), session or '-', bucket,
                  trigger, source or '-', target or '-', str(round(latency, 2))]
        line = ' | '.join(str(v).replace('\n', ' ').replace('\r', ' ').replace('|', '/') for v in fields)
        path = os.environ.get('SAITAMA_ROUTING_LOG_PATH', '/var/log/omnirouter/routing.log')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a') as f:
            f.write(line + '\n')
    except OSError as e:
        logger.error('routing receipt unavailable: %s', e)


# Elevations persist for the session lifetime (TTL/LRU matches the affinity store).
_SESSION_TIERS = {}

def session_state(key, bucket):
    now = _cb_time.monotonic()
    for k in list(_SESSION_TIERS):
        if now - _SESSION_TIERS[k]['ts'] > 900:
            del _SESSION_TIERS[k]
    ident = (key, bucket)
    if ident not in _SESSION_TIERS:
        if len(_SESSION_TIERS) >= 4096:
            del _SESSION_TIERS[min(_SESSION_TIERS, key=lambda k: _SESSION_TIERS[k]['ts'])]
        _SESSION_TIERS[ident] = {'tier': 1, 'tier2_failures': 0, 'arm': None, 'ts': now}
    _SESSION_TIERS[ident]['ts'] = now
    return _SESSION_TIERS[ident] if key else {'tier': 1, 'tier2_failures': 0, 'arm': None, 'ts': now}


def resolve(requested: Optional[str], vision: bool = False,
            modality: str = "text", sem_bucket: Optional[str] = None
            ) -> Tuple[str, List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Spec sections 7-9 + Directive 2 modality gate + semantic routing.
    Returns (pool_key, pool_arms, explicit_arm_or_None).
    Priority: alias > explicit model > modality gate > semantic bucket > vision > default.
    """
    key = ALIASES.get(_norm(requested))
    if key in ALL_POOLS:
        return key, ALL_POOLS[key], None
    hit = find_explicit(requested) if requested else None
    if hit:
        return hit[0], ALL_POOLS[hit[0]], hit[1]
    # Directive 2: modality gate (pre-routing for audio/video and document/file payloads)
    if modality == "audio_video":
        mimo_hit = find_explicit("mimo-v2.5")
        if mimo_hit:
            # primary mimo-v2.5, universal fallback gemini-3.8-flash (heavy_multimodal)
            return "heavy_multimodal", ALL_POOLS["heavy_multimodal"], mimo_hit[1]
        return "heavy_multimodal", ALL_POOLS["heavy_multimodal"], None
    if modality == "document":
        luna_hit = find_explicit("gpt-5.6-luna")
        if luna_hit:
            # primary gpt-5.6-luna, universal fallback gemini-3.8-flash
            return "heavy_multimodal", ALL_POOLS["heavy_multimodal"], luna_hit[1]
        return "heavy_multimodal", ALL_POOLS["heavy_multimodal"], None
    # Directive 2: semantic routing for text/image payloads
    if sem_bucket and sem_bucket in ALL_POOLS:
        return sem_bucket, ALL_POOLS[sem_bucket], None
    if vision:
        return "heavy_multimodal", ALL_POOLS["heavy_multimodal"], None
    return DEFAULT_BUCKET, ALL_POOLS[DEFAULT_BUCKET], None


def build_candidates(pool_key: str, pool: List[Dict[str, Any]],
                     explicit_arm: Optional[Dict[str, Any]], vision: bool) -> List[Dict[str, Any]]:
    """Build a bucket-local, free-first cascade; exact IDs are direct only."""
    if explicit_arm is not None:
        return [dict(explicit_arm, tier=pool_key)] if provider_ready(explicit_arm) else []
    ready = [u for u in pool if provider_ready(u)]
    if vision:
        ready = sorted(ready, key=lambda u: (not u.get("vision"),))  # vision-capable first

    ordered: List[Dict[str, Any]] = []
    seen = set()

    def add(up: Dict[str, Any], tier: str) -> None:
        if up.get("name") in seen:
            return
        s = dict(up)
        s["tier"] = tier
        ordered.append(s)
        seen.add(up.get("name"))

    if explicit_arm is not None and provider_ready(explicit_arm):
        add(explicit_arm, pool_key)
    for up in ready:
        add(up, pool_key)
    return sorted(ordered, key=lambda u: u.get('routing_tier', 1))



# --------------------------------------------------------------------------
# V9.03 D3 - SAAR: Session-Aware Agentic Routing
# --------------------------------------------------------------------------
class AffinityStore:
    """Per-session arm pinning (SAAR), telephone packet V9.03 D3.

    Problem: one multi-step execution loop (LLM -> tool -> LLM -> tool) issues N
    independent HTTP requests.  Without affinity every request re-enters
    semantic routing independently, so a loop can land on arm A for planning and
    arm B for execution.  Two costs: the upstream KV cache for that conversation
    is thrown away at every hop, and an agent that reasoned with one model
    continues its plan on another.

    Design constraints, in priority order:

    1. **Never block.**  Affinity only *reorders* the candidate cascade.  If the
       pinned arm fails, normal failover proceeds and the pin moves with it --
       a warm cache is worth less than a completed request.
    2. **Never override scope.**  A pin that is invalid for the current request
       (modality gate fired, bucket changed, provider no longer ready, arm
       missing) is ignored, not enforced.  Affinity is a preference, not a lock.
    3. **Bounded.**  TTL plus an LRU session cap, so an abandoned session cannot
       pin an arm indefinitely.

    Thread-safety: the FastAPI handlers run on one event loop and every method
    below is straight-line dict work with no awaits, so no lock is required.
    """

    def __init__(self, ttl_seconds: int = 900, max_sessions: int = 4096):
        self.ttl = int(ttl_seconds)
        self.max_sessions = int(max_sessions)
        self._pins: Dict[str, Dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0
        self.scope_misses = 0
        self.pins_set = 0
        self.failovers = 0
        self.expired = 0
        self.evicted = 0

    def _now(self) -> float:
        return _cb_time.time()

    def get(self, key: Optional[str]) -> Optional[str]:
        if not key:
            return None
        rec = self._pins.get(key)
        if rec is None:
            return None
        if self._now() - rec["ts"] > self.ttl:
            self._pins.pop(key, None)
            self.expired += 1
            return None
        rec["ts"] = self._now()
        return rec["arm"]

    def pin(self, key: Optional[str], arm: str, previous: Optional[str] = None) -> str:
        if not key:
            return "no_key"
        if previous and previous != arm:
            self.failovers += 1
            outcome = "failover_repin"
        elif previous == arm:
            outcome = "held"
        else:
            outcome = "new_pin"
        self._pins[key] = {"arm": arm, "ts": self._now(), "hits": 0}
        self._pins[key]["hits"] = self._pins[key].get("hits", 0)
        self.pins_set += 1
        self._expire()
        return outcome

    def note_hit(self, key: Optional[str], arm: str) -> None:
        self.hits += 1
        rec = self._pins.get(key or "")
        if rec is not None:
            rec["hits"] = rec.get("hits", 0) + 1

    def note_scope_miss(self, key: Optional[str], arm: str, requested_scope: str) -> None:
        self.scope_misses += 1
        logger.info("saar: pin %s not valid for scope=%s -> advisory miss, cascade unchanged",
                    arm, requested_scope)

    def _expire(self) -> None:
        """Drop expired sessions; LRU-evict oldest if over the session cap."""
        if len(self._pins) > self.max_sessions:
            ordered = sorted(self._pins.items(), key=lambda kv: kv[1]["ts"])
            for k, _ in ordered[: len(self._pins) - self.max_sessions]:
                self._pins.pop(k, None)
                self.evicted += 1
        if len(self._pins) > 256:
            now = self._now()
            for k in [k for k, v in self._pins.items() if now - v["ts"] > self.ttl]:
                self._pins.pop(k, None)
                self.expired += 1

    def stats(self) -> Dict[str, Any]:
        total = self.hits + self.scope_misses
        return {
            "enabled": True,
            "ttl_seconds": self.ttl,
            "active_sessions": len(self._pins),
            "max_sessions": self.max_sessions,
            "affinity_hits": self.hits,
            "affinity_scope_misses": self.scope_misses,
            "affinity_pins_set": self.pins_set,
            "failover_repins": self.failovers,
            "expired": self.expired,
            "lru_evicted": self.evicted,
            "hit_rate": round(self.hits / total, 3) if total else None,
            "arms_held": sorted({v["arm"] for v in self._pins.values()}),
        }


_affinity = AffinityStore()


def _affinity_key_from(request: Any, payload: Dict[str, Any],
                       messages: List[Dict]) -> Optional[str]:
    """Derive a stable conversation key for SAAR.

    Priority: explicit client signal (header, then payload field) > content hash
    of the stable conversation prefix.

    The content fallback is the point of the whole design: it makes SAAR engage
    for clients that know nothing about it (no protocol change, no client
    coordination), and it is stable *because* the system prompt and first user
    message stay byte-identical across the turns of one loop.  Two unrelated
    conversations sharing a prefix collide, which is harmless -- they just share
    a warm arm.
    """
    try:
        hdrs = getattr(request, "headers", None)
        if hdrs is not None:
            for h in ("x-session-id", "x-affinity-key", "x-conversation-id"):
                v = (hdrs.get(h) or "").strip()
                if v:
                    return "hdr:" + h + ":" + v[:200]
    except Exception:
        pass

    for k in ("session_id", "conversation_id", "user"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return "body:" + k + ":" + v.strip()[:200]

    md = payload.get("metadata")
    if isinstance(md, dict):
        for k in ("session_id", "conversation_id"):
            v = md.get(k)
            if isinstance(v, str) and v.strip():
                return "meta:" + k + ":" + v.strip()[:200]

    sys_txt = ""
    first_user = ""
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, sort_keys=True, default=str) if content else ""
        if role == "system" and not sys_txt:
            sys_txt = content
        elif role == "user" and not first_user:
            first_user = content
            break
    if not (sys_txt or first_user):
        return None
    digest = hashlib.sha256((sys_txt + "\x00" + first_user).encode("utf-8", "replace"))
    return "conv:" + digest.hexdigest()[:16]


# V8.03 D4: Circuit Breaker - advisory failure tracking (no lockouts)
import time as _cb_time


class CircuitBreaker:
    """V9.00: advisory failure telemetry. is_open() always False (fluid cascade)."""

    def __init__(self, failure_threshold=3, cooldown_seconds=600):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: Dict[str, int] = {}
        self._cooldowns: Dict[str, float] = {}
        self._models: Dict[str, str] = {}
        self._tiers: Dict[str, str] = {}
        self._last_errors: Dict[str, str] = {}
        self._escalations = 0
        self._total_requests = 0
        self._total_failures = 0

    def record_failure(self, provider_name, is_timeout=False, model=None, tier=None, error=None):
        self._failures[provider_name] = self._failures.get(provider_name, 0) + 1
        self._total_failures += 1
        if model:
            self._models[provider_name] = model
        if tier:
            self._tiers[provider_name] = tier
        if error:
            self._last_errors[provider_name] = str(error)[:200]
        if is_timeout:
            self._failures[provider_name] = max(self._failures[provider_name], self.failure_threshold)
        if self._failures[provider_name] >= self.failure_threshold:
            backoff_multiplier = min(2 ** (self._escalations % 4), 8)
            effective_cooldown = min(self.cooldown_seconds * backoff_multiplier, 3600)
            self._cooldowns[provider_name] = _cb_time.time() + effective_cooldown
            self._escalations += 1
            logger.warning("CIRCUIT-BREAKER: %s tripped (%d failures, model=%s, bucket=%s), cooldown %ds (backoff x%d)",
                           provider_name, self._failures[provider_name],
                           self._models.get(provider_name, "?"), self._tiers.get(provider_name, "?"),
                           effective_cooldown, backoff_multiplier)
            return True
        return False

    def record_success(self, provider_name):
        self._failures.pop(provider_name, None)
        self._cooldowns.pop(provider_name, None)
        self._last_errors.pop(provider_name, None)

    def is_open(self, provider_name):
        """Advisory-only — always False (no artificial lockouts in the fluid cascade)."""
        return False

    def stats(self):
        open_details = {}
        for name, expiry in self._cooldowns.items():
            open_details[name] = {
                "model": self._models.get(name, "?"),
                "bucket": self._tiers.get(name, "?"),
                "cooldown_remaining_s": max(0, int(expiry - _cb_time.time())),
                "last_error": self._last_errors.get(name, "?"),
            }
        return {
            "version": GATEWAY_VERSION,
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,
            "escalations": self._escalations,
            "total_requests": self._total_requests,
            "total_failures": self._total_failures,
            "open_circuits": list(self._cooldowns.keys()),
            "open_circuit_details": open_details,
            "failure_counts": dict(self._failures),
            "tracked_models": dict(self._models),
            "tracked_buckets": dict(self._tiers),
        }


_circuit_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=600)

_DEEP_CUES = ("think deeply", "deep think", "think harder", "ultrathink",
              "think step by step", "reason step by step", "reason carefully",
              "chain of thought")


def deep_thinking_requested(messages: List[Dict]) -> bool:
    """True when the caller asks for deliberate reasoning."""
    txt = " ".join(str(m.get("content", "")) for m in messages
                   if isinstance(m.get("content"), str)).lower()
    return any(c in txt for c in _DEEP_CUES)



# --- Directive 2: payload modality gate + semantic embedding router -------

EMBED_MODEL = "nvidia/nemotron-3-embed-1b"
# Calibrated 2026-09-13 against nemotron-3-embed-1b: cosine on this model runs low
# (real intents land 0.13-0.39); argmax discriminates correctly, so the threshold only
# guards against genuine no-match/failed-embed cases -> fall back to B1.
SEMANTIC_THRESHOLD = float(os.getenv("SEMANTIC_ROUTING_THRESHOLD", "0.08"))

# Reference descriptions for each paid functional bucket (used for embedding).
# Sentence style + input_type=query (proven best separation on nemotron-3-embed-1b).
# V9.02 reword: widened angular separation between buckets — the coding reference now
# claims code artefacts (diff hunks, stack traces, compiler/syntax errors, repo
# tooling) exclusively, while heavy_multimodal claims non-text payload ingestion
# (pixels/frames/audio) exclusively; neither mentions "document"/"file" generically
# any more, which is what made a CSV-parsing coding prompt argmax to heavy_multimodal.
BUCKET_DESCRIPTIONS = {
    "chat": "A casual friendly exchange, a greeting, or a short general-knowledge "
            "question that needs no tools, no code, and no file or media processing.",
    "coding": "Work on source code: writing or refactoring a function or a class, "
              "reading a unified diff or patch hunk, fixing an exception stack trace, "
              "resolving a syntax or compiler error, and running git, shell, or test "
              "commands inside a repository.",
    "agentic": "Planning and carrying out an autonomous multi-step workflow: calling "
               "several tools or APIs in sequence, scheduling background jobs, and "
               "orchestrating services until a goal is reached.",
    "reasoning": "Careful deliberation over a text argument: auditing a protocol or "
                 "policy, weighing trade-offs, root-cause analysis, and formal logical "
                 "or mathematical proof.",
    "heavy_multimodal": "Ingesting a non-text payload so it can be perceived rather "
                        "than parsed as text: a photograph or screenshot, a scanned "
                        "page rendered as pixels, a video frame, or an audio recording.",
}

_BUCKET_VECTORS: Dict[str, List[float]] = {}


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Pure-Python cosine similarity (no numpy dependency)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _embed_text(text_str: str) -> Optional[List[float]]:
    """Embed a single text string via NVIDIA NIM nemotron-3-embed-1b."""
    if not _nvidia_key:
        return None
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                NVIDIA_EMBED_URL,
                headers={"Authorization": f"Bearer {_nvidia_key}"},
                json={"input": text_str, "model": EMBED_MODEL,
                      "input_type": "query", "encoding_format": "float"},
            )
        if r.status_code >= 400:
            logger.warning("semantic embed HTTP %s: %s", r.status_code, r.text[:200])
            return None
        data = r.json()
        return data["data"][0]["embedding"]
    except Exception as e:
        logger.warning("semantic embed error: %s", e)
        return None


async def _precompute_bucket_vectors():
    """Startup: embed each bucket description for cosine routing."""
    for bucket, desc in BUCKET_DESCRIPTIONS.items():
        vec = await _embed_text(desc)
        if vec:
            _BUCKET_VECTORS[bucket] = vec
            logger.info("semantic: bucket '%s' reference embedded (%d dims)", bucket, len(vec))
        else:
            logger.warning("semantic: bucket '%s' embed failed — will fall back to default", bucket)


def _classify_modality(messages: List[Dict]) -> str:
    """Detect payload modality. Returns: 'audio_video', 'document', or 'text'."""
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type", "")
                if ptype in ("input_audio",) or "audio" in ptype:
                    return "audio_video"
                if ptype in ("video",) or "video" in ptype:
                    return "audio_video"
                if ptype in ("file",) or "file" in ptype:
                    return "document"
                if ptype in ("document",):
                    return "document"
                # data: URL checks
                url = ""
                if ptype == "image_url":
                    iu = part.get("image_url", {})
                    url = iu.get("url", "") if isinstance(iu, dict) else str(iu)
                if url.startswith("data:video/") or url.startswith("data:audio/"):
                    return "audio_video"
        elif isinstance(content, str):
            if "data:video/" in content or "data:audio/" in content:
                return "audio_video"
    return "text"


async def semantic_route(messages: List[Dict]) -> Optional[str]:
    """Embed prompt and return best-matching bucket name, or None if below threshold."""
    parts: List[str] = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
    prompt_text = " ".join(parts).strip()
    if not prompt_text or not _BUCKET_VECTORS:
        return None
    vec = await _embed_text(prompt_text)
    if not vec:
        return None
    best_bucket: Optional[str] = None
    best_sim = -1.0
    for bucket, ref_vec in _BUCKET_VECTORS.items():
        sim = _cosine_similarity(vec, ref_vec)
        if sim > best_sim:
            best_sim = sim
            best_bucket = bucket
    if best_sim < SEMANTIC_THRESHOLD:
        logger.info("semantic: below threshold (%.3f < %.3f) — defaulting to chat", best_sim, SEMANTIC_THRESHOLD)
        return None
    logger.info("semantic: classified '%s' (sim=%.3f)", best_bucket, best_sim)
    return best_bucket

def catalog() -> List[Dict[str, str]]:
    """Spec section 8 discovery surface: one row per arm, tagged with its bucket."""
    out = []
    for pool, provs in ALL_POOLS.items():
        priority = 0
        for up in provs:
            priority += 1
            out.append({
                "id": up["name"],
                "bucket": pool,
                "tier": pool,                       # backward-compat field
                "priority": priority,
                "model": str(up.get("model", "")),
                "ready": str(provider_ready(up)).lower(),
                "vision": str(bool(up.get("vision"))).lower(),
                "reasoning": str(bool(up.get("reasoning"))).lower(),
                "provider": up.get("provider", "openrouter"),
            })
    return out


class ClientPayloadError(RuntimeError):
    """Upstream rejected OUR payload with a deterministic client error (4xx).

    V9.02 fail-fast: a 400/413/422 is a verdict on the request itself, not a verdict
    on the arm — every other arm in the cascade would reject the identical bytes. So
    this error is NOT failover-worthy: the cascade aborts on first sight and the
    upstream status is returned to the caller instead of burning the chain (observed
    ~120 s to client timeout on a synthetic-PDF probe, Dir2 addendum).
    """

    def __init__(self, status_code: int, arm: str, body: str = ""):
        self.status_code = status_code
        self.arm = arm
        self.body = body
        super().__init__(f"HTTP {status_code} from {arm}: {body[:200]}")


# statuses that mean "your payload is malformed/too large" and must never fail over
CLIENT_ERROR_STATUSES = (400, 413, 422)


async def _post(up: Dict[str, Any], body: Dict) -> Any:
    """POST to the arm's upstream (streamed for true TTFT) and reassemble the
    full response. Trigger classification: 429 -> '429'; 4xx with tools ->
    'schema_error'; first-token latency > 8s -> 'ttft'. Non-tools 4xx stays a
    deterministic ClientPayloadError (fail-fast, no failover)."""
    import httpx
    url = up["url"]
    key = up.get("key", "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = dict(body)
    body["stream"] = True
    body.setdefault("stream_options", {"include_usage": True})
    t0 = _cb_time.monotonic()
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_acc: Dict[int, Dict[str, Any]] = {}
    finish = None
    usage = None
    saw_token = False
    try:
        async with asyncio.timeout(150):
            async with httpx.AsyncClient(timeout=httpx.Timeout(150, connect=10)) as client:
                async with client.stream("POST", url, headers=headers, json=body) as r:
                    if r.status_code == 429:
                        raise RoutingTrigger("429", f"HTTP 429 from {up['name']}")
                    if r.status_code in CLIENT_ERROR_STATUSES:
                        text = (await r.aread()).decode("utf-8", "replace")
                        if body.get("tools"):
                            raise RoutingTrigger("schema_error",
                                                 f"HTTP {r.status_code} from {up['name']}: {text[:200]}")
                        raise ClientPayloadError(r.status_code, up["name"], text)
                    if r.status_code >= 400:
                        text = (await r.aread()).decode("utf-8", "replace")
                        raise RuntimeError(f"HTTP {r.status_code}: {text[:200]}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except Exception:
                            continue
                        if isinstance(chunk.get("usage"), dict):
                            usage = chunk["usage"]
                        ch = (chunk.get("choices") or [{}])[0]
                        delta = ch.get("delta") or {}
                        payload_now = bool(delta.get("content") or delta.get("reasoning")
                                           or delta.get("tool_calls"))
                        if payload_now and not saw_token:
                            saw_token = True
                            if _cb_time.monotonic() - t0 > TTFT_TRIGGER_SECONDS:
                                raise RoutingTrigger(
                                    "ttft", f"first token after {_cb_time.monotonic() - t0:.1f}s from {up['name']}")
                        if delta.get("content"):
                            content_parts.append(delta["content"])
                        if delta.get("reasoning"):
                            reasoning_parts.append(delta["reasoning"])
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            acc = tool_acc.setdefault(idx, {"id": None, "type": "function",
                                                            "name": "", "arguments": ""})
                            if tc.get("id"):
                                acc["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                acc["name"] += fn["name"]
                            if fn.get("arguments"):
                                acc["arguments"] += fn["arguments"]
                        if ch.get("finish_reason"):
                            finish = ch["finish_reason"]
    except RoutingTrigger:
        raise
    except ClientPayloadError:
        raise
    except TimeoutError as e:
        raise RuntimeError(f"timeout: {e}") from e
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {e}") from e
    tool_calls = [{"id": acc["id"], "type": "function", "index": idx,
                   "function": {"name": acc["name"], "arguments": acc["arguments"]}}
                  for idx, acc in sorted(tool_acc.items())]
    message = {"role": "assistant",
               "content": "".join(content_parts) or None,
               "reasoning": "".join(reasoning_parts) or None,
               "tool_calls": tool_calls or None}
    return {"choices": [{"index": 0, "finish_reason": finish or "stop", "message": message}],
            "usage": usage or {}}


TTFT_TRIGGER_SECONDS = float(os.getenv("SAITAMA_TTFT_TRIGGER_SECONDS", "8.0"))
_CLIENT_ERR = (400, 413, 422)


async def completion(messages: List[Dict], explicit: Optional[str],
                     vision: bool = False, tools: Optional[List[Dict]] = None,
                     tool_choice: Optional[Any] = None,
                     deep_thinking: bool = False,
                     modality: str = "text",
                     sem_bucket: Optional[str] = None,
                     affinity_key: Optional[str] = None) -> Dict[str, Any]:
    pool_key, pool, explicit_arm = resolve(explicit, vision, modality=modality, sem_bucket=sem_bucket)
    candidates = build_candidates(pool_key, pool, explicit_arm, vision)
    if not candidates:
        raise RuntimeError("no ready provider (stage provider keys in the gateway .env)")

    # V9.03 D3 SAAR: if this session already holds an arm that is valid for
    # this request, promote it to the head of the cascade. Reordering only --
    # every other arm stays reachable for failover.
    _aff_pin = _affinity.get(affinity_key)
    _aff_promoted = False
    if _aff_pin:
        _aff_idx = next((i for i, u in enumerate(candidates) if u.get("name") == _aff_pin), None)
        if _aff_idx is None:
            _affinity.note_scope_miss(affinity_key, _aff_pin, pool_key)
        else:
            if _aff_idx > 0:
                candidates = [candidates[_aff_idx]] + candidates[:_aff_idx] + candidates[_aff_idx + 1:]
            _aff_promoted = True
            _affinity.note_hit(affinity_key, _aff_pin)

    _circuit_breaker._total_requests += 1
    # Strict waterfall state: T1 default entry, T2 on trigger, T3 only after
    # two T2 failures. Elevation persists for the session (SAAR pinning).
    state = session_state(affinity_key, pool_key)
    t_floor = state["tier"]
    t2_failures = state["tier2_failures"]
    if os.getenv("SAITAMA_WATERFALL_DEBUG") == "1":  # temporary diagnostic, env-gated
        print(f"[waterfall-debug] session={affinity_key} bucket={pool_key} "
              f"held_floor={t_floor} t2_failures={t2_failures} "
              f"pool_tiers={sorted({(u['name'], u.get('routing_tier')) for u in pool})} "
              f"candidates={[(u['name'], u.get('routing_tier')) for u in candidates]}")
    last_err = None
    triggered = None
    i = 0
    while i < len(candidates):
        up = candidates[i]
        # Session elevation floor: skip arms below the held tier (never demote).
        if up.get("routing_tier", 1) < t_floor:
            i += 1
            continue
        # T3 gate: enter only after two recorded T2 trigger failures, or when
        # this session is already pinned at the closer tier.
        if up.get("routing_tier", 1) >= 3 and t_floor < 3 and t2_failures < 2:
            i += 1
            continue
        body: Dict[str, Any] = {"model": up["model"], "messages": messages, "stream": False}
        if tools:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if deep_thinking and up.get("reasoning"):
            # OpenRouter unified reasoning surface (only for reasoning-flagged arms)
            body["reasoning"] = {"effort": "high"}
        try:
            data = await _post(up, body)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message", {}) or {}
            _circuit_breaker.record_success(up["name"])
            # V9.03 D3 SAAR: hold this arm for the rest of the loop. A pin
            # change here means the previous arm failed, which is exactly the
            # KV-cache loss SAAR is meant to surface as a metric.
            _aff_outcome = _affinity.pin(affinity_key, up["name"], previous=_aff_pin)
            if _aff_outcome == "failover_repin":
                logger.warning("saar: session %s failed over %s -> %s (KV cache cold)",
                               (affinity_key or "?")[:24], _aff_pin, up["name"])
            state.update({"tier": up.get("routing_tier", 1), "tier2_failures": 0, "arm": up["name"]})
            routing_event(affinity_key, pool_key, triggered or "default",
                          _aff_pin or None, up["name"], 0.0)
            return {
                "id": f"saitama-gw-{up['name']}",
                "object": "chat.completion",
                "model": up["model"],
                "tier": up.get("tier", pool_key),      # backward-compat field
                "choices": [{
                    "index": 0,
                    "finish_reason": choice.get("finish_reason", "stop"),
                    "message": {"role": "assistant",
                                "content": msg.get("content"),
                                "reasoning": msg.get("reasoning"),
                                "tool_calls": msg.get("tool_calls")},
                }],
                "usage": data.get("usage", {}),
                "saitama": {
                    "route": up["name"],
                    "routing_tier": up.get("routing_tier", 1),
                    "bucket": up.get("tier", pool_key),
                    "phase": "0" if up.get("tier") == "free_local" else "1",
                    "provider": up.get("provider", "openrouter"),
                    "trigger": triggered or "default",
                    "deep_thinking": bool(deep_thinking and up.get("reasoning")),
                    "affinity_key": affinity_key,
                    "affinity_pinned": _aff_promoted,
                    "affinity_arm": _aff_pin,
                    "affinity_outcome": _aff_outcome,
                },
            }
        except RoutingTrigger as e:
            # 429 / schema_error / ttft — strict waterfall: a tier-1 trigger
            # escalates the session floor to T2 (the floor skip suppresses the
            # remaining T1 siblings); a T2 arm is retried once in place, and
            # only a second T2 failure elevates to the T3 closer. The next
            # request re-enters at the held floor (SAAR elevation pin).
            last_err = f"{up['name']}: {e}"
            triggered = e.trigger_type
            logger.warning("waterfall trigger %s on %s", e.trigger_type, up["name"])
            tier = up.get("routing_tier", 1)
            if tier == 1:
                state["tier"] = 2
                t_floor = 2
                i += 1  # leave tier 1; the floor skip drops the rest of T1
            elif tier == 2:
                t2_failures += 1
                state["tier2_failures"] = t2_failures
                if t2_failures == 1:
                    continue  # same T2 arm, retried once (i unchanged)
                state["tier"] = 3
                t_floor = 3
                i += 1
            _err_str = str(e)
            _circuit_breaker.record_failure(
                up["name"],
                is_timeout=(e.trigger_type == "ttft"),
                model=up.get("model", "?"), tier=up.get("tier", pool_key), error=_err_str[:200])
        except ClientPayloadError as e:
            # V9.02 fail-fast: deterministic client error -> abort cascade immediately,
            # no circuit-breaker penalty (the arm is healthy; our payload is not).
            logger.warning("cascade ABORT on client error %s (arm=%s) — no failover", e.status_code, e.arm)
            raise
        except Exception as e:
            last_err = f"{up['name']}: {e}"
            logger.warning("failover %s failed: %s", up["name"], e)
            _err_str = str(e)
            _circuit_breaker.record_failure(
                up["name"],
                is_timeout=("timeout" in _err_str.lower() or "timed out" in _err_str.lower()),
                model=up.get("model", "?"), tier=up.get("tier", pool_key), error=_err_str[:200])
            i += 1
    raise RuntimeError(f"all upstreams failed; last={last_err}")


# --- HTTP surface -----------------------------------------------------------
try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response
    app = FastAPI(title="Saitama Omnirouter Gateway", version=GATEWAY_VERSION)
    @app.on_event("startup")
    async def _startup_semantic_precompute():
        """Directive 2: pre-compute bucket reference vectors for semantic routing."""
        try:
            await _precompute_bucket_vectors()
            logger.info("semantic: %d bucket vectors pre-computed at startup", len(_BUCKET_VECTORS))
        except Exception as e:
            logger.error("semantic: startup precompute failed: %s", e)


    _telemetry_db = TelemetryDB(db_path="telemetry.db")
    app.add_middleware(TelemetryMiddleware, db=_telemetry_db)
    mount_telemetry_routes(app, _telemetry_db)

    def _bucket_readiness() -> Dict[str, int]:
        out = {}
        for k, provs in ALL_POOLS.items():
            out[k] = len([u for u in provs if provider_ready(u)])
        return out

    @app.get("/health")
    async def health():
        return JSONResponse({
            "status": "ok",
            "gateway": "saitama",
            "version": GATEWAY_VERSION,
            "routing": "functional-buckets (V9.02 fail-fast + semantic router)",
            "host": HOST, "port": PORT,
            "nous_portal": "evicted",
            "paid_provider": "openrouter-only",
            "default_bucket": DEFAULT_BUCKET,
            "default_model": f"deepseek/deepseek-v4.1-flash",
            "buckets": BUCKET_ORDER_PAID + ["audio_edge", "free_local"],
            "bucket_ready": _bucket_readiness(),
        })

    @app.get("/v1/models")
    async def models():
        return JSONResponse({"object": "list", "data": catalog()})

    @app.get("/v1/buckets")
    async def buckets():
        return JSONResponse({
            "version": GATEWAY_VERSION,
            "default_bucket": DEFAULT_BUCKET,
            "default_model": "deepseek/deepseek-v4.1-flash",
            "buckets": {
                k: [{"id": u["name"], "model": u["model"],
                     "ready": provider_ready(u),
                     "vision": bool(u.get("vision")),
                     "reasoning": bool(u.get("reasoning")),
                     "provider": u.get("provider", "openrouter")}
                    for u in provs]
                for k, provs in ALL_POOLS.items() if k != "free_local"
            },
            "free_local_pool": [u["name"] for u in FREE_LOCAL],
            "audio_edge": {
                "tts_cloud": {"engine": "edge-tts", "voices": list(EDGE_TTS_VOICES),
                              "default_voice": EDGE_TTS_DEFAULT_VOICE,
                              "endpoint": "POST /v1/audio/speech (model=edge)"},
                "tts_local": {"engine": "piper", "url": PIPER_SPEECH_URL,
                              "default_voice": PIPER_DEFAULT_VOICE,
                              "endpoint": "POST /v1/audio/speech (default)"},
                "stt": {"engine": "groq-whisper-large-v3",
                        "endpoint": "POST /v1/audio/transcriptions",
                        "fallback_url": STT_FALLBACK_URL},
            },
            "aliases": ALIASES,
        })

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        """OpenAI-compatible embeddings proxy -> NVIDIA NIM BYOK (2048-dim)."""
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": {"message": "invalid json", "type": "invalid_request_error", "code": 400}}, status_code=400)
        nim_key = env("NVIDIA_NIM_API_KEY")
        if not nim_key:
            return JSONResponse({"error": {"message": "NVIDIA_NIM_API_KEY not staged on gateway",
                                           "type": "config_error", "code": 503}}, status_code=503)
        model = str(payload.get("model") or "nvidia/nemotron-3-embed-1b")
        body = {"input": payload.get("input"), "model": model,
                "input_type": payload.get("input_type", "query"),
                "encoding_format": payload.get("encoding_format", "float")}
        if payload.get("user"):
            body["user"] = payload["user"]
        import httpx
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(NVIDIA_EMBED_URL, headers={"Authorization": f"Bearer {nim_key}"}, json=body)
            return JSONResponse(r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse({"error": {"message": str(e), "type": "upstream_error", "code": 503}}, status_code=503)

    # ---- B6 AUDIO EDGE: TTS -------------------------------------------------
    async def _tts_piper(text: str, voice: str) -> bytes:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(PIPER_SPEECH_URL, json={"model": voice, "input": text, "voice": voice})
        if r.status_code >= 400:
            raise RuntimeError(f"piper HTTP {r.status_code}: {r.text[:160]}")
        return r.content

    async def _tts_edge(text: str, voice: str) -> bytes:
        import edge_tts
        comm = edge_tts.Communicate(text, voice)
        buf = b""
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                buf += chunk["data"]
        if not buf:
            raise RuntimeError("edge-tts returned no audio")
        return buf

    @app.post("/v1/audio/speech")
    async def speech(request: Request):
        """B6 TTS. model=edge|edge-tts -> Edge TTS cloud conduit (MP3);
        anything else (or omitted) -> Piper local on Saitama (WAV). Fails over between them."""
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": {"message": "invalid json", "type": "invalid_request_error", "code": 400}}, status_code=400)
        text = str(payload.get("input") or payload.get("text") or "")
        if not text:
            return JSONResponse({"error": {"message": "missing 'input'", "type": "invalid_request_error", "code": 400}}, status_code=400)
        model = str(payload.get("model") or "").strip().lower()
        voice = str(payload.get("voice") or "").strip()
        want_edge = model in ("edge", "edge-tts", "edge_tts", "cloud")
        wants_wav = str(payload.get("response_format") or "").lower() == "wav"
        if want_edge and wants_wav:
            # edge-tts only emits MP3; serve the local Piper engine for a true WAV
            want_edge = False
            logger.info("audio_edge: response_format=wav requested -> using Piper local for a real WAV")

        attempts = []
        if want_edge:
            attempts = [("edge-tts", _tts_edge, voice or EDGE_TTS_DEFAULT_VOICE, "audio/mpeg"),
                        ("piper-local", _tts_piper, voice or PIPER_DEFAULT_VOICE, "audio/wav")]
        else:
            attempts = [("piper-local", _tts_piper, voice or PIPER_DEFAULT_VOICE, "audio/wav"),
                        ("edge-tts", _tts_edge, voice if voice in EDGE_TTS_VOICES else EDGE_TTS_DEFAULT_VOICE, "audio/mpeg")]

        last_err = None
        for engine, fn, v, media in attempts:
            try:
                audio = await fn(text, v)
                if not audio:
                    raise RuntimeError("empty audio")
                headers = {"X-Saitama-Engine": engine, "X-Saitama-Voice": v,
                           "X-Saitama-Bucket": "audio_edge"}
                if engine == "piper-local" and not audio[:4] == b"RIFF":
                    logger.warning("audio_edge: piper output missing RIFF header")
                logger.info("audio_edge TTS ok engine=%s voice=%s bytes=%d", engine, v, len(audio))
                return Response(content=audio, media_type=media, headers=headers)
            except Exception as e:
                last_err = f"{engine}: {e}"
                logger.warning("audio_edge TTS engine %s failed: %s", engine, e)
        return JSONResponse({"error": {"message": f"all TTS engines failed; last={last_err}",
                                       "type": "upstream_error", "code": 503}}, status_code=503)

    # ---- B6 AUDIO EDGE: STT -------------------------------------------------
    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request):
        """B6 STT: Groq Whisper Large V3 passthrough (BYOK). Local Whisper fallback is
        wired to STT_FALLBACK_URL (Genos .5:8645) and degrades gracefully while absent."""
        import httpx
        groq_key = env("GROQ_API_KEY")
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

        if groq_key:
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    r = await client.post(GROQ_STT_URL, headers={"Authorization": f"Bearer {groq_key}"},
                                          data={"model": model}, files=files)
                if r.status_code < 400:
                    out = r.json()
                    out["_engine"] = "groq-whisper"
                    return JSONResponse(out, status_code=200)
                logger.warning("audio_edge STT groq failed: HTTP %s", r.status_code)
            except Exception as e:
                logger.warning("audio_edge STT groq failed: %s", e)
        else:
            logger.warning("audio_edge STT: GROQ_API_KEY not staged; trying local fallback")

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(STT_FALLBACK_URL, files=files, data={"model": model})
            out = r.json()
            out["_engine"] = "local-whisper"
            return JSONResponse(out, status_code=r.status_code)
        except Exception as e:
            return JSONResponse({"error": {"message": f"STT unavailable: groq={bool(groq_key)} fallback={STT_FALLBACK_URL} ({e})",
                                           "type": "upstream_error", "code": 503}}, status_code=503)

    # ---- chat completions ---------------------------------------------------
    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": {"message": "invalid json", "type": "invalid_request_error", "code": 400}}, status_code=400)
        mm = payload.get("messages") or []
        explicit = str(payload.get("model") or "") or None
        want_stream = bool(payload.get("stream", False))
        deep_thinking = bool(payload.get("enable_thinking") or payload.get("reasoning_effort")) \
            or deep_thinking_requested(mm)
        # Directive 2: classify payload modality, then semantic route text/image
        modality = _classify_modality(mm)
        sem_bucket = None
        classified = bool(ALIASES.get(_norm(explicit))) or bool(explicit and find_explicit(explicit))
        if modality == "audio_video":
            logger.info("modality-gate: audio/video payload -> mimo-v2.5 (fallback gemini-3.8-flash)")
        elif modality == "document":
            logger.info("modality-gate: document/file payload -> gpt-5.6-luna (fallback gemini-3.8-flash)")
        elif not classified:
            sem_bucket = await semantic_route(mm)
        # V9.03 D3 SAAR: one key per execution loop, so the whole loop stays
        # pinned to a single warm arm.
        affinity_key = _affinity_key_from(request, payload, mm)
        try:
            result = await completion(mm, explicit, vision=False,
                                      tools=payload.get("tools"),
                                      tool_choice=payload.get("tool_choice"),
                                      deep_thinking=deep_thinking,
                                      modality=modality,
                                      sem_bucket=sem_bucket,
                                      affinity_key=affinity_key)
            if not want_stream:
                return JSONResponse(result)
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
                    yield f"data: {json.dumps({'id': rid, 'object': 'chat.completion.chunk', 'created': int(_t.time()), 'model': model, 'tier': tier, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]})}\n\n"
                for tc in tool_calls:
                    fn = tc.get("function", {}) or {}
                    delta = {"tool_calls": [{
                        "index": tc.get("index", 0),
                        "id": tc.get("id"),
                        "type": tc.get("type", "function"),
                        "function": {"name": fn.get("name"), "arguments": fn.get("arguments", "")},
                    }]}
                    yield f"data: {json.dumps({'id': rid, 'object': 'chat.completion.chunk', 'created': int(_t.time()), 'model': model, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]})}\n\n"
                yield f"data: {json.dumps({'id': rid, 'object': 'chat.completion.chunk', 'created': int(_t.time()), 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]})}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream")
        except ClientPayloadError as e:
            # V9.02 fail-fast: surface the upstream verdict verbatim instead of a 503
            # after walking the whole chain.
            return JSONResponse(
                {"error": {"message": f"upstream rejected the request payload: {e.body[:400] or e}",
                           "type": "invalid_request_error", "code": e.status_code,
                           "arm": e.arm}},
                status_code=e.status_code)
        except Exception as e:
            return JSONResponse({"error": {"message": str(e), "type": "upstream_error", "code": 503}}, status_code=503)

    @app.get("/v1/affinity/stats")
    async def affinity_stats():
        """V9.03 D3 SAAR telemetry: how often a loop held one warm arm."""
        return JSONResponse({"affinity": _affinity.stats(), "version": GATEWAY_VERSION})

    @app.get("/circuit-breaker/stats")
    async def circuit_breaker_stats():
        return JSONResponse({"circuit_breaker": _circuit_breaker.stats(), "version": GATEWAY_VERSION})

    def main():
        import uvicorn
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")

except ImportError:
    raise SystemExit("fastapi not installed: .venv/bin/pip install -q fastapi uvicorn httpx edge-tts")

if __name__ == "__main__":
    main()
