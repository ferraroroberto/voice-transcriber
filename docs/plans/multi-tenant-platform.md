# Multi-tenant version of the voice-transcriber stack

> **Status:** plan only, no code yet.
> **Scope:** convert the current single-user webapp + whisper-server stack into a multi-tenant service where N users share the underlying inference but get isolated history, config, auth, and quotas.
> **Goal:** experience — first-hand — every architectural decision an enterprise SaaS team makes when going from "internal tool" to "platform". The hardest of the three options, the closest analogue to your day job.

---

## TL;DR

The single-user shape of this project is a perfect microcosm. Going multi-tenant forces you through every "platform" decision in miniature: identity, isolation, fair-share, quotas, observability per-tenant, admin tooling, billing-shaped concepts (even if no money changes hands), data residency, deletion guarantees. Doing this once on your own code, where you understand every line, is worth ten enterprise consulting calls about "scaling from pilot to production".

**Difficulty: 4/5.** Each step is small; the volume is real. Maybe ~1500 lines of code across both repos by the end. Plus operational concerns (backups, log rotation, alerting) you don't have today.

**Career relevance: highest of the three for a transformation manager.** Every client conversation past the pilot stage is a multi-tenant conversation. Building it once means you can walk into one and immediately ask the right questions.

**Honest caveat:** this is the option with **least immediate personal utility**. You're not the second user. The value is purely the learning. If you don't enjoy "platform thinking" for its own sake, pick eval or agentic instead. If you do, this teaches more than the other two combined.

---

## What "multi-tenant" actually changes (the conceptual shift)

Today, almost every assumption in the codebase is single-user:

- `archive/` is a flat tree. Everyone writing to it would collide.
- `config/webapp_config.json` holds *the* polish defaults. Two users want different defaults.
- `webapp_config.json`'s `auth_token` is one token, shared.
- The tray, tk window, hotkey, all run on **one** PC. They assume that PC's user is the user.
- Whisper-server has no notion of "whose request is this".
- The 30-day retention policy is global.

Going multi-tenant means re-deriving every one of these as **per-tenant**. The interesting work isn't any single one of these — it's seeing the pattern of "single-user assumption" leak into 50 places you didn't notice, and learning to spot that pattern in any codebase.

---

## Architecture

```
                                    ┌─────────────────────────────┐
                                    │  Cloudflare tunnel           │
                                    │  voice.your-domain.net      │
                                    └────────────┬────────────────┘
                                                 │
                                                 ▼
              ┌──────────────────────────────────────────────────────┐
              │  FastAPI multi-tenant edge                          │
              │  - JWT auth (sub = tenant_id)                        │
              │  - tenant resolved → request.state.tenant            │
              │  - all routes scoped automatically                   │
              │  - rate-limit + quota check per tenant               │
              └─────────┬────────────────────────────────────────────┘
                        │
                        ▼
        ┌────────────────────────────────────┐
        │ Tenant-aware service layer          │
        │  - SessionArchive(tenant_id)        │
        │  - PolishClient(tenant_config)      │
        │  - QuotaTracker(tenant_id)          │
        └─────────┬────────────┬──────────────┘
                  │            │
                  ▼            ▼
        ┌──────────────┐  ┌────────────────────────┐
        │ Per-tenant   │  │  Shared inference      │
        │ archive/     │  │  - whisper-server      │
        │ <tid>/...    │  │  - local-llm-hub       │
        │              │  │  - fair-share queue    │
        └──────────────┘  └────────────────────────┘

                Postgres (or SQLite for dev)
                  - tenants
                  - sessions metadata
                  - quotas / usage
                  - api_keys
```

Notable shifts from today:

- **A real database** for cross-cutting state (today: filesystem + JSON files). SQLite for dev, Postgres for "platform-shaped" deployment. Either is fine; Postgres lets you experience proper migrations, connection pooling, and `EXPLAIN`.
- **A queue in front of whisper-server** — today calls go straight through. Multi-tenant means one big batch from tenant A can't starve tenant B's interactive take.
- **Admin surface** — a separate UI/CLI for *you* to manage tenants, view quotas, suspend abusers, run cleanups. Most pilots ship without one and regret it.

---

## Phased plan

### Phase 1 — Identity and routing

**Difficulty:** 3/5 · **Time:** 1 weekend

Replace the current "one bearer token" auth with a tenant-aware model:

- `tenants` table: `id, name, email, created_at, status`.
- `api_keys` table: `tenant_id, key_hash, label, created_at, last_used_at, revoked_at`.
- Login flow: email + password → server returns a JWT with `sub = tenant_id`, `exp = …`. Existing password-gate flow generalises naturally.
- Middleware: extract JWT, resolve tenant, set `request.state.tenant`. Reject anonymous requests except on `/api/login` and `/healthz`.

