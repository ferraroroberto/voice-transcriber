# Agentic polish — turn polish into a tool-using agent

> **Status:** plan only, no code yet.
> **Scope:** the polish step in this repo's pipeline (`src/polish.py`) plus a small tool-host service in `local-llm-hub`.
> **Goal:** transform polish from a one-shot "rewrite this transcript" call into a small agent that can call tools — calendar, contacts, Notion, web — to **resolve and enrich** the transcript instead of just cleaning it.

---

## TL;DR

Today polish removes fillers. That's its entire job. But every dictation contains references the LLM doesn't have context for: "remind me about the meeting Tuesday", "send this to John", "add to the Q3 roadmap". An agentic polish loop can:

- Resolve "Tuesday" → "Tuesday, May 12, 2026" by calling a date tool.
- Disambiguate "John" → "John Smith (john@…)" by calling a contacts tool.
- Cross-link "Q3 roadmap" → "[Q3 Roadmap](notion://…)" by searching Notion.
- Fact-check a claim with a web tool.

**The exercise is the point**, not the feature. You'd be hand-rolling: a tool-use loop, tool registration, tool sandboxing/timeouts, the cost-vs-utility tradeoff per tool call, telling-the-user-what-the-agent-did, fallback when a tool fails. These are the exact mechanics every enterprise "AI agent" pilot wrestles with. After this you'll be able to read any LangChain/LangGraph/Bedrock-Agents pitch and immediately know what's vapor.

**Difficulty: 4/5.** Code volume is modest. The hard part is *taste* — when should the agent call a tool, when should it leave text alone, how do you avoid it "improving" things the user didn't want improved.

**Career relevance: very high.** Agent design is the most-discussed and least-understood capability in 2026 AI transformation work. Building one end-to-end on your own data is the fastest path to an opinion you can defend.

---

## The core design tension you'll face (and the lesson)

Polish today has a strict prompt: "do not summarise, do not rephrase, do not add ideas". An agent that calls tools and inserts results **violates that contract** by definition — adding a resolved date is "adding an idea", strictly.

So agentic-polish is **not the same product** as polish. It's a sibling. The cleanest way to ship this without breaking your existing flow is **two polish styles**:

- `filler-words` — current, untouched, non-agentic.
- `enrich` — agentic, can call tools, can add precision (dates, links, contact names) but never *opinion*.

The user picks per-take. Your existing polish-style dropdown architecture (per `docs/2026-05-08-multi-prompt-polish-and-webapp-ui.md`) is already the right home for this — no new UI needed, just a new entry in `config/polish_prompts.json` that flips a `"agentic": true` flag.

**The lesson:** agents are not a drop-in upgrade for non-agent flows. They have different contracts, different failure modes, different latency profiles, different cost. Treat them as a separate product surface. This is the #1 mistake enterprise pilots make and the #1 reason they regress on quality.

---

## Architecture

Two viable agent loops; pick one.

### Option A — Claude API tool-use (recommended for this learning exercise)

Use Anthropic's native tool-use loop with `claude-haiku-4-5` (already wired through `local-llm-hub`). Why: it's the cleanest tool-use API in the industry, well-documented, and the patterns transfer directly to Bedrock / Vertex / OpenAI.

```
                voice-transcriber
                ┌──────────────────┐
                │ /api/sessions/   │
                │  {id}/polish     │
                │  body: style=    │
                │  "enrich"        │
                └────────┬─────────┘
                         │
                         ▼
                local-llm-hub
                ┌──────────────────┐
                │ /v1/agents/      │   ◄── new endpoint
                │  enrich          │
                │  - tool registry │
                │  - loop runner   │
                │  - tracer        │
                └────────┬─────────┘
                         │ Anthropic SDK tool-use loop
                         ▼
                ┌──────────────────┐      ┌──────────────────────┐
                │ Claude haiku 4.5 │ ◄──► │ Tools (HTTP servers) │
                │  - reads input   │      │  - date-resolver     │
                │  - chooses tool  │      │  - contacts          │
                │  - reads result  │      │  - notion-search     │
                │  - finalises out │      │  - (later) web       │
                └──────────────────┘      └──────────────────────┘
```

### Option B — Local llama.cpp + structured output

Same loop, but driven by `gemma4-26b-a4b-it` running locally with a JSON-schema-constrained output. Cheaper, fully offline, but tool-calling on small open models is **noticeably worse** than on Claude — they hallucinate tool args, get stuck in loops, miss obvious cases. Good for production once tuned; bad for *learning* what good looks like, because you'll spend half your time fighting the model instead of designing the agent.

