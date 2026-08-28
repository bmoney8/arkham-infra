# Makima Tier-YAML Replication — Config Map

Declarative tier map for `app/makima_router.py`. Mirrors the fsociety
hermes_gateway tier assignments (see `app/hermes_gateway.py`). This is the
"replicated-into-Makima-runtime" profile. Provider key values live only in env
/ Makima Keys vault (never in git).

## tier0 — Zero-Cost / Local

| id            | endpoint                                             | model                                     | auth                 | tier   |
|---------------|------------------------------------------------------|-------------------------------------------|----------------------|--------|
| deepseek-flash| https://openrouter.ai/api/v1/chat/completions         | deepseek/deepseek-v4-flash-0731 (PRIMARY, free) | env OPENROUTER_API_KEY | FREE tier |
| nvidia-nim    | https://integrate.api.nvidia.com/v1/chat/completions | deepseek-ai/deepseek-v4-flash-0731 (live) | env NVIDIA_NIM_API_KEY | FREE credits |
| groq          | https://api.groq.com/openai/v1/chat/completions       | llama-3.3-70b-versatile                   | env GROQ_API_KEY     | FREE quota |
| ollama-local  | http://192.168.0.206:11434/v1/chat/completions        | hermes3:8b                                | keyless              | LOCAL |

## tier-1 — Low-Cost Workhorses

| id           | provider | endpoint                                   | model                           | auth             | notes |
|--------------|----------|--------------------------------------------|---------------------------------|------------------|-------|
| mistral      | Mistral  | https://api.mistral.ai/v1/chat/completions    | mistral-medium (verified)       | env MISTRAL_API_KEY | workhorse |
| gemini-flash | Gemini   | https://openrouter.ai/api/v1/chat/completions  | google/gemini-2.0-flash          | env GEMINI_API_KEY | Vision Gate OCR/parsing |

## tier-2 — Frontier / Reasoning (BOUNDED)

| id | endpoint | model | cost_turn_usd | gate |
|----|----------|-------|---------------|------|
| openrouter-reasoner | https://openrouter.ai/api/v1/chat/completions | deepseek/deepseek-reasoner | 0.05 (ceiling) | auto |
| openrouter-flagship | https://openrouter.ai/api/v1/chat/completions | (explicit only) | 0.15 | operator approve() |

## Blacklist (HARD ceiling — never auto-selected)

claude-opus, claude-sonnet, gpt-5, gpt-4.5, o1-/o1-preview/o3/o4,
gemini-ultra, gemini-3-pro.

## Runtime wiring

- Router: `app/makima_router.py` (stdlib-only; importable into any Hermes
  profile: Makima, Jeeves, worker).
- `python app/makima_router.py` prints readiness; keys read from env at import.
- Verified live 2026-08-21: Tier0 (NVIDIA NIM) and Tier1 (Mistral) return real
  completions; blacklist refuses flagships; flagship model requires approval.