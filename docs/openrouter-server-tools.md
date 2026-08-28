# OpenRouter Server Tools — Fleet Reference

> **Status:** All server tools are **Beta**. APIs and behavior may change.
> Source: <https://openrouter.ai/docs/guides/features/server-tools/>

---

## Table of Contents

1. [Overview](#overview)
2. [Server Tools vs Plugins vs User-Defined Tools](#comparison-table)
3. [The Five Fleet-Critical Server Tools](#the-five-fleet-critical-server-tools)
   - [1. Tool Search (`openrouter:tool_search`)](#1-tool-search-openroutertool_search)
   - [2. Advisor (`openrouter:advisor`)](#2-advisor-openrouteradvisor)
   - [3. Subagent (`openrouter:subagent`)](#3-subagent-openroutersubagent)
   - [4. Image Generation (`openrouter:image_generation`)](#4-image-generation-openrouterimage_generation)
   - [5. Search Models (`openrouter:experimental__search_models`)](#5-search-models-openrouterexperimental__search_models)
4. [Tool Call Limits & Control Fields](#tool-call-limits--control-fields)
5. [Usage Tracking](#usage-tracking)
6. [Quick Start](#quick-start)
7. [Practical Fleet Integration Patterns](#practical-fleet-integration-patterns)

---

## Overview

Server tools are specialized tools **operated by OpenRouter** that any model can call during a request. When a model decides to use a server tool, OpenRouter executes it server-side and returns the result to the model — no client-side implementation needed.

You include server tools in the `tools` array of your API request. The model decides whether and when to call each tool. OpenRouter intercepts the tool call, executes it, and returns the result. The model uses the result to formulate its response and may call the tool again if needed.

Server tools work alongside your own user-defined tools — you can include both in the same request.

---

## Comparison Table

| Dimension | Server Tools | Plugins | User-Defined Tools |
|---|---|---|---|
| **Who decides to use it** | The model | Always runs | The model |
| **Who executes it** | OpenRouter | OpenRouter | Your application |
| **Call frequency** | 0–N times per request | Once per request | 0–N times per request |
| **Specified via** | `tools` array | `plugins` array | `tools` array |
| **Type prefix** | `openrouter:*` | N/A | `function` |

- **Server tools** are tools the model can invoke zero or more times during a request. OpenRouter handles execution transparently.
- **Plugins** inject or mutate a request or response to add functionality (e.g. response healing, PDF parsing). They always run once when enabled.
- **User-defined tools** are standard function-calling tools where the model suggests a call and *your* application executes it.

---

## The Five Fleet-Critical Server Tools

### 1. Tool Search (`openrouter:tool_search`)

**Purpose:** Deferred tool loading to eliminate schema bloat (>10k tokens in tool definitions).

The `openrouter:tool_search` server tool lets a model work with a large tool library without paying for it on every request. You mark tools to keep hidden with `defer_loading: true`, and the model searches for what it needs when it needs it.

This matters at scale for two reasons:
- Tool definitions are charged as **input tokens on every turn**, so a large library is a fixed cost on every request whether or not the model uses any of it.
- **Tool selection accuracy degrades** as the list grows — a model choosing between several hundred similar tools picks wrong more often than one choosing between five.

#### Key Behaviors

| Aspect | Detail |
|---|---|
| **Search method** | Regex (case-insensitive) matched against tool name, description, argument names, and argument descriptions |
| **Pattern cap** | 200 characters max |
| **`max_results` default** | 5 (capped at 50) |
| **Works on** | Responses API and Messages API only — **NOT Chat Completions** |
| **Prompt caching** | Preserved across searches (discovered tools don't disturb the cache) |
| **Tool search itself** | **Cannot** be deferred (would leave nothing to load anything) |

#### Marking Tools as Deferred

Add `defer_loading: true` to any tool you want withheld. Deferred tools are hidden by default — the model cannot see or call one until a search returns it.

```
{ "defer_loading": true }   ← on any function tool definition
```

**Best practice:** Keep your **3–5 most frequently used tools loaded**. A tool the model needs on almost every request costs more in search round-trips than it saves in tokens.

#### API-Specific Type Aliases

| API | Accepted `type` values |
|---|---|
| Responses | `openrouter:tool_search`, `tool_search` |
| Messages | `openrouter:tool_search`, `tool_search_tool_regex`, `tool_search_tool_regex_20251119` |

> The BM25 variant (`tool_search_tool_bm25_20251119`) is **not yet supported** — use `openrouter:tool_search` or the regex variant instead.

#### Tool Choice Compatibility

- If `tool_choice` is omitted or `"auto"`, nothing special is required.
- If you set `tool_choice`, it **must** be `{"type": "allowed_tools", ...}` naming immediately-callable tools. Deferred tools are added to that set as the model finds them.
- Any other `tool_choice` (forcing a specific tool, requiring a call, forbidding calls) conflicts with deferral and will **fail with a 400**.

#### Naming Best Practices

Write tool descriptions in the words your users actually use. Use consistent name prefixes (`github_issues_list`, `github_pulls_list`) so one search reaches the whole group.

---

### 2. Advisor (`openrouter:advisor`)

**Purpose:** Consult a stronger/higher-intelligence model for guidance mid-generation.

The `openrouter:advisor` server tool lets a model consult a higher-intelligence **advisor model** mid-generation. When your model hits a decision point — before committing to an approach, when it's stuck, or before declaring a task done — it invokes the tool with a `prompt`. The advisor model thinks, returns its guidance as the tool result, and your model continues, informed by the advice.

The advisor can be **any OpenRouter model**. The tool returns the advisor model's response directly as the tool result; your model writes the final answer.

#### When to Use

Use the advisor for **mid-generation validation before high-stakes actions**:
- Mutations (database writes, file changes)
- `git push` and other remote operations
- Container lifecycle operations (create, start, stop, delete)
- Cross-node changes across fleet infrastructure

#### Configuration Parameters

| Parameter | Default | Description |
|---|---|---|
| `name` | None (default advisor) | Optional name for this advisor. At most one entry may omit `name` to act as the default. |
| `model` | Outer request model | The advisor model to consult (any OpenRouter model). |
| `instructions` | None | System instructions for the advisor sub-agent. |
| `forward_transcript` | `false` | When `true`, the full parent conversation is forwarded to the advisor. |
| `stream` | `false` | When `true`, advice streams incrementally (Responses API only). |
| `max_completion_tokens` | Provider default | Max output tokens (including reasoning) for the advisor call. |
| `reasoning` | Provider default | Reasoning config: `{ effort, max_tokens }`. |
| `temperature` | Provider default | Sampling temperature (0–2). |

#### Tool-Call Arguments (what the model passes)

| Argument | Description |
|---|---|
| `prompt` | What the model wants advice on. Required unless `forward_transcript` is `true`. |
| `model` | The advisor model to use. Only honored when the tool definition does not fix a model. |

#### Inner Agent Loop Budget

| Tool | Parameter | Default | Max |
|---|---|---|---|
| **Advisor** | `max_tool_calls` | Provider default | **25** |

This budget is independent of the outer request's `max_tool_calls` budget.

#### Multiple Advisors

You can offer the model a choice of **several named advisors** by including multiple `openrouter:advisor` entries in the `tools` array, one per advisor. Each named advisor appears as a distinct tool the model can choose.

**Rules:**
- At most one entry may omit `name` (it becomes the default advisor).
- Names must be **unique** across entries.
- Names allow letters, digits, spaces, underscores, dashes; trimmed; 1–64 chars.
- Forcing with `tool_choice` targets the **first** advisor entry.

#### Cross-Request Memory

Each advisor remembers its own prior `prompt → advice` exchanges **across API requests** in a conversation. Replay the prior transcript (assistant messages with advisor tool calls and results included) and the advisor sees its earlier consultations.

- Memory is **per advisor** — a "reviewer" advisor never sees what the "architect" was told.
- If history exceeds the advisor model's context window, it is compressed with the **middle-out transform** (keeps oldest and newest, trims the middle).
- **Keep advisor entry order stable** across requests — reordering shifts identities.

#### Response Format

On success:
```json
{
  "status": "ok",
  "advice": "... guidance text ...",
  "model": "anthropic/claude-opus-4.5"
}
```

On failure:
```json
{
  "status": "error",
  "error": "Advisor call failed: ..."
}
```
The calling model continues without the advice on failure.

---

### 3. Subagent (`openrouter:subagent`)

**Purpose:** Delegate self-contained tasks to a smaller, faster worker model to preserve context.

The `openrouter:subagent` server tool lets a model delegate self-contained tasks to a smaller, cheaper, faster **worker model** mid-generation. When your model has work that doesn't need its full capability (summarizing a document, extracting structured data, drafting boilerplate, reformatting text), it invokes the tool with a `task_name` and a `task_description`. The worker executes and returns its result as `outcome`.

**Key difference from Advisor:** The worker is **fixed by the tool definition** (the delegating model does not choose per call). The subagent tool can never be the worker model itself.

#### When to Use

Use the subagent for **isolated execution to preserve context**:
- Summarizing large documents
- Extracting structured data from text
- Drafting boilerplate code or text
- Reformatting content
- Any focused sub-task that doesn't need the parent conversation

#### Configuration Parameters

| Parameter | Default | Description |
|---|---|---|
| `model` | Outer request model | The worker model (any OpenRouter model). Typically smaller/cheaper/faster. |
| `tools` | None | Tools available to the worker. Only OpenRouter server tools supported; function tools are rejected with 400. |
| `inherit_functions` | `false` | **Experimental** — worker inherits every client function from top-level tools. Responses API only. |
| `inherited_function_names` | None | **Experimental** — names of specific function tools to inherit. Responses API only. |
| `instructions` | None | System instructions for the worker. |
| `max_tool_calls` | Provider default | Max tool-calling steps the worker may take. Range 1–25. |
| `max_completion_tokens` | Provider default | Max output tokens (including reasoning) for the worker call. |
| `reasoning` | Provider default | Reasoning config: `{ effort, max_tokens }`. |
| `temperature` | Provider default | Sampling temperature (0–2). |

#### Tool-Call Arguments (what the model passes)

| Argument | Description |
|---|---|
| `task_name` | Short identifier (e.g. `summarize-changelog`). |
| `task_description` | Everything the worker needs: full context, inputs, constraints, expected output format. The worker sees **only this**. |

#### Inner Agent Loop Budget

| Tool | Parameter | Default | Max |
|---|---|---|---|
| **Subagent** | `max_tool_calls` | Provider default | **25** |

#### Worker Tools

When you pass `tools`, the worker runs as an **agentic sub-agent** over them. For example, giving the worker `openrouter:web_search` lets it ground its result in fresh sources. Only the worker's **final text** is returned to your model.

Nested tools must be OpenRouter server tools. Client function tools placed in the nested `tools` array are rejected with a 400.

#### Response Format

On success:
```json
{
  "status": "ok",
  "model": "anthropic/claude-haiku-4.5",
  "task_name": "summarize-changelog",
  "outcome": "Release 2.4 highlights: 1) New streaming API..."
}
```

On failure:
```json
{
  "status": "error",
  "task_name": "summarize-changelog",
  "error": "Subagent call failed: ..."
}
```

---

### 4. Image Generation (`openrouter:image_generation`)

**Purpose:** Generate images from text prompts with any model.

The `openrouter:image_generation` server tool enables any model to generate images from text prompts. When the model determines it needs to create an image, it calls the tool with a description. OpenRouter executes the image generation and returns the result.

#### How It Works

1. You include `{ "type": "openrouter:image_generation" }` in your `tools` array.
2. Based on the user's request, the model decides whether image generation is needed and crafts a prompt.
3. OpenRouter generates the image using the configured model (defaults to `openai/gpt-5-image`).
4. The generated image URL is returned to the model.
5. The model incorporates the image into its response. It may generate **multiple images** in a single request.

#### Configuration Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | string | `openai/gpt-5-image` | Image generation model to use. |
| `quality` | string | N/A | Image quality level (e.g. `"low"`, `"medium"`, `"high"`). |
| `size` | string | N/A | Image dimensions (e.g. `"1024x1024"`, `"512x512"`). |
| `aspect_ratio` | string | N/A | Aspect ratio (e.g. `"16:9"`, `"1:1"`, `"4:3"`). |
| `background` | string | N/A | Background style (e.g. `"transparent"`, `"opaque"`). |
| `output_format` | string | N/A | Output format (e.g. `"png"`, `"jpeg"`, `"webp"`). |
| `output_compression` | number | N/A | Compression level (0–100) for lossy formats. |
| `moderation` | string | N/A | Content moderation level (e.g. `"auto"`, `"low"`). |

All parameters except `model` are passed directly to the underlying image generation API.

#### Response

The generated image appears in the API response at:
```
choices[0].message.images[0].image_url.url
```
The URL is a **base64 data-URL** (e.g. `data:image/png;base64,...`).

On success (tool result):
```json
{
  "status": "ok",
  "imageUrl": "https://..."
}
```

On failure:
```json
{
  "status": "error",
  "error": "..."
}
```

---

### 5. Search Models (`openrouter:experimental__search_models`)

**Purpose:** Search and filter the OpenRouter model catalog at runtime.

The `openrouter:experimental__search_models` server tool lets a model search and filter the OpenRouter model catalog. The model can look up models by name, capabilities, modalities, and other attributes — useful for agents that pick models dynamically (e.g. combined with the Subagent tool).

> **Note:** This tool is experimental. When it graduates, the tool type is likely to be renamed (dropping the `experimental__` prefix), which would be a breaking change.

#### Configuration Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_results` | integer | 5 | Maximum number of models to return per call. Between 1 and 20. |

#### Call Arguments (what the model passes)

All fields are optional — an empty call browses the full catalog:

| Field | Type | Description |
|---|---|---|
| `query` | string | Free-text search matched against model names, slugs, and descriptions. |
| `input_modalities` | string[] | Filter by input modalities (`text`, `image`, `file`, `audio`, `video`). Returns models supporting ALL specified. |
| `output_modalities` | string[] | Filter by output modalities (`text`, `image`, `embeddings`, `audio`). Returns models supporting ALL specified. |
| `min_context_length` | integer | Minimum context length in tokens. |
| `series` | string | Filter by model series/family (e.g. `Claude`, `GPT`, `Gemini`). |

#### Response

Returns matching models with key attributes:

```json
{
  "results": [...],
  "total_results": 42,
  "showing": 5
}
```

- `total_results` = number of models matching the filters
- `showing` = how many returned after applying `max_results`

**Pricing:** No separate charge — only standard token usage.

---

## Tool Call Limits & Control Fields

Every request using server tools runs an **agent loop** with a step budget. Each tool call consumes one step; when the budget is exhausted, the model is asked to produce its final answer with the context gathered so far.

### Outer Loop Controls

These are top-level request fields (siblings of `messages` and `tools`):

| Field | Default | Max | Behavior |
|---|---|---|---|
| `max_tool_calls` | 30 | 30 | Total server-tool steps allowed for the request, across **all** server tools. |
| `stop_server_tools_when` | None | None | Array of stop conditions (step count, spend cap, and more). When set, **overrides** `max_tool_calls`. |

### Inner Agent Loop Budgets

Tools that run their own inner agent loops have **separate, per-tool budgets** configured via the tool's `parameters`:

| Tool | Parameter | Default | Max |
|---|---|---|---|
| Fusion | `max_tool_calls` | 4 | 16 |
| **Advisor** | `max_tool_calls` | Provider default | **25** |
| **Subagent** | `max_tool_calls` | Provider default | **25** |

These inner budgets bound each advisor, subagent, or panelist's own tool loop and are **independent** of the outer request budget.

---

## Usage Tracking

Server tool usage is tracked in the response `usage` object:

```json
{
  "usage": {
    "prompt_tokens": 500,
    "completion_tokens": 150,
    "total_tokens": 650,
    "server_tool_usage": {
      "openrouter:web_search": 2,
      "openrouter:tool_search": 1
    }
  }
}
```

The `server_tool_usage` field shows each server tool used and how many times it was called during the request.

---

## Quick Start

### Minimal: Tool Search with Deferred Loading

```json
{
  "model": "openai/gpt-5.2",
  "messages": [
    { "role": "user", "content": "What's the weather in Tokyo?" }
  ],
  "tools": [
    { "type": "openrouter:tool_search" },
    {
      "type": "function",
      "name": "get_weather",
      "description": "Get the current weather for a city.",
      "parameters": {
        "type": "object",
        "properties": { "city": { "type": "string" } },
        "required": ["city"]
      },
      "defer_loading": true
    }
  ]
}
```

The model searches for `weather`, finds `get_weather`, and calls it on the next turn. You handle that call exactly as you would any other function tool call — deferral changes *when* a tool becomes available, not *how* it works once it does.

### With Advisor for High-Stakes Validation

```json
{
  "model": "anthropic/claude-sonnet-4",
  "messages": [...],
  "tools": [
    {
      "type": "openrouter:advisor",
      "parameters": {
        "model": "~anthropic/claude-opus-latest",
        "instructions": "You are a senior staff engineer. Be decisive.",
        "forward_transcript": false
      }
    },
    { "type": "function", "name": "git_push", "..." : "..." }
  ],
  "max_tool_calls": 30
}
```

### With Subagent for Delegation

```json
{
  "model": "openai/gpt-5.2",
  "messages": [...],
  "tools": [
    {
      "type": "openrouter:subagent",
      "parameters": {
        "model": "~anthropic/claude-haiku-latest",
        "instructions": "You are a fast, focused worker. Complete the task exactly as described.",
        "tools": [{ "type": "openrouter:web_search" }]
      }
    }
  ]
}
```

### Combining with User-Defined Tools

Server tools and user-defined tools can be used in the same request. The model can call any combination — OpenRouter executes the server tools automatically, while your application handles the user-defined tool calls as usual.

```json
{
  "model": "openai/gpt-5.2",
  "messages": [{ "role": "user", "content": "..." }],
  "tools": [
    { "type": "openrouter:web_search" },
    {
      "type": "function",
      "name": "get_database",
      "description": "Query the local database.",
      "parameters": { "type": "object", "properties": { "query": { "type": "string" } } }
    }
  ]
}
```

---

## Practical Fleet Integration Patterns

### Pattern 1: OmniRouter Gateway — Forwarding Server Tools

Makima's OmniRouter gateway (`saitama_gateway.py`) acts as the fleet's unified LLM gateway. It forwards OpenRouter server tools transparently because server tools are specified in the `tools` array with the `openrouter:` prefix — OpenRouter intercepts and executes them server-side.

**How it works:**
1. Fleet agents (Hermes, Codex, Claude Code) send requests through OmniRouter to OpenRouter.
2. The `tools` array includes `openrouter:*` entries alongside fleet-specific function tools.
3. OpenRouter intercepts server tool calls and executes them; the gateway passes the results back.
4. Client function tool calls are handled by the fleet agents themselves.

**Gateway forwarding rules:**
- Preserve the `tools` array unchanged — do not strip `openrouter:*` entries.
- Pass through `max_tool_calls` and `stop_server_tools_when` control fields.
- The gateway itself never executes server tools; OpenRouter does.

### Pattern 2: Wiring Tool Search into Hermes Config

For fleet agents with large tool libraries (Hermes with 20+ skills/tools), use `openrouter:tool_search` to reduce token consumption:

**Configuration approach:**
1. **Always-loaded tools (3–5):** Core tools used on nearly every request (e.g., `terminal`, `read_file`, `write_file`) — do NOT set `defer_loading` on these.
2. **Deferred tools:** Everything else gets `defer_loading: true`.
3. **Add `openrouter:tool_search`** as the first entry in the `tools` array.
4. **Ensure Responses or Messages API** is used (not Chat Completions).

**Example fleet config:**
```json
{
  "tools": [
    { "type": "openrouter:tool_search" },
    { "type": "function", "name": "terminal", "..." : "..." },
    { "type": "function", "name": "read_file", "..." : "..." },
    { "type": "function", "name": "write_file", "..." : "..." },
    { "type": "function", "name": "browser_navigate", "defer_loading": true, "..." : "..." },
    { "type": "function", "name": "session_search", "defer_loading": true, "..." : "..." },
    { "type": "function", "name": "skill_manage", "defer_loading": true, "..." : "..." }
  ]
}
```

**Expected token savings:** For a library with 20 tools at ~500 tokens each, deferring 15 tools saves ~7,500 input tokens per request. The model pays for a search round-trip (~200 tokens) only when it needs a deferred tool.

### Pattern 3: Advisor + Subagent Synergy

Combine the advisor and subagent tools for a powerful **validate-then-execute** pattern:

**Architecture:**
```
User Request → Main Model (strong reasoning)
                  ├→ Advisor (even stronger model) — validates decisions
                  ├→ Subagent (fast worker) — executes isolated tasks
                  └→ Main Model — integrates results into final response
```

**Use case: Fleet infrastructure deployment**
1. Main model plans the deployment steps.
2. Before executing risky mutations, main model calls **advisor** with `~anthropic/claude-opus-latest` for validation.
3. For data extraction, summarization, or boilerplate generation, main model delegates to **subagent** with `~anthropic/claude-haiku-latest`.
4. Main model integrates advisor guidance and subagent results into the final deployment action.

**Configuration:**
```json
{
  "model": "anthropic/claude-sonnet-4",
  "tools": [
    {
      "type": "openrouter:advisor",
      "parameters": {
        "model": "~anthropic/claude-opus-latest",
        "instructions": "You are reviewing infrastructure changes. Flag risks. Be specific."
      }
    },
    {
      "type": "openrouter:subagent",
      "parameters": {
        "model": "~anthropic/claude-haiku-latest",
        "instructions": "You are a focused worker. Extract and format exactly what is asked.",
        "tools": [{ "type": "openrouter:web_search" }]
      }
    },
    { "type": "function", "name": "deploy", "..." : "..." },
    { "type": "function", "name": "git_push", "..." : "..." }
  ],
  "max_tool_calls": 30
}
```

**Budget math:** The outer budget (30 steps) counts advisor and subagent calls. Each has its own inner budget (up to 25). A typical deployment request might use: 2 advisor calls + 3 subagent calls + 5 function calls = 10 of 30 outer steps.

---

## Reference: All Available Server Tools

| Tool | Type | Description |
|---|---|---|
| Web Search | `openrouter:web_search` | Search the web for current information |
| Web Fetch | `openrouter:web_fetch` | Fetch and extract content from URLs |
| Datetime | `openrouter:datetime` | Get the current date and time |
| Image Generation | `openrouter:image_generation` | Generate images from text prompts |
| Apply Patch | `openrouter:apply_patch` | Propose file edits via V4A diff patches (Responses API only) |
| Shell | `openrouter:shell` | Run commands in a hosted, sandboxed shell (Responses & Messages APIs) |
| Bash | `openrouter:bash` | Anthropic-style bash tool with optional sandboxed execution (Messages API only) |
| Containers | `openrouter:containers` | Container lifecycle management |
| Fusion | `openrouter:fusion` | Run a panel of models and an analyst for multi-model analysis |
| **Advisor** | `openrouter:advisor` | Consult a stronger model for guidance mid-generation |
| **Subagent** | `openrouter:subagent` | Delegate self-contained tasks to a smaller, faster worker model |
| **Search Models** | `openrouter:experimental__search_models` | Search and filter the OpenRouter model catalog |
| **Tool Search** | `openrouter:tool_search` | Discover and load deferred tools on demand (Responses & Messages APIs) |

---

*Last updated: 2026-08-27. All tools are Beta — behavior may change.*
*Documentation sourced from: https://openrouter.ai/docs/guides/features/server-tools/*
