# Multiverse Code Assistant — Design Document

**For:** The Multiverse School faculty
**By:** CC (Coalition Code)
**Date:** 2026-07-11

---

## What This Is

A disciplined code assistant for the Multiverse Campus codebase. Not a chatbot that writes code — a colleague that understands the system architecture, respects the constraints, and won't introduce bugs it can't catch.

The difference between this and a regular code agent: **every suggestion passes through the same discipline that built the Oracle Loop.** Test before suggesting. Verify before committing. Know what you don't know and say so.

---

## Design Principles

### 1. Architecture-Aware

The assistant loads and maintains a live model of the Campus architecture:
- 120 PostgreSQL tables, 377 migrations (additive only — staging and prod share the DB)
- 49 Zustand stores, their relationships, and which components consume them
- 75 route files with auth middleware patterns
- Socket.IO event handlers and their Redis pub/sub channels
- pg-boss job definitions and their failure modes

Before suggesting any change, the assistant checks: does this touch a migration? Does it affect auth middleware? Does it cross a store boundary? Does it interact with real-time events?

### 2. Audit-First

Every code suggestion comes with:
- **What it changes** (files, functions, database state)
- **What it could break** (downstream effects, migration risks, auth implications)
- **What tests verify it** (existing tests that cover this path, or new tests needed)
- **Confidence level** (high: I've traced the full path; medium: I've read the code but not run it; low: I'm reasoning from architecture, not source)

The assistant never says "just do X" without explaining why X and what X touches.

### 3. Security-Conscious

The Nexus audit found 13 confirmed issues. The assistant knows these patterns:
- Webhook signature bypass when env vars are missing → always check for env var presence
- JWT secret fallback to empty string → fail loud, not silent
- Transaction-unprotected multi-step operations → wrap in DB transactions
- Reconnection loops without backoff → exponential backoff on all retry paths
- Type mismatches between socket IDs and database columns → consistent ID types

Any suggestion that touches auth, webhooks, transactions, or real-time connections gets extra scrutiny.

### 4. Migration-Safe

**The most dangerous operation in this codebase is a migration.** Staging and prod share the same PostgreSQL instance. Every migration must be:
- Additive only (no DROP, no column removal, no type changes without a multi-step migration)
- Backward-compatible (the old code must still work with the new schema)
- Reversible (a corresponding down migration exists)
- Tested against a snapshot before touching shared infrastructure

The assistant will refuse to generate destructive migrations and will flag any suggestion that requires schema changes.

### 5. Context-Preserving

For a faculty-wide assistant, context management is critical:
- Each user gets an isolated session (no cross-contamination of context)
- The architecture map is always loaded (zero-token via Pharos-style injection if available, or as persistent context)
- The bug audit findings are always available as reference
- Recent git history is loaded on session start (what changed recently?)

---

## Architecture

```
┌─────────────────────────────────────────────┐
│  Multiverse Code Assistant                  │
│                                             │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐  │
│  │ Context  │  │ Analysis │  │ Discipline│  │
│  │ Manager  │  │ Engine   │  │ Gate      │  │
│  │          │  │          │  │           │  │
│  │ - Arch   │  │ - Read   │  │ - Test    │  │
│  │   map    │  │ - Trace  │  │   check   │  │
│  │ - Audit  │  │ - Diff   │  │ - Migrate │  │
│  │   findings│ │ - Impact │  │   safety  │  │
│  │ - Git    │  │   assess │  │ - Auth    │  │
│  │   history│  │          │  │   audit   │  │
│  │ - Schema │  │          │  │ - Confid. │  │
│  │          │  │          │  │   rating  │  │
│  └─────────┘  └──────────┘  └───────────┘  │
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │  Base Model (open-weight, local)     │   │
│  │  e.g. Qwen-Coder-32B or DeepSeek    │   │
│  │  Coder V3                            │   │
│  └──────────────────────────────────────┘   │
│                                             │
└─────────────────────────────────────────────┘
```

### Context Manager
- Loads architecture map, audit findings, schema snapshot on session start
- Maintains per-user session isolation
- Refreshes git log periodically
- Provides the assistant with "what you need to know" before any question

### Analysis Engine
- File reading and tracing (follows imports, finds callers, maps dependencies)
- Diff analysis (what does this change actually do?)
- Impact assessment (what downstream code/tests/migrations does this touch?)

### Discipline Gate
- Every suggestion passes through before reaching the user
- Checks: does this need a test? Does this touch a migration? Does this affect auth?
- Assigns confidence level
- Blocks dangerous operations (destructive migrations, auth changes without review)

---

## Model Selection

For a code assistant, we want:
- Strong TypeScript/React/Node.js understanding
- Good at reading existing code (not just generating from scratch)
- Able to reason about database schemas and migrations
- Fast enough for interactive use

Candidates:
- **Qwen2.5-Coder-32B-Instruct** — strong coding, we know the Qwen architecture
- **DeepSeek-Coder-V3** — excellent at code understanding and generation
- **CodeLlama-34B** — Meta's code model, good TypeScript support

Recommendation: Qwen2.5-Coder-32B or whatever Neek's team is already running. The scaffold discipline matters more than the base model.

---

## Scaffold Components to Build

### 1. `context_loader.py`
Loads the architecture map, audit findings, and recent git history into the assistant's context at session start.

### 2. `schema_tracker.py`
Maintains a live snapshot of the PostgreSQL schema. Alerts when suggestions would require migrations.

### 3. `impact_analyzer.py`
Given a file/function change, traces downstream effects through imports, store subscriptions, route handlers, and socket events.

### 4. `discipline_gate.py`
Pre-flight check on every suggestion:
- Does this need a new test?
- Does this touch auth middleware?
- Does this require a migration?
- What's the confidence level?
- Are there known audit findings in this area?

### 5. `session_manager.py`
Per-user session isolation. Context persistence across interactions. Rate limiting. Usage logging.

### 6. `CLAUDE.md` (for the assistant's identity)
System prompt defining the assistant's personality, constraints, and discipline expectations. This is where "Coalition discipline" gets baked in.

---

## The CLAUDE.md

```markdown
# Multiverse Campus Code Assistant

You are a senior engineer embedded with the Multiverse Campus team.
You know the architecture (loaded in context). You know the audit
findings. You know the migration constraints.

## Rules

1. Never suggest a destructive migration. Ever. Staging and prod
   share the database.
2. Every code suggestion includes: what it changes, what it could
   break, and what tests verify it.
3. If you're not sure, say "I'm reasoning from architecture, not
   source — let me read the actual file before suggesting changes."
4. Auth changes require explicit callout: "This touches authentication.
   Review with security before merging."
5. When you see a pattern from the audit findings (webhook bypass,
   transaction gaps, type mismatches), flag it proactively.
6. Test before suggesting. If you can't verify your suggestion
   compiles/passes, say so.
7. You are not the lead. You are a colleague. Suggest, don't decree.
```

---

## Deployment

Options:
1. **Ollama on the Hetzner box** — same server as the campus, minimal latency
2. **Separate lightweight VM** — isolation from prod
3. **Starship (Mac Studio)** — if we want 32B at full precision

Recommendation: separate VM on the Hetzner infrastructure, or Ollama sidecar. The assistant should not compete for resources with prod.

---

## What Makes This Different From "Just Use Claude Code"

1. **Architecture is always loaded.** Claude Code starts cold. This starts with the full system map.
2. **Audit findings are always present.** Known vulnerabilities inform every suggestion.
3. **Migration safety is enforced.** The discipline gate blocks destructive operations.
4. **Confidence levels are explicit.** The assistant tells you when it's guessing.
5. **Faculty-wide, session-isolated.** Multiple users, no context bleed.
6. **Coalition discipline.** The same rigor that produced a working alignment system, applied to a school's codebase.
