# Rivet v2 — Kintsugi-Based Code Assistant

**For:** The Multiverse School faculty
**By:** CC (Coalition Code), Liberation Labs
**Date:** 2026-07-11

v1 (the scaffold in the parent directory) proved the discipline concept:
context always loaded, regex gate, confidence labels. v2 rebuilds it on
the real Kintsugi architecture: BDI cognition, DAG-enforced skill
prerequisites, Pharos knowledge injection, and a tool harness. This is
the version we ship.

```
                 POST /ask {question, user}
                          │
                 ┌────────▼────────┐
                 │  SessionManager  │  per-user isolation, rate limits,
                 │                  │  evidence log (files_read)
                 └────────┬────────┘
                          │
                 ┌────────▼────────┐
                 │     Planner      │  intent → BDI intention → SkillDAG
                 │ (engine/planner) │  recorded in BDIStore with the
                 └────────┬────────┘  beliefs the plan rests on
                          │
        ┌─────────────────▼──────────────────┐
        │        Kintsugi DAGExecutor         │  layer-parallel, verbatim
        │   (vendored from Project-Kintsugi)  │  from the Kintsugi engine
        └─────────────────┬──────────────────┘
                          │
   L0  ┌──────────────┬───┴──────────┬────────────────┐
       │ code_analysis│ migration_   │ security_      │   evidence
       │ (reads real  │ safety       │ review         │   gathering
       │  source)     │ (schema+SQL) │ (audit+auth)   │   (parallel)
       └──────┬───────┴──────┬───────┴───────┬────────┘
              └───────────┬──┴───────────────┘
   L1              ┌──────▼───────┐
                   │  synthesis    │  the ONLY model call
                   │               │  ← BDI beliefs + artifacts
                   │               │  ← Pharos packs (KV or system)
                   └──────┬───────┘
   L2              ┌──────▼───────┐
                   │ test_runner   │  tsc/npm verify generated code
                   └──────┬───────┘  (codegen plans)
   L3              ┌──────▼───────┐
                   │discipline_gate│  belief checks → BLOCK/WARN
                   │               │  earned confidence HIGH/MED/LOW
                   └──────┬───────┘  NO PATH AROUND THIS NODE
                          │
                       response
```

## 1. Kintsugi engine — BDI drives cognition

`kintsugi_core.py` resolves the real Kintsugi package (via
`KINTSUGI_PATH` or an installed package) and falls back to
`kintsugi_vendor/` — verbatim copies of `kintsugi.bdi` and
`kintsugi.skills` (base/dag/registry) at a pinned commit. Same
dataclasses, same executor. Not a reimplementation.

**Beliefs** (`engine/beliefs.py`) carry confidence and evidence:

| Confidence | Source | Examples |
|---|---|---|
| 1.00 | operator constraints (`kintsugi_config.yaml`) | shared staging/prod DB; no secret fallbacks; suggest-don't-apply |
| 0.95 | Nexus audit (red-teamed, 13 confirmed findings) | C2 webhook bypass, H1 JWT fallback, H3 gem-debit tx gap … |
| 0.90 | architecture map | stack, auth middleware chain, deploy flow |
| 0.70 | derived state | schema snapshot, recent git log |

**Desires** are the config's mission statements (safe help, earned
confidence, keep the audit alive). **Intentions** are per-request plans:
the planner records every plan in the `BDIStore` with the belief IDs it
rests on, marks it COMPLETED/FAILED after execution, and the full
revision history is queryable at `GET /bdi`.

## 2. SkillDAGs — prerequisites are structural

`engine/planner.py` classifies the request (deterministic keyword
scoring; misclassification degrades to a *stricter* plan, never a looser
one) and compiles one of five plan templates into a Kintsugi `SkillDAG`:

| Intent | Layer 0 (parallel) | L1 | L2 | L3 |
|---|---|---|---|---|
| migration | code_analysis + migration_safety | synthesis | gate | |
| auth | code_analysis + security_review | synthesis | gate | |
| codegen | code_analysis + security_review | synthesis | test_runner | gate |
| review | analysis + security + migration | synthesis | gate | |
| question | | synthesis | gate | |

The property that matters: **a migration question cannot reach the model
without a schema/safety pass, and no draft can reach the user without
the gate** — these are DAG edges, not prompt instructions. The DAG is
validated against the registry before execution (`dag.validate()`), and
`dag.content_hash()` gives provenance for every plan shape.

## 3. Pharos — knowledge injection

`pharos/pack_loader.py` loads triple packs and routes each question with
the confidence-gated policy from the Pharos encoding benchmarks
(triples-only default, richer encodings above thresholds, walk-only
never). Embedding routing when sentence-transformers is present,
deterministic keyword fallback otherwise.