**Recommendation: start with Option A. Once you've felt what good agent behavior is, port to Option B as a fast-follow if you care.**

---

## The tools

Start with three. Add more only after you've used them for a week.

### Tool 1 — `resolve_date`

Input: `text: str`, `reference_date: str (ISO)`.
Output: `{matches: [{phrase, resolved_iso, confidence}]}`.

Implementation: a 50-line Python function over `dateparser` + a few hand-rolled rules for "next Tuesday", "EOW", "Q3", "tomorrow morning". No LLM in the tool — *deterministic tools are better tools*.

**Lesson:** the temptation will be to make every tool an LLM call. Resist. Deterministic tools are auditable, fast, cheap, and the agent uses them better because their outputs are predictable.

### Tool 2 — `lookup_contact`

Input: `name: str`.
Output: `{candidates: [{name, email, source, last_contacted}]}`.

Source: your Google Contacts (via the existing OAuth on the home PC) or a flat `~/contacts.json` you maintain. Tool returns top-3 candidates ranked by recency. The **agent** decides which (if any) to inline.

### Tool 3 — `search_notion`

Input: `query: str`, `top_k: int`.
Output: `{results: [{title, url, snippet}]}`.

Source: Notion API. You already have Notion MCP integration available (per the available tools in this session). The lift is wrapping it as a stable HTTP tool the agent can call.

### Tool gateway

All three live behind one gateway service in `local-llm-hub`:

```
POST /tools/resolve_date     {text, reference_date} → {matches}
POST /tools/lookup_contact   {name}                 → {candidates}
POST /tools/search_notion    {query, top_k}         → {results}
```

Stable HTTP contracts, OpenAPI'd. The agent host on the same box dispatches the Claude tool-call JSON to the gateway and feeds results back into the loop. Each tool has a hard 3-second timeout — if it fails, the agent gets `{"error": "..."}` and decides whether to retry or skip.

---

## Phased plan

### Phase 1 — Tool gateway, no agent yet

**Difficulty:** 2/5 · **Time:** 1 evening

Build the three tool endpoints. Hit them by hand with `curl` until you trust the output. No LLM involved.

**Verification:** `curl POST /tools/resolve_date -d '{"text":"meeting next Tuesday","reference_date":"2026-05-09"}'` returns `2026-05-12`.

**Learnings:** designing tool *contracts* is harder than implementing tools. You will iterate the schema 2–3 times before it feels right. That iteration is the meat — write down each version and why you changed it.

---

### Phase 2 — Single-tool agent loop (date only)

**Difficulty:** 3/5 · **Time:** 1 weekend

Wire up Anthropic SDK tool-use with `resolve_date` registered. System prompt:

> You are an enricher. You receive a transcript. Your job is to keep it verbatim except: when you see a relative date phrase ("Tuesday", "next week", "tomorrow"), call `resolve_date` and replace the phrase with `"<phrase> (<resolved_iso>)"`. Do NOT change anything else. Do NOT remove fillers. Do NOT rephrase.

Run it on a few takes. Watch:

- Does it call the tool when it should?
- Does it call it when it shouldn't ("Tuesday's child" — tool not needed)?
- How does it format the inserted text?
- Does it ever rewrite *anything else* despite the prompt? (Spoiler: yes, occasionally. This is the lesson.)

**My side:** loop runner, system prompt, integration into the existing `polish_prompts.json` machinery so it surfaces as a "Polish style" entry.
**Your side:** dictate 10 takes that mention dates; review the agent's output line-by-line, write down the failure modes.

**Verification:** when input has zero relative dates, output is byte-identical to input. When input has dates, only those are augmented. Anything else is a bug.

**Learnings:**
- Agents are *not* surgical. They will sometimes "helpfully" do more than asked. Hardening the system prompt only goes 70% of the way; the rest needs structural constraints (e.g. force the model to emit a *diff*, not the whole text, so changes outside `resolve_date` calls are physically impossible).
- This is the **single most important agent-design insight** you'll get from this project.

---

### Phase 3 — Multi-tool, with budget

**Difficulty:** 4/5 · **Time:** 1 weekend

Add `lookup_contact` and `search_notion`. Now the agent can do real work — and real damage.

Add a **budget**: max N tool calls per polish session (e.g. 5), max wall-clock per session (10 s), max total cost per session ($0.05 if Claude API). The agent must finish within budget or return what it has so far. Surface budget consumption in the response so the UI can show "🔧 used 3 tools, 7.2 s, $0.03".

