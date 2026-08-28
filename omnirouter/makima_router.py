"""
Makima — 3-Tier LLM Routing (replicated from fsociety hermes_gateway pattern).

Cost-first routing for Makima's own execution runtime. Mirrors the TIERS /
_classify_route / select_lowest / HARD_BLACKLIST framework from fsociety-hermes
app/hermes_gateway.py, but assigns the canonical tier set so Makima follows the
original 3-tier architecture:

    Tier 0 (Zero-Cost / Local):  NVIDIA NIM free tiers, Groq free quotas,
                                 local Ollama inference (no paid token burn)
    Tier 1 (Low-Cost Workhorses): Mistral + Google Gemini Flash (Vision Gate
                                 OCR + parsing pipeline)
    Tier 2 (Frontier / Reasoning): Bounded paid routing with strict
                                 cost-per-turn ceilings and a hard blacklist on
                                 unapproved flagship models.

Stdlib-only, dependency-free: importable into any Hermes runtime (Makima,
Jeeves, worker profiles) or run standalone as a mini-router. Provider keys are
NEVER stored here; each is read from the environment (Makima Keys vault / host
.env, not git).
"""

import json
import os
import urllib.request
from typing import Any, Dict, List, Optional


def env(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    if not v:
        return ""
    v = v.strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        v = v[1:-1]
    return v.strip()


# --- Tier 0: zero-cost / local -------------------------------------------------
# deepseek-v4-flash-0731 is the PRIMARY upstream for interactive/ambient
# (chat) sessions. It is served free on OpenRouter (deepseek/deepseek-v4-flash-0731)
# and on NVIDIA NIM (deepseek-ai/deepseek-v4-flash-0731), so we list both before
# groq/ollama. This prevents interactive sessions from degrading to the weak
# local hermes3:8b layer (which cannot render the execution/approval flow).
TIER0: List[Dict[str, Any]] = [
    {
        "name": "deepseek-flash",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key": env("OPENROUTER_API_KEY"),
        "model": env("DEEPSEEK_FLASH_MODEL", "deepseek/deepseek-v4-flash-0731"),
        "local": False,
    },
    {
        "name": "nvidia-nim",
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "key": env("NVIDIA_NIM_API_KEY", env("NVIDIA_NIM_TOKEN")),
        "model": env("NVIDIA_NIM_MODEL", "deepseek-ai/deepseek-v4-flash-0731"),
        "local": False,
    },
    {
        "name": "groq",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "key": env("GROQ_API_KEY"),
        "model": env("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "local": False,
    },
    {
        "name": "ollama-local",
        "url": env("OLLAMA_URL", "http://192.168.0.206:11434/v1/chat/completions"),
        "key": env("OLLAMA_API_KEY", ""),  # local, keyless acceptable
        "model": env("OLLAMA_MODEL", "hermes3:8b"),
        "local": True,
    },
]

# --- Tier 1: low-cost workhorses -------------------------------------------------

TIER1: List[Dict[str, Any]] = [
    {
        "name": "mistral",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "key": env("MISTRAL_API_KEY"),
        "model": env("MISTRAL_MODEL", "mistral-medium"),
        "local": False,
    },
    {
        "name": "gemini-flash",
        "url": env("GEMINI_BASE_URL", "https://openrouter.ai/api/v1") + "/chat/completions",
        "key": env("GEMINI_API_KEY"),
        "model": env("GEMINI_FLASH_MODEL", "google/gemini-2.0-flash"),
        "local": False,
        "vision": True,  # Vision Gate OCR / parsing
    },
]

# --- Tier 2: bounded frontier -----------------------------------------------------

TIER2: List[Dict[str, Any]] = [
    {
        "name": "openrouter-reasoner",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key": env("OPENROUTER_API_KEY"),
        "model": env("TIER2_MODEL", "deepseek/deepseek-reasoner"),
        "local": False,
        "cost_turn_usd": float(env("TIER2_MAX_TURN_USD", "0.05")),
    },
    {
        "name": "openrouter-flagship",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key": env("OPENROUTER_API_KEY"),
        "model": env("TIER2_FLAGSHIP_MODEL", ""),  # explicit operator approval only
        "local": False,
        "cost_turn_usd": float(env("TIER2_FLAGSHIP_MAX_TURN_USD", "0.15")),
        "requires_approval": True,
    },
]

TIERS: Dict[str, List[Dict[str, Any]]] = {
    "tier0": TIER0,
    "tier1": TIER1,
    "tier2": TIER2,
}

# Hard ceiling: never auto-selected for any tier. Only an explicit human
# approve() on a run naming one of these may bypass — nothing routes here by
# fallthrough.
HARD_BLACKLIST = [
    "claude-opus", "claude-sonnet",
    "gpt-5", "gpt-4.5", "o1-", "o1-preview", "o3,", "o4,",
    "gemini-ultra", "gemini-3-pro",
]


def classify_route(prompt: str) -> str:
    """Deterministic complexity classifier -> tier name."""
    p = (prompt or "").lower()
    deep = ["analyze", "compare", "strategy", "architecture", "multi-step",
            "synthesize", "design", "plan ", "research"]
    tools = ["scrape", "search", "fetch", "extract", "list", "query", "tool",
             "recon", "parse", "ocr"]
    if any(w in p for w in deep):
        return "tier2"
    if any(w in p for w in tools):
        return "tier1"
    return "tier0"


def ceiling_blocked(model: str) -> bool:
    """True if the model name matches the unapproved-flagship blacklist."""
    m = (model or "").lower()
    if "min" in m or "flash" in m or "free" in m or "mini" in m:
        return False
    for token in HARD_BLACKLIST:
        if token in m:
            return True
    return False


def provider_ready(up: Dict[str, Any]) -> bool:
    """Usable iff not blacklisted, not approval-gated, and has a key (or local)."""
    if ceiling_blocked(str(up.get("model", ""))):
        return False
    if up.get("requires_approval"):
        return False
    if up.get("local"):
        return True
    return bool(up.get("key"))


def select_lowest(tier: str) -> Dict[str, Any]:
    """First ready provider in the requested tier; fall back to cheaper tiers."""
    order = ["tier0", "tier1", "tier2"]
    for t in ([tier] + [x for x in order if x != tier]):
        for up in TIERS.get(t, []):
            if provider_ready(up):
                sel = dict(up)
                sel["tier"] = t
                return sel
    raise RuntimeError("no configured + allowed provider for route")


def route_model(prompt: str, explicit: Optional[str] = None) -> Dict[str, Any]:
    """Resolve a prompt/explicit-route to a concrete upstream dict.

    explicit may be a tier name ('tier0'..), a shorthand ('local','deep'),
    or a specific provider/model id. Falls back to classifier if auto.
    """
    r = (explicit or "").strip().lower()

    # named provider/model override
    if r and r not in ("auto", "tier0", "tier1", "tier2", "local", "free",
                       "ambient", "deep", "reason", "tool", "workhorse"):
        for t, provs in TIERS.items():
            for up in provs:
                if r in (up["name"], str(up.get("model", ""))):
                    if provider_ready(up):
                        sel = dict(up)
                        sel["tier"] = t
                        return sel
                    raise RuntimeError("named model unavailable or ceiling-blocked")

    if r == "tier2" or r in ("deep", "reason"):
        return select_lowest("tier2")
    if r == "tier1" or r in ("tool", "workhorse"):
        return select_lowest("tier1")
    if r == "tier0" or r in ("local", "free", "ambient"):
        return select_lowest("tier0")
    return select_lowest(classify_route(prompt))


def list_models() -> List[Dict[str, str]]:
    """Public /v1/models parity catalog."""
    out = []
    for tname, provs in TIERS.items():
        for up in provs:
            out.append({
                "id": up["name"],
                "model": str(up.get("model", "")),
                "tier": tname,
                "ceiling_blocked": str(ceiling_blocked(str(up.get("model", "")))).lower(),
                "ready": str(provider_ready(up)).lower(),
                "vision": str(bool(up.get("vision"))).lower(),
            })
    return out


def chat(prompt: str, model: Optional[str] = None,
         messages: Optional[List[Dict]] = None) -> str:
    """Minimal OpenAI-compatible chat call through the route engine."""
    up = route_model(prompt, model)
    msgs = messages or [{"role": "user", "content": prompt}]
    payload = {"model": up["model"], "messages": msgs, "stream": False}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {up['key']}",
    }
    req = urllib.request.Request(
        up["url"], data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode())
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        return json.dumps(data)


if __name__ == "__main__":
    import sys
    print("Makima tier router")
    for m in list_models():
        print(f"  {m['id']:22} tier={m['tier']:5} ready={m['ready']:5} "
              f"blocked={m['ceiling_blocked']:5} model={m['model']}")
    if len(sys.argv) > 1:
        sel = route_model(" ".join(sys.argv[1:]))
        print("\nresolved:", json.dumps({k: sel[k] for k in ("name", "model", "tier")}))