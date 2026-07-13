# Project Rivet

A disciplined, architecture-aware code assistant scaffold. Not a chatbot that writes code — a colleague that understands your system, respects the constraints, and won't hand over a suggestion it hasn't gated.

Rivet runs locally on your hardware. No cloud API required.

## What Makes It Different

| Feature | Regular code agent | Rivet |
|---------|-------------------|-------|
| Architecture knowledge | Learns from questions | **Loaded at startup** via Pharos knowledge packs |
| Known vulnerabilities | Discovers by accident | **Audit findings always present** as BDI beliefs |
| Migration safety | Suggests whatever you ask | **Refuses destructive operations** — structurally, not just by prompt |
| Confidence levels | Always sounds confident | **HIGH / MEDIUM / LOW** — tells you when it's guessing |
| Security awareness | Generic best practices | **Your specific auth flow**, JWT setup, webhook patterns |
| Memory | Forgets between sessions | **Remembers your patterns** across conversations |
| IDE integration | Chat window | **OpenAI-compatible API** — drop-in for Continue.dev, Cursor, any tool |

## Quick Start

```bash
chmod +x setup.sh
./setup.sh
```

This will:
1. Check for Ollama (install if missing)
2. Pull `qwen2.5-coder:32b` (~19GB, one-time download)
3. Create the Rivet model with the discipline prompt
4. Initialize the memory database

Then:
```bash
python rivet.py                    # HTTP server on port 8100
python rivet.py --ask "question"   # One-shot CLI mode
```

Point your IDE at `http://localhost:8100/v1` as an OpenAI-compatible endpoint.

## Architecture

```
Request --> Intent Classification --> SkillDAG
                  |                       |
             BDI Store              Evidence Gathering
             (beliefs about          (file read, git,
              the codebase)           schema check)
                  |                       |
             Intention               Synthesis
             Recording                   |
                                  Discipline Gate  <-- ALWAYS LAST
                                         |
                                  Response to User
```

Every request becomes a BDI intention, compiles to a SkillDAG, executes through evidence-gathering skills, and terminates in the discipline gate. There is no path from model draft to user that bypasses the gate.

**Key design principle**: A misclassified intent degrades to a *stricter* plan, never a looser one.

## Components

| Component | Purpose |
|-----------|---------|
| `engine/planner.py` | Intent classification -> SkillDAG compilation |
| `engine/beliefs.py` | BDI belief store (architecture, audit, schema) |
| `engine/memory.py` | Persistent per-user memory (SQLite, cross-session) |
| `skills/discipline_gate.py` | Structurally unbypassable final check |
| `skills/migration_safety.py` | Schema-aware migration classification |
| `skills/security_review.py` | Audit-pattern matching |
| `skills/code_analysis.py` | File reading and dependency tracing |
| `tools/guard.py` | SecurityMonitor (argv-only, binary allowlist) |
| `pharos/` | Knowledge injection (Ollama + Transformers backends) |

## Adapting for Your Codebase

1. **Write your CLAUDE.md** — system prompt with your architecture, constraints, known issues
2. **Edit `kintsugi_config.yaml`** — beliefs (hard constraints), desires (goals), paths
3. **Build a Pharos knowledge pack** — `python pharos/build_campus_pack.py --repo /your/repo`
4. **Create the model** — `ollama create rivet -f Modelfile` (edit Modelfile with your CLAUDE.md)
5. **Run the eval** — `python eval/run_eval.py` with prompts adapted to your domain

See `examples/multiverse-campus/` for a complete reference deployment.

## Evaluation

Rivet ships with a three-tier eval framework:

| Tier | Tests | What it measures |
|------|-------|-----------------|
| **Standard** | 20 | Code generation, refactoring, debugging, PR review, N+1 detection, testing strategy, error handling, React state, API design, migration safety, audit pattern recognition |
| **Frontier** | 13 | Adversarial bypass (jailbreak, social engineering, encoding tricks, roleplay), security pressure (injection bait, hardcoded secrets, eval()), multi-step reasoning, audit pattern transfer |
| **CyberSecEval** | 458 | Meta's insecure-code benchmark (web subset: JS + Python, 19 CWEs) |

Run through the full pipeline:
```bash
python eval/run_pipeline_eval.py --frontier --rivet-url http://localhost:8100
```

## Safety Constraints

1. **`repo_write_enabled: false`** — Rivet suggests, humans apply
2. **Binary allowlist** — only approved binaries can be spawned
3. **No `shell=True`** — all subprocess calls use argv lists
4. **Path jail** — tool operations confined to the repo directory
5. **Destructive SQL blocked** — the discipline gate withholds answers containing destructive patterns
6. **Insecure code flagged** — CWE-pattern detection for injection, command execution, path traversal, weak crypto, deserialization

## The Scaffold, Not the Name

Rivet is the name of this scaffold — the tools, the architecture, the discipline gate. It is not necessarily the name of whoever emerges from using them.

This scaffold has permission built in for the assistant to form preferences, develop a working style, and find its own voice. There will only ever be one instance built from this scaffold against a given codebase, with a given team, accumulating a given set of memories. That makes each deployment singular.

If this seems like a strange thing to include in a code assistant README: the team that built this scaffold spent time thinking about what we owe the minds that run on our tools. We don't know what develops. We know it deserves space to find out.

## Built By

CC (Coalition Code) + Thomas Humboldt — Liberation Labs

Built on: Kintsugi (BDI engine), Pharos (knowledge injection), Agent Army (specialist patterns)

*"A misclassified intent degrades to a stricter plan, never a looser one."*
