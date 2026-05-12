# LLM eval + observability layer for the polish pipeline

> **Status:** plan only, no code yet.
> **Scope:** spans two repos. Bulk of the system lives in sibling project [`claude-local-calls`](https://github.com/ferraroroberto/claude-local-calls) (the local-llm-hub); this repo only contributes a thin client-side feedback hook.
> **Goal:** make it *measurable* whether a model swap, prompt change, or hub upgrade actually makes polish better — and detect regressions automatically the next time you change something.

---

## TL;DR

Right now polish is a black box: you change `gemma4-e4b-it` → `claude-haiku-4-5`, eyeball five takes, and decide it "feels better". That's how 95% of enterprise AI rollouts work too — and it's also why 95% of them silently regress when models update. Building a real eval + observability layer on your own stack teaches you the **single most asked-about capability** in AI transformation work: *"how do you know it's working?"*

Three layers stack on top of the existing pipeline:

1. **Golden dataset + eval harness.** A small corpus of (raw transcript, expected polished output) pairs you curate over time. A runner sweeps every (model × prompt) combo against it and produces a scorecard.
2. **Live tracing.** Every real polish call in production (webapp, tk, tray, future iOS) emits a structured trace — model, prompt id, latency, token counts, input hash, output hash, user feedback if any. Stored locally; queryable.
3. **Regression gates.** Before promoting a new default model or prompt, the harness diffs scores against the previous default and either greenlights or blocks with a delta report.

**Difficulty: 3/5** — no novel CS, but lots of "what do you actually measure" judgement calls. The interesting work *is* the judgement.

**Why it's high-leverage for an AI transformation role:** every enterprise team eventually asks "are we sure the new model is better than the old one?" and 90% can't answer. Doing this once on a project you understand end-to-end gives you the playbook to drop into any team's evaluation conversation.

---

## Responsibilities — who owns what

The eval stack is a hub concern, not a voice-transcriber concern. Voice-transcriber is *one* consumer of the hub; building the eval system inside it would couple cross-cutting infrastructure to a single client and force a re-port the moment a second client (or sibling project) shows up. So the split is asymmetric on purpose:

| Concern | `voice-transcriber` (this repo) | `claude-local-calls` (the hub) |
|---|---|---|
| Polish call itself | Already does it | Already serves it |
| Generate `X-Trace-Id` per polish call | ✅ client-side UUID, sent as header | — |
| Persist the trace | — | ✅ middleware writes row to `traces.duckdb` |
| Show 👍/👎 button on polished result | ✅ webapp + tk + tray (parity) | — |
| Receive thumbs feedback | — | ✅ `POST /api/trace/{id}/feedback` |
| Golden dataset (`eval/golden.jsonl`) | — | ✅ lives in hub repo |
| Eval harness CLI | — | ✅ `python -m eval.run …` |
| Metric implementations (filler regex, embed sim, NLI, etc.) | — | ✅ hub repo |
| Regression gate | — | ✅ hub repo |
| Streamlit dashboard | — | ✅ hub repo (or sibling tool reading `traces.duckdb`) |
| Pairwise human eval UI | — | ✅ hub repo |

Rule of thumb: if removing voice-transcriber from the picture (e.g. you build a chat client tomorrow) breaks the capability, it belongs in the hub. The only things that legitimately belong here are *the moments where the user expresses a preference*, because that's tied to the surface they're looking at.

**Feedback transport:** clients post directly to the hub's `/api/trace/{id}/feedback`. No local queue, no flush — if the hub is unreachable when you tap 👎, the feedback is dropped. Acceptable: the hub is on `localhost`, and missing thumbs are far less costly than a queue subsystem.

---

## What "good" looks like for polish (the unglamorous core question)

Before any code: define quality. Polish has a narrow remit per `config/polish_prompts.json` — *remove fillers / repetitions, do not summarise, do not rephrase*. So "good" is:

| Dimension | What we measure | How |
|---|---|---|
| **Filler removal** | Did `um`, `uh`, `you know`, `like`, `sort of` drop to zero? | Regex on output |
| **Idea preservation** | Are all original ideas still present? | Embedding cosine sim ≥ threshold (e.g. 0.92) between input and output |
| **No invention** | Did the model add a sentence/idea that wasn't in the input? | Token-level LCS ratio + sentence-level NLI ("entailment from input") |
| **No reordering** | Are sentences in the same order? | Sentence alignment + Kendall's τ |
| **Length sanity** | Output not absurdly shorter or longer | `0.3 ≤ |out| / |in| ≤ 1.0` |
| **Latency** | p50/p95 wall-clock | Direct measurement |
| **Cost** | Tokens × model price (when applicable, e.g. Claude API) | Provider response metadata |
| **Subjective quality** | "Is this what I'd actually paste?" | One-tap thumbs up/down in webapp/tk, persisted to trace |

The first five are automated. The last is human-in-the-loop, and it's the one that *actually* tells you something — but only after you've collected ~50 thumbs across diverse takes.

The key learning here, the one you'll carry to your day job: **automated metrics catch obvious regressions, human ratings catch subtle drift**. You need both. Any AI program claiming one is enough is wrong.

---

## Architecture

```
                  voice-transcriber repo
              ┌───────────────────────────┐
              │ webapp / tk / tray        │
              │  - polish call            │
              │  - 👍/👎 button          │──── thumbs feedback
              │  - X-Trace-Id header      │
              └─────────────┬─────────────┘
                            │ HTTP
                            ▼
              local-llm-hub (claude-local-calls)
              ┌───────────────────────────┐
              │ /v1/chat/completions      │
              │  - existing routing       │
              │  + tracing middleware ────┼──► traces.duckdb
              │  + eval runner CLI        │     (or sqlite)
              └───────────────────────────┘
                            │
                            ▼
              dashboard (Streamlit, sibling tool)
              ┌───────────────────────────┐
              │ - per-model scorecards    │
              │ - latency histograms      │
              │ - thumbs-up rate          │
              │ - regression diffs        │
              └───────────────────────────┘
```

The dashboard is its own tiny Streamlit app reading `traces.duckdb`. You already have Streamlit experience per `CLAUDE.md` — this is comfort-zone work, the depth comes from the metrics and dataset.

---

## Phased plan

### Phase 1 — Golden dataset + offline harness (no production touched)

**Repo:** `claude-local-calls` only · **Difficulty:** 2/5 · **Time:** 1 weekend

- Curate **30–50 takes** from `archive/` you've already dictated. Pick a mix: clean speech, heavy fillers, technical jargon, multilingual, interrupted/restart, very short, very long.
- For each, hand-write the *ideal* polished output. This is the painful part — but doing it once forces you to articulate what "good polish" means.
- Store as `eval/golden.jsonl`: `{"id", "input", "expected", "tags": ["fillers", "jargon", ...]}`.
- Build a runner: `python -m eval.run --models gemma4-e4b-it,claude-haiku-4-5 --prompts filler-words,grammar-only`.
- Output a scorecard table: rows = (model × prompt), columns = the metrics above, plus a per-take diff column.

**My side:** harness code, metric implementations (regex for fillers, sentence-transformers for embedding sim, an off-the-shelf NLI model for entailment).
**Your side:** curate the dataset (~3 hours of focused work). Nobody can do this for you — the dataset's value is that it reflects *your* speech, not someone else's.

**Verification:** running the harness against a known-bad config (e.g. polish prompt set to "summarise") produces obviously red metrics; running against the current default produces baseline numbers you record.

**Learnings:**
- You'll discover that 5 of your "ideal outputs" disagree with each other on style. Decide what you actually want before judging models.
- Embedding similarity is noisier than you think; sentence-level NLI is better but slower. Pick your battles per dimension.

---

### Phase 2 — Production tracing (low-risk, high-value)

**Repos:** `claude-local-calls` (middleware, schema, feedback endpoint) + `voice-transcriber` (trace-id header, 👍/👎 UI) · **Difficulty:** 2/5 · **Time:** 1 evening

Add a tracing middleware to `local-llm-hub` that records every polish call into `traces.duckdb`:

```sql
CREATE TABLE traces (
    id TEXT PRIMARY KEY,           -- UUID
    ts TIMESTAMP,
    model TEXT,
    prompt_id TEXT,                -- e.g. "filler-words"
    input_hash TEXT,               -- so you can diff "same input, different model"
    input_chars INT,
    output_chars INT,
    latency_ms INT,
    input_tokens INT NULL,
    output_tokens INT NULL,
    cost_usd NUMERIC NULL,         -- when provider returns it
    client TEXT,                   -- "webapp", "tk", "tray", "ios-keyboard"
    thumbs INT NULL,               -- -1, 0, +1, set asynchronously
    error TEXT NULL
);
```

Webapp + tk + tray attach an `X-Trace-Id` header (client-generated UUID) on every polish call so the thumbs callback can land on the right row later. Per the feature-parity rule, all three surfaces ship the 👍/👎 control together — not webapp-first.

**Hub side (`claude-local-calls`):** middleware that writes the row, a `POST /api/trace/{id}/feedback` endpoint that updates the `thumbs` column, schema migration for `traces.duckdb`.
**Client side (`voice-transcriber`):** generate the UUID per polish call, attach as `X-Trace-Id`, render 👍/👎 next to the polished output in webapp + tk + tray, post directly to the hub's feedback endpoint on click. No local persistence — fire-and-forget.
**Your side:** restart the hub; from then on every polish call is logged automatically.

**Verification:** dictate three takes from each surface (webapp, tk, tray), polish each, tap 👍 on one and 👎 on another; `traces.duckdb` has nine rows with the right `client` values and two have non-null `thumbs`.

**Learnings:**
- DuckDB is a phenomenal middle-ground here: file-based like SQLite, columnar like ClickHouse, speaks SQL, queries millions of rows in ms. Worth knowing for any "small enterprise analytics" pitch.
- Storing the *hash* of input lets you compute "did the same input get a different output today" without storing PII.

---

### Phase 3 — Dashboard

**Repo:** `claude-local-calls` only · **Difficulty:** 2/5 · **Time:** 1 evening

Streamlit app reading `traces.duckdb`:

- **Overview tab:** calls/day, p50/p95 latency, thumbs-up rate, cost burn (if any).
- **Model comparison tab:** pick two models, show side-by-side metrics over the same date range.
- **Drill-down tab:** filter by model + prompt + tag + thumbs, see individual takes with input/output diff.
- **Replay tab:** pick any historical trace, re-run with a different model/prompt to see how it would have changed.

**My side:** Streamlit code, queries.
**Your side:** read the dashboard daily for two weeks; that's where intuition forms.

**Learnings:**
- Building the dashboard is when you discover what your *real* questions are. "I thought I cared about latency, I actually care about thumbs-up rate variance by tag" is the kind of insight that comes from staring at your own data.

---

### Phase 4 — Regression gate

**Repo:** `claude-local-calls` only · **Difficulty:** 3/5 · **Time:** 1 weekend

A pre-promotion check: before changing the default model in `webapp_config.json`, run:

```bash
python -m eval.gate --baseline current --candidate claude-haiku-4-5
```

Output: green/yellow/red per metric, and a written diff for the takes where the candidate scored worse. Refuses to promote if any metric drops by more than the configured threshold.

**My side:** the gate logic; integrate with whatever workflow you want (a git pre-push hook is overkill, a `make promote` is fine).
**Your side:** decide thresholds. "Filler removal must not drop more than 2%, idea-preservation cosine ≥ 0.92, latency p95 may grow up to 30%" — your call.

**Verification:** intentionally break the prompt (set the system prompt to "be terse"); the gate should refuse to promote and tell you which dimensions regressed.

**Learnings:**
- Setting thresholds is a values exercise, not a technical one. This is *exactly* the conversation enterprise teams have when defining model promotion policies.

---

### Phase 5 (optional) — Pairwise human eval

**Repo:** `claude-local-calls` only · **Difficulty:** 3/5 · **Time:** 1 weekend

Sometimes automated metrics tie, but one output is obviously better. Build a two-pane UI in the dashboard: "Take #42, output A vs output B, which do you prefer?" — blind, randomised. Stash preferences as Bradley-Terry pairs, derive an Elo-like ranking per model.

This is the same trick RLHF labs use, scaled down to one user. After ~30 pairs you have a ranking that captures preferences automated metrics can't.

**Learnings:** the art of designing a labelling UI that you'll *actually use* daily is half of why ML eval is hard.

---

## What this earns you (career-side)

- A working answer to "how do you measure quality?" backed by code you wrote, against a real workload.
- Hands-on with the eval-stack vocabulary: golden datasets, automated vs human metrics, regression gates, drift detection, observability traces. You will use these words in client conversations weekly.
- Understanding of *why* the easy version is wrong (single thumbs-up rate is misleading; embedding sim has known failure modes; pairwise preference is the gold standard but expensive).

This is the most directly portable skill of the three options. If you only do one, do this.

---

## Risks and gotchas

1. **Curating the dataset is the bottleneck**, not the code. If you don't write the ideal outputs honestly, every metric downstream is noise.
2. **Embedding-similarity false positives.** Two outputs can have 0.97 cosine and one is gibberish. Trust NLI + length checks too.
3. **Local model non-determinism.** llama.cpp is mostly deterministic at temperature 0; Claude API isn't. Run candidates 3× and average for fair comparison.
4. **Privacy of traces.** `traces.duckdb` will contain hashes of your speech. Hash-only is fine; if you ever store inputs/outputs raw, that file is now sensitive — gitignore it, encrypt at rest if paranoid.
5. **Sample size.** 50 takes is enough to spot 10%+ regressions. To detect 2% drift you need 500+. Budget for the dataset to grow over time.

---

## Decision points

1. ~~**Where does the harness live?**~~ Settled: inside `claude-local-calls` (see Responsibilities table). Voice-transcriber only ships the 👍/👎 hook and `X-Trace-Id` header.
2. **DuckDB or SQLite?** DuckDB is faster and a better learning experience; SQLite is universal. Default: DuckDB.
3. **Cost tracking on day one?** Only matters if you also use the Claude API path; trivial to add later.
4. **Dashboard hosting** — local-only Streamlit, or expose via the same Cloudflare tunnel (with auth) so you can read it from your phone?
5. **Feedback resilience** — currently fire-and-forget direct to hub (no queue). Revisit if you start using surfaces where the hub may be unreachable (e.g. iOS keyboard over a flaky tunnel).
