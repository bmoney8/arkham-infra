# OpenRouter Server Tools & Inference Reference
> Consolidated reference for the Arkham fleet. Source: https://openrouter.ai/docs/guides/features/server-tools
> Maintained by Makima Core (bmoney8/arkham-infra). Last updated: 2026-08-27 (V5.5).

## Overview

Server Tools are model-callable tools operated by OpenRouter that any model can call during a request.
When a model decides to use a server tool, OpenRouter executes it server-side and returns the result to the model — no client-side implementation needed.

Server tools work alongside user-defined tools. You can include both in the same request.
Tool type prefix: `openrouter:*` (distinguishes from user-defined `function` tools).

## Available Server Tools

| Tool | Type | Description |
|------|------|-------------|
| **Tool Search** | `openrouter:tool_search` | Discover and load deferred tools on demand (Responses + Messages API) |
| **Advisor** | `openrouter:advisor` | Consult a stronger model for guidance mid-generation |
| **Subagent** | `openrouter:subagent` | Delegate self-contained tasks to a smaller, faster worker model |
| **Image Generation** | `openrouter:image_generation` | Generate images from text prompts |
| **Web Search** | `openrouter:web_search` | Search the web for current information |
| **Web Fetch** | `openrouter:web_fetch` | Fetch and extract content from URLs |
| **Datetime** | `openrouter:datetime` | Get the current date and time |
| **Search Models** | `openrouter:experimental__search_models` | Search and filter the OpenRouter model catalog |
| **Fusion** | `openrouter:fusion` | Run a panel of models and an analyst for multi-model analysis |
| **Apply Patch** | `openrouter:apply_patch` | Propose file edits via V4A diff patches (Responses API only) |
| **Shell** | `openrouter:shell` | Run commands in a hosted, sandboxed shell (Responses + Messages API) |
| **Bash** | `openrouter:bash` | Anthropic-style bash tool (Messages API only) |
| **Containers** | `openrouter:containers` | Run code in sandboxed containers |

## Tool Search Integration (`openrouter:tool_search`)

**Purpose:** Defer tool schema loading — only pay for tool definition tokens when the model actually needs them. Eliminates schema bloat (saves >10k tokens on large tool libraries).

**How it works:**
1. Include `openrouter:tool_search` in the `tools` array alongside your own tools
2. Mark tools you want hidden with `"defer_loading": true`
3. Model searches by regex against tool names, descriptions, and arguments
4. Found tools are loaded into context dynamically

**Key rules:**
- `openrouter:tool_search` itself CANNOT be deferred
- `tool_choice` must be omitted or set to `{"type": "allowed_tools", ...}` — cannot force/restrict tool calls with deferral
- Keep 3-5 most-used tools loaded (defer_loading: false); tools needed every request cost more in search round-trips than they save
- Prompt caching is preserved across searches
- **API restriction:** Responses API and Messages API only. Chat Completions API returns 400.

**Request shape:**
```json
{
  "model": "openai/gpt-5.6-luna",
  "messages": [...],
  "tools": [
    { "type": "openrouter:tool_search" },
    {
      "type": "function",
      "name": "my_tool",
      "description": "...",
      "parameters": { ... },
      "defer_loading": true
    }
  ]
}
```

**Configuration:** `max_results` (default 5, max 50) — maximum tools returned by a single search.

**Makima fleet relevance:** The OmniRouter gateway proxies requests to OpenRouter. Tool search is transparent — just include it in the tools array. For Hermes clients that use Chat Completions API, tool_search is NOT available (use Responses API endpoint or load all tools upfront). The Saitama gateway currently uses `/v1/chat/completions` — a Responses API passthrough would need gateway modification.

## Advisor Integration (`openrouter:advisor`)

**Purpose:** Consult a stronger model mid-generation for guidance before committing to high-stakes actions (mutations, git push, container lifecycle, cross-node changes).

**How it works:**
1. Model hits a decision point and invokes the advisor tool with a `prompt`
2. Advisor model thinks, returns guidance as tool result
3. Continuing model uses the guidance to formulate response

