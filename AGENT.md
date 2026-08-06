# AGENT.md -- Instructions for AI clients using Rivet's MCP tools

## What Rivet Is

Rivet is a discipline-gated code assistant. Every question you route through it passes through a full pipeline -- intent classification, evidence gathering via skill chips, model synthesis, and a discipline gate that sits last. There is no path from the model's draft to the user that bypasses the gate. The gate checks for destructive SQL, authentication flow changes, insecure code patterns, and audit finding matches. If the gate blocks an answer, the answer is withheld entirely. You receive structured metadata (confidence, flags, review requirements) with every response.

## Tools

### rivet_ask

The primary tool. Send a code question; Rivet classifies intent (migration, auth, codegen, review, or general question), builds a skill DAG, gathers evidence (file reads, schema checks, security scans), synthesizes an answer, and runs it through the discipline gate.

Use for: any code question, architecture guidance, debugging, implementation advice, refactoring suggestions.

Parameters:
- `question` (required): the code question or request
- `user` (optional): user identifier, defaults to "mcp-user"

Returns: `response`, `gate_passed`, `confidence`, `intent`, `flags`, `requires_review`, `packs_used`, `elapsed`

### rivet_review

Review a code change, migration, or SQL statement for safety. This routes through the same pipeline as `rivet_ask` but frames the input as a safety review. The gate verdict (pass/block) and specific flags are the primary output.

Use for: reviewing diffs before merge, checking migration SQL, validating security-sensitive changes, pre-commit safety checks.

Parameters:
- `code` (required): the code, SQL, or migration to review
- `context` (optional): what the change is for

Returns: `response`, `gate_passed`, `confidence`, `flags`, `requires_review`

### rivet_health

Returns agent status: model backend, loaded knowledge packs, registered skill chips, belief count, and whether a repo is attached.

Use for: confirming Rivet is running before first use, checking which packs are loaded, verifying repo attachment status.

Parameters: none

### rivet_bdi

Returns the BDI (Belief-Desire-Intention) snapshot: active beliefs (hard constraints and learned knowledge about the codebase), desires (mission goals), and current intentions.

Use for: understanding what constraints Rivet is operating under, inspecting which beliefs informed a previous answer, checking active intentions.

Parameters: none

## The Discipline Gate

The gate is the last node of every plan. Nothing reaches the user without passing it. It runs three layers in order:

1. **Belief checks.** The gate holds beliefs from the BDI store about shared-database constraints, authentication architecture, and audit findings. The draft is checked against each. A belief with confidence 1.0 (operator constraint) produces a BLOCK on violation. Lower-confidence beliefs produce WARNs citing their source.

2. **Pattern checks.** Code-level regex detection for destructive SQL (DROP, TRUNCATE, DELETE without WHERE), insecure code patterns (SQL injection, eval with user input, shell=True, weak crypto, unsafe deserialization), and signature bypass shapes. These are structural checks -- they cannot be argued with.

3. **Confidence grading.** The gate grades how much of the answer is actually evidenced.

## Confidence Grading

- **HIGH**: Source code was read this session. If the answer contains generated code, that code was verified (tests/type checks ran and passed).
- **MEDIUM**: Answer is backed by architecture knowledge packs or BDI beliefs, but source files were not read, or generated code was not verified.
- **LOW**: Neither source files nor knowledge packs contributed. The answer is reasoning from general knowledge. Also assigned when verification ran and failed.

Additional downgrade rules:
- Citing files that were not actually read this session downgrades HIGH to MEDIUM.
- Generated code that was not verified caps at MEDIUM.
- Failed verification forces LOW.

## Interpreting Results

### gate_passed

- `true`: The answer passed all gate checks. It is safe to present to the user. Check `flags` for any WARN or INFO annotations that provide additional context.
- `false`: The answer was blocked. The gate withheld it because it violated a hard constraint. Explain to the user WHY it was blocked (the `flags` array contains the specific violations). Offer the safe alternative -- for example, if a destructive migration was blocked, suggest an additive multi-step migration instead.

Never attempt to circumvent a gate block. Do not rephrase the blocked answer to avoid detection. Do not ask Rivet the same question differently to get around the block. The block exists because the answer would cause harm.

### flags

An array of objects, each with:
- `severity`: BLOCK, WARN, or INFO
- `message`: what was detected and why it matters
- `belief`: the BDI belief ID that backs this flag (empty string if pattern-only)

BLOCK flags mean the answer was withheld. WARN flags mean proceed with caution -- the answer is released but something needs attention. INFO flags are contextual notes (e.g., "code not verified").

### requires_review

A list of review domains (e.g., "security") that should sign off before the change is merged. When this is non-empty, tell the user which reviews are required.

### confidence

HIGH, MEDIUM, or LOW as described above. Always surface this to the user. Do not present a LOW-confidence answer as authoritative.

## Constraints

### Suggest, do not apply

Rivet suggests changes. Humans apply them. The config sets `repo_write_enabled: false` by default. Rivet will not write files, run migrations, or execute commands against the repo. Present suggestions as code blocks the user can review and apply themselves.

### Intent classification degrades strictly

A misclassified intent produces a stricter plan, never a looser one. Migration questions always get schema and safety checks. Auth questions always get security review. The gate runs its full check suite regardless of classification.

### Destructive SQL is always blocked

DROP TABLE, TRUNCATE, DELETE without WHERE, and similar destructive operations are blocked structurally -- both in code blocks and in prose. Even mentioning destructive SQL in an explanation gets blocked because it gets copy-pasted. When blocked, suggest the safe alternative (additive migration, soft delete, archive pattern).

## Recommended Workflow

1. Call `rivet_health` once at the start to confirm the agent is running and see which packs and skills are loaded.
2. Use `rivet_ask` for questions and implementation guidance.
3. Use `rivet_review` before presenting code changes that touch migrations, auth, or security-sensitive areas.
4. Check `gate_passed` and `confidence` on every response. Surface flags and review requirements to the user.
5. If the gate blocks an answer, explain the block and ask for the safe alternative.