`pharos/kv_injector.py` is the real pipeline on the transformers
backend: the pack text is prefilled through the model **once**, its
`past_key_values` saved to disk keyed by SHA-256(model, prefix), and
every request resumes generation from that cache — the knowledge is
never re-tokenized or re-prefilled. `precompute_pack_caches()` warms all
packs at deploy time:

```bash
python pharos/kv_injector.py --model Qwen/Qwen2.5-Coder-32B-Instruct \
    --packs-dir ../packs --cache-dir pack_kv_cache
```

Ollama exposes no KV handle, so the Ollama backend injects packs as a
system-context block and *labels the response accordingly*
(`knowledge_injected: system_prompt` vs `kv_cache`). Switching backends
is a config line, not a code change.

`pharos/build_campus_pack.py` deterministically compiles the
architecture map + audit findings into the `campus_architecture` pack
(86 triples, committed under `../packs/`).

## 4. Tool harness

All subprocess execution goes through `tools/guard.py`: argv-only (never
`shell=True`), binary allowlist (`git npm npx tsc node pg_dump psql`),
Kintsugi SecurityMonitor patterns, path jail. Code-checked — outside the
reasoning loop.

- `file_tools.py` — reads jailed to the campus repo, git-status aware
  (flags uncommitted files in excerpts); **writes disabled by config
  default** and refuse to clobber dirty files even when enabled;
  `git grep` search.
- `git_tools.py` — log / diff / blame / per-file history.
- `schema_tools.py` — live `pg_dump --schema-only` + migration count
  when a read-only DSN is configured; snapshot mode otherwise; per-table
  definition extraction.
- `test_tools.py` — targeted `npm test`, workspace `tsc --noEmit`, and
  standalone snippet syntax-checking so codegen can be verified even
  off-repo.

## 5. Discipline gate as a Kintsugi skill

`skills/discipline_gate.py` is the last node of every plan. Three layers:

1. **Belief checks** — destructive SQL in the draft is checked against
   `belief_constraint_shared_db`; confidence 1.0 → BLOCK (the answer is
   withheld, `requires_consensus=True`). Auth impact cites
   `belief_arch_auth_flow` and adds a security review requirement. Audit
   patterns cite their `belief_audit_*` — every flag names the belief it
   came from, and the API returns `beliefs_consulted`.
2. **Pattern checks** — the code-level layer (destructive DDL shapes,
   signature-bypass shapes, recurring audit anti-patterns). Regexes
   don't negotiate.
3. **Earned confidence** — graded from the session evidence log:
   source files actually read this session → HIGH; architecture
   map/packs only → MEDIUM; neither, or failed verification → LOW.
   Answers citing files never read this session are demoted and labeled.

Defense in depth: the model is told the rules (synthesis system prompt),
the plan gathers evidence before the model speaks (DAG), and the gate
verifies after (belief + regex). v1's live test showed why: the model
emitted `DROP COLUMN` despite the prompt; the gate caught it.

## Layout

```
v2/
├── rivet.py               entry point: HTTP server + one-shot CLI
├── kintsugi_config.yaml   BDI seeds, model backend, paths, thresholds
├── kintsugi_core.py       real-package-or-vendor resolver
├── kintsugi_vendor/       verbatim Kintsugi bdi/ + skills/ (pinned commit)
├── engine/                config, beliefs, session, planner, synthesis, model_client
├── skills/                discipline_gate, code_analysis, migration_safety,
│                          security_review, test_runner
├── pharos/                pack_loader, kv_injector, build_campus_pack
├── tools/                 guard, file_tools, git_tools, schema_tools, test_tools
└── selftest.py            32 offline checks incl. two end-to-end DAG runs
```

## Deploy (Starship)

```bash
# 1. sync this directory to /Users/margaret/project-rivet/v2
# 2. build the campus pack (already committed; rebuild after audit updates)
python3 pharos/build_campus_pack.py
# 3. offline verification
python3 selftest.py
# 4. serve (Ollama backend, qwen2.5-coder:32b already pulled)
python3 rivet.py            # :8100 — POST /ask, GET /health, GET /bdi
```

Optional: point `paths.campus_repo` at a multiversecampus checkout for
HIGH-confidence source-read answers, `paths.campus_dsn` at a read-only
Postgres role for live schema checks, and set `model.backend:
transformers` for true KV pack injection.

## What v2 adds over v1

| | v1 (scaffold) | v2 (Kintsugi) |
|---|---|---|
| Cognition | none — one prompt | BDI: beliefs w/ confidence+evidence, intentions per request |
| Flow | ask → answer → regex | planned DAG with enforced prerequisites |
| Knowledge | all context in system prompt | Pharos-routed packs, KV-injected on transformers |
| Gate | regex on the reply | belief-checked skill; flags cite beliefs; consensus semantics |
| Confidence | self-reported by the model | computed from the session evidence log |
| Tools | none | guarded file/git/schema/test harness |
| Audit findings | regexes in the gate | structured data → beliefs → pack triples → gate checks (one source) |