**Parameters:**
| Field | Default | Description |
|-------|---------|-------------|
| `name` | None (default advisor) | Optional name for named advisors (unique, 1-64 chars) |
| `model` | Outer request model | Any OpenRouter model (e.g., `deepseek/deepseek-v4-pro`) |
| `instructions` | None | System instructions for the advisor sub-agent |
| `forward_transcript` | false | When true, forwards full parent conversation to advisor |
| `stream` | false | Stream advice incrementally (Responses API only) |

**Multiple advisors:** Include multiple `openrouter:advisor` entries with different `name` values. Model picks the right advisor per situation.

**Cross-request memory:** Advisor remembers prior consultations when conversation transcript is replayed.

**Fleet use case (recommended tier mapping):**
- Primary execution: Tier 0 (mimo-v2.5 / glm-5.3-flash)
- Advisor for high-stakes actions: Tier 1 (deepseek-vision-exp / gpt-luna)
- Heavy reasoning advisory: Tier 2-3 (deepseek-pro / gpt-terra / gemini-flash)

## Subagent Integration (`openrouter:subagent`)

**Purpose:** Delegate self-contained tasks to a smaller, faster worker model. Preserves context for the orchestrator while offloading execution.

**How it works:**
1. Model delegates a task via the subagent tool
2. Worker model executes in isolation with its own tool loop
3. Worker returns results to orchestrator

**Per-tool budget:** `max_tool_calls` (default: provider default, max: 25). Independent of outer request budget.

**Fleet relevance:** Enables hierarchical execution — Makima (orchestrator) delegates isolated execution to subagents via OpenRouter, preserving context. Combined with advisor for mid-generation validation before committing mutations.

## Image Generation (`openrouter:image_generation`)

**Purpose:** Generate images from text prompts, executed server-side.

**Existing fleet integration:** fsociety-hermes `generate_image` tool already uses OpenRouter `google/gemini-3.1-flash-image` via `/chat/completions`. The server tool provides a simpler integration path — no need to extract base64 data-URLs manually.

## Web Search (`openrouter:web_search`)

**Purpose:** Search the web for current information. Model invokes during generation when it needs up-to-date data.

**Makima fleet relevance:** Eliminates need for separate search tooling. The osint_recon.py (now in makima-hermes/scripts/) provides deeper domain-specific recon; web_search handles general queries.

## Search Models (`openrouter:experimental__search_models`)

**Purpose:** Search and filter the OpenRouter model catalog programmatically. Useful for dynamic model selection and tier updates.

## Tool Call Limits

| Field | Default | Max | Behavior |
|-------|---------|-----|----------|
| `max_tool_calls` | 30 | 30 | Total server-tool steps across all tools per request |
| `stop_server_tools_when` | None | None | Array of stop conditions (overrides max_tool_calls) |

Inner budgets per tool (independent of outer):
- Fusion: max_tool_calls default 4, max 16
- Advisor: default provider, max 25
- Subagent: default provider, max 25

## Integration Architecture (Arkham Fleet)

```
Makima Client (Hermes/Dashboard)
    |
    v
OmniRouter Gateway (Saitama 100.64.0.3:8000)
    |  [tier routing: tier0-3 + vision cascade]
    v
OpenRouter API
    |  [server tools executed here]
    v
Response (with server tool results inline)
```

Server tools are transparent to the OmniRouter — they flow through as part of the standard API request. The gateway does not need modification to support them; they're activated by including the appropriate `tools` entries.

## Constraints & Gotchas

1. **tool_search requires Responses/Messages API** — Chat Completions returns 400. If Hermes or a client uses `/v1/chat/completions`, tool_search won't work. Responses API passthrough would need gateway modification.
2. **Advisor models can be expensive** — `forward_transcript: true` sends the full conversation. Use judiciously.
3. **Subagent inner loops consume tokens** — worker model tokens are charged separately. Budget-conscious fleets should set `max_tool_calls` bounds.
4. **All server tools are Beta** — API and behavior may change. Monitor changelog.