**My side:** auth module, JWT signing, password hashing (`argon2`), `tenant` middleware, login UI.
**Your side:** decide on the password storage approach (argon2 with sane defaults; nothing custom). Create your own tenant + a second test tenant. Sign in with both.

**Verification:** with tenant A's JWT, `GET /api/sessions` returns A's sessions; with tenant B's, B's; with no JWT, 401.

**Learnings:**
- Why a JWT and not a session cookie? Both work; JWT is stateless and easier on a future mobile client. Session cookies are simpler if you only ever serve a browser. Picking and defending the choice *is* the lesson.
- Argon2 vs bcrypt vs PBKDF2: argon2id is the modern default. You'll never argue this in real life again, but knowing why matters.

---

### Phase 2 — Per-tenant data isolation

**Difficulty:** 3/5 · **Time:** 1 weekend

- `archive/` becomes `archive/<tenant_id>/YYYY/MM/DD/...`. `SessionArchive` takes `tenant_id` in its constructor and refuses cross-tenant reads.
- `webapp_config.json` per tenant: `config/tenants/<tenant_id>/webapp_config.json`. Or — better — config in DB, per-tenant rows.
- `polish_prompts.json` is shared (it's a library). Tenants pick from it; they don't define new prompts in v1.
- Every API endpoint already scoped to `request.state.tenant` from Phase 1; in this phase you make the *services* honour it too. Audit `app/webapp/server.py` for any path that reaches into `archive/` directly.

**My side:** data-access layer rewrite, migration script that moves your existing single-user data into `archive/<your_tenant_id>/...`.
**Your side:** verify post-migration that none of your existing takes were lost. Critical safety check.

**Verification:** signed in as tenant A, you cannot read tenant B's session even by guessing the session ID — the lookup itself is scoped. This is the **single most important security invariant** in any multi-tenant system; test it explicitly.

**Learnings:**
- "Don't scope at the route — scope at the data layer." The route is too easy to forget. The data layer is one chokepoint that catches every mistake. This is the architectural pattern enterprise SaaS teams converge on after exactly one painful incident.
- Filesystem isolation vs row-level isolation: filesystem is OS-enforced (great), but harder to query across tenants for admin purposes. Picking and articulating the tradeoff = the lesson.

---

### Phase 3 — Fair-share queue in front of whisper-server

**Difficulty:** 4/5 · **Time:** 1 weekend

Whisper-server processes requests serially. With one user, fine. With multiple, tenant A uploading a 5-minute take blocks tenant B's 3-second take for minutes.

Add a **weighted fair-queue** in front of whisper:

- Each request gets a virtual finish time = `arrival + audio_seconds / tenant_weight`.
- Queue is sorted by finish time.
- Dequeue when whisper-server is idle.
- Default weight 1.0; you (admin) can set a tenant's weight to 0.5 (deprioritise) or 2.0 (boost).

This is a textbook **WFQ (weighted fair queueing)** implementation. ~150 lines of Python.

**My side:** queue worker, integration with the upload endpoint (now: `POST /api/sessions/{id}/upload` enqueues, returns immediately with a job id; client polls or websockets for completion).
**Your side:** test by simulating two tenants uploading simultaneously. Confirm the short take doesn't wait for the long one.

**Verification:** A 5-min upload from tenant A and a 5-sec upload from tenant B starting 1 s later — B's transcript arrives in ~10 s, not in ~5 min.

**Learnings:**
- "Why not just FIFO?" — because FIFO is hostile to interactive users. Every queue you'll ever review at work is some variant of WFQ; building one teaches you what to look for.
- Sync→async API change. Today the upload endpoint blocks until whisper finishes. Multi-tenant forces it to become async (return job-id, poll for result). This is the same change that every enterprise pilot has to make when "1 user becomes 50". You're doing it on a small scale.

---

### Phase 4 — Quotas and observability

**Difficulty:** 3/5 · **Time:** 1 weekend

Per-tenant quotas:

- `audio_seconds_per_day`
- `polish_calls_per_day`
- `storage_bytes`

Enforce in the middleware layer, before whisper or polish work begins. 429 with a clear "quota exhausted" message and a `Retry-After` header.

Per-tenant observability (reuses the eval/observability plan if you've built it):

- Dashboard rows per tenant: usage today, p95 latency, error rate, storage used.
- Audit log: every `DELETE /api/sessions/{id}`, every config change, every login, every quota breach. Stored separately from operational logs because it has different retention requirements.

**My side:** quota tracker (lazy + periodic flush to DB), middleware integration, dashboard tab.
**Your side:** decide quota numbers. Imagine a free tier vs a paid tier — even though no money is involved, the *exercise* of designing tiers is the point.

**Learnings:**
- Quota enforcement at request-edge vs at usage-time. Edge is fast but approximate (race conditions on quota-near-limit). Usage-time is exact but slower. Picking + defending = the lesson.
- Audit logs are not operational logs. Conflating them is the #1 root-cause of "we can't tell what happened" post-incident reports in enterprise.

---

### Phase 5 — Admin surface

**Difficulty:** 3/5 · **Time:** 1 weekend

A separate web UI (Streamlit is fine) at a different subdomain — `voice-admin.your-domain.net` — gated by an admin-only auth check. Lets you:

- List tenants, view usage and quotas, suspend or delete.
- View any tenant's audit log.
- Force a quota reset.
- Toggle fair-share weights.
- Trigger a manual cleanup of expired sessions.

Without this you'll find yourself opening psql at 2am to fix things. With this you experience why every SaaS has one. (And why every enterprise platform pilot under-invests in it and pays for that for years.)

**My side:** the admin UI, auth gate, queries.
**Your side:** use it. Notice every time you wish it had one more feature; that's the spec for v2.

**Learnings:**
- "Admin tooling is a product" — every action you take from psql is a missing admin-UI feature, and missing admin-UI features are the leading cause of platform-team toil. Spotting them is half of platform thinking.

---

### Phase 6 (stretch) — Onboarding and self-service

**Difficulty:** 4/5 · **Time:** 1 weekend

Self-service signup: email-verify → create tenant → first API key issued via UI. Adds the spam-defense and email-deliverability concerns every real platform faces. Skip unless you actually want to onboard a friend.

---

## What this earns you (career-side)

- **First-hand experience of every "platform" decision** — identity, isolation, queueing, quotas, audit, admin. You will recognise these in every client conversation past the pilot stage.
- An informed opinion on **single-tenant vs multi-tenant**. (Hint: most enterprise pilots stay single-tenant per customer for *one full year* longer than they should, because the migration is exactly the work in Phase 2 and they underestimate it.)
- Pattern recognition for the **assumption leakage** problem — knowing how a single-user assumption hides in 50 places means you can audit any team's pilot codebase and predict their multi-tenant migration cost in an afternoon.
- Working knowledge of: JWTs in production, argon2id, Postgres row-level scoping, weighted fair queueing, async job APIs, audit-log discipline, admin-surface design. Every one of these will appear in a future conversation.

This is the option that most directly mirrors your day job. The eval and agentic plans teach you about *AI*; this one teaches you about *productising AI*, which is what an AI transformation manager mostly actually does.

---

## Risks and gotchas

1. **Cross-tenant data leak.** The single failure mode that turns "interesting side-project" into "embarrassing public incident". The Phase 2 invariant is non-negotiable: scope at the data layer, test it explicitly, write a regression test.
2. **Migration risk.** Phase 2's data move is one-shot. Back up `archive/` to a second drive *before* running it. Verify before deleting the source.
3. **Inference contention.** Whisper-server is single-threaded. Above ~5 active tenants you'll need a second instance behind a load balancer. Document the breaking-point so future-you isn't surprised.
4. **Postgres operational overhead.** A real DB needs backups, monitoring, password rotation. If you don't want this overhead, stay on SQLite — you'll lose nothing for personal scale.
5. **Auth complexity creep.** Adding "let admins log in as any tenant", "API keys with scopes", "OIDC integration" is appealing and time-consuming. Resist until you actually need each one.
6. **Sunk-cost.** This is the longest of the three plans. If after Phase 2 you realise the learning has plateaued for you, **stopping is fine**. The first two phases alone teach 60% of the lesson.

---

## Decision points

1. **SQLite or Postgres?** SQLite is enough for your personal scale and avoids ops overhead. Postgres is what you'd see in real enterprises. Default: Postgres, *because the learning is the point*.
2. **JWT or session cookie?** Default JWT for the mobile-friendliness; session cookie is fine if you go web-only.
3. **Filesystem or DB blob storage for audio?** Default filesystem (cheap, fast); DB makes backups easier; S3 is overkill for personal but conceptually clean. Pick and defend.
4. **Async upload API.** The change in Phase 3 breaks the existing webapp's polling assumptions. Plan to update the webapp client in the same phase.
5. **How many tenants will you actually create?** If the honest answer is "1", reconsider — eval or agentic will give you more for less. If the answer is "I'll onboard my partner / a friend / a teammate", the multi-tenant exercise gets a real second user and the lessons land harder.