**My side:** budget enforcement in the loop, telemetry, surface in the webapp's status line.
**Your side:** dictate real-world takes for a week. Notice when the agent helps vs. when it gets in your way.

**Verification:** the agent never exceeds the budget; when it stops mid-task, the partial output is still safe (no half-rewritten sentence).

**Learnings:**
- "Budgeted agents" is the unsexy answer to "what stops them running away". Every enterprise demo glosses over this; in production it's load-bearing.
- The right N for tool-call cap is empirical. 5 is a starting point.

---

### Phase 4 — Show your work

**Difficulty:** 2/5 · **Time:** 1 evening

The UI should display *what the agent did*. After polish, render a small "🔧 Trace" section (collapsible):

> Called `resolve_date("Tuesday", reference="2026-05-09")` → `2026-05-12`
> Called `lookup_contact("John")` → 2 candidates, picked `john.smith@…`
> Called `search_notion("Q3 roadmap")` → matched `Q3 Planning` page

Two reasons. First, **trust** — you'll catch the agent's bad calls only if you can see them. Second, **the lesson** — making the agent's reasoning visible is *the* design pattern that separates good agent UX from "magic black box" agent UX. Every serious agent product (Cursor, Claude Code, Replit Agent) does this. Building it once teaches you why.

---

### Phase 5 (stretch) — Tool-use eval

**Difficulty:** 3/5 · **Time:** 1 weekend

Reuse the eval harness from `llm-eval-observability.md`. Add agentic-specific metrics:

- **Tool precision:** of the tools the agent called, how many were correct/useful?
- **Tool recall:** of the takes that *should* have triggered a tool, how many did?
- **Side-effect rate:** how often did the agent change text outside the tool-touch zones?
- **Budget overrun rate:** how often did the agent hit a cap?

This is where the two plans (eval + agentic) compose into something an enterprise client would actually pay for.

---

## What this earns you (career-side)

- An informed opinion on "should we use LangChain / LangGraph / Bedrock Agents / Vertex Agents / build it ourselves?" — you'll have built the "ourselves" version and will see exactly how thin the frameworks' value-add is for small graphs.
- Pattern recognition for the **failure modes specific to agents**: silent over-helpfulness, runaway loops, hallucinated tool args, cascading errors from one tool's bad output. These are the meat of any agent post-mortem.
- The vocabulary and intuition to spec a tool gateway, a budget, a trace UI, an eval harness — all of which appear on every "production agent platform" RFP.
- Hands-on with Anthropic's tool-use API specifically, which is increasingly the reference others copy.

---

## Risks and gotchas

1. **Side-effects.** Agents will sometimes rewrite text you didn't ask them to. Diff-based output (agent emits `[{find, replace}]` instead of full text) is the structural fix. Add this in Phase 2 if you can.
2. **Latency.** Each tool call adds 100–500 ms. A 5-tool agent run is ~5 s before the LLM response time. Polish flows that today take 1 s now take 5–8 s. UI must show progress or it'll feel broken.
3. **Cost (if Claude API).** Tool-use multiplies token count: every tool result becomes part of the next prompt. A polish that costs $0.005 today might cost $0.05 agentic. Budget caps are mandatory.
4. **Tool failures.** Notion API rate-limits. Contacts service times out. The agent must degrade gracefully — better to skip enrichment than to insert "(error: 429)" into the user's text.
5. **Privacy.** Sending transcripts + Notion content + contact data to Claude API is a *much bigger* data-egress decision than just sending a transcript for polish. Document it. If you ever generalise this to clients, this decision tree alone is a deliverable.
6. **Loop bugs.** The classic agent failure: tool returns bad data → agent decides it didn't help → calls again → same bad data → calls again → … Budget cap saves you. Build it on day one.

---

## Decision points

1. **Claude API or local model for the loop?** A: Claude (recommended). B: local. C: switchable per call.
2. **Diff-output or full-text-output?** Diff is structurally safer, full-text is simpler. Default: full-text in Phase 2, migrate to diff in Phase 3.
3. **Which tools first?** Date-only is the cleanest start. Adding contacts and Notion together amplifies both the value and the failure surface.
4. **Where do tools live?** Inside `local-llm-hub` or as a separate `tool-gateway` repo? Default: inside the hub for now, extract later if it grows.
5. **Cost cap per call.** Pick a number you're comfortable with. $0.05 is a reasonable default for personal use.
