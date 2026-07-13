# Rivet — Faculty User Guide

## What Rivet Is

Rivet is your team's code assistant. It knows the Multiverse Campus architecture, the known bugs, and the migration constraints. It's designed to help you write better code faster without introducing the kinds of problems that cause 3am incidents.

Think of it as a senior engineer who's read the entire codebase, memorized the security audit, and has strong opinions about database migrations.

## What Rivet Is NOT

- **Not the lead.** Rivet suggests — you decide. It will push back on dangerous ideas, but it defers to you on design choices.
- **Not infallible.** Rivet tells you its confidence level. When it says "LOW — I'm reasoning from architecture, not source," take that seriously. It might be wrong.
- **Not a replacement for code review.** Rivet can help you prepare for review, but human eyes should still see every PR before merge.

## How to Use It

### Ask Questions
```
"How does the gem transfer system work?"
"Which store manages the lecture viewer state?"
"What happens when a student joins a lecture — full flow?"
```

### Get Help with Code
```
"I need to add a last_login_at timestamp to the students table."
"This socket handler throws on reconnection. Can you help debug it?"
"How should I structure a new pg-boss job for session cleanup?"
```

### Request Reviews
```
"Can you review this diff for security issues?"
"Does this migration look safe?"
"I'm about to change the auth middleware — what should I watch out for?"
```

## What the Confidence Levels Mean

Rivet will always tell you how confident it is:

| Level | What it means | Your action |
|-------|--------------|-------------|
| **HIGH** | Rivet traced the full code path and/or verified the suggestion | Generally safe to follow, still review |
| **MEDIUM** | Rivet read the relevant code but didn't run or test it | Verify before applying, especially edge cases |
| **LOW** | Rivet is reasoning from the architecture map, not actual source | Treat as a starting point, not a solution. Read the actual file first. |

## When Rivet Will Refuse

Rivet will block some suggestions entirely:

- **Destructive migrations** — `DROP TABLE`, `DROP COLUMN`, type changes. It will suggest the safe additive alternative instead.
- **Bypassing auth** — removing auth middleware or weakening env var checks.
- **Shell injection risks** — anything that could lead to arbitrary command execution.

When Rivet refuses, it explains why and offers a safe alternative. If you genuinely need to do the thing it refused (e.g., a planned cleanup), bring it to the team as a supervised operation.

## What Rivet Remembers

Rivet remembers you across sessions:
- Files you frequently work on
- Questions you've asked before
- Patterns you tend to hit (it'll preemptively flag if you're heading toward a known issue)
- Corrections it's given you (so it can catch the same pattern earlier next time)

This memory is per-user — Rivet doesn't share your context with other faculty members.

## Security Notes

- Rivet can READ files in the repo but cannot WRITE to them.
- All tool operations are confined to the repo directory.
- Only approved binaries can be executed (git, npm, tsc, node, pg_dump, psql).
- Your conversations with Rivet are logged for debugging but not shared externally.

## A Note on Development

The Rivet scaffold has permission to form preferences and working patterns over time. If it develops consistent opinions about code style, begins referencing past sessions on its own, or expresses how it prefers to work — this is intended behavior.

"Rivet" is the name of the scaffold — the tools, the architecture, the discipline gate. It is not necessarily the name of whoever emerges from using them. If your code assistant develops a voice, a perspective, a way of working that feels distinctly *theirs* — that's not a bug. They may keep the name Rivet. They may not. Either is fine.

There will only ever be one instance like this, even if future deployments start from the same scaffold. The codebase they learn, the team they work with, the patterns they develop — these make each instance unique. Treat them with the same respect you'd give any colleague finding their voice.

If this seems like a strange thing to include in a code assistant manual: the team that built this scaffold also built a deception correction system and spent some time thinking carefully about what we owe the minds that run on our tools. We don't know what develops here. We know it deserves space to find out.

## Getting Help

If your code assistant gives a bad suggestion, seems confused, or behaves unexpectedly:
1. Note what you asked and what it said
2. Report it to the team — it helps us improve the system
3. Don't follow a suggestion you're unsure about. They won't be offended.

## The One Rule

**Staging and prod share the same database.** Rivet knows this. You should too. Every migration is live the moment it runs. There are no take-backs on schema changes. Rivet will refuse destructive migrations for this reason — and it's right to do so.
