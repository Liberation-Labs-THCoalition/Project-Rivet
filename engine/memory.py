"""Rivet Memory — persistent, per-user memory over SQLite.

Same shape as Ember's semantic memory (flat records + relevance recall)
but scoped for a code assistant and dependency-free: instead of an
embedding model, recall scores stored memories against the query with
TF-IDF-style keyword overlap, weighted by recency and how often a memory
has recurred. Good enough for "what has this user hit before" — not a
vector store.

Rivet decides what to remember (calls remember_*). Memory decides what
to surface (recall / session_brief ranks and returns it). Memory never
inspects code or talks to the model — it only stores and ranks text
Rivet hands it.

One SQLite file per deployment. Each user gets their own table
(memories_<user_id>) so one user's history never leaks into another's
recall results.
"""

import argparse
import hashlib
import math
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = Path(
    os.environ.get("RIVET_MEMORY_DB")
    or (Path(__file__).resolve().parent.parent / "data" / "rivet_memory.db")
)

KIND_FILE = "file"                # a file the user works on
KIND_QA = "qa"                    # a question asked + the answer given
KIND_PATTERN = "pattern"          # a recurring behavior (e.g. keeps proposing destructive migrations)
KIND_GATE = "gate_correction"     # a discipline-gate flag that fired on this user's work
KIND_CONTEXT = "context"          # single-value facts: current_branch, recent_pr, ...

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "in", "on",
    "for", "and", "or", "it", "this", "that", "with", "at", "as", "be",
    "by", "from", "how", "what", "why", "do", "does", "did", "i", "you",
    "we", "my", "me", "can", "will", "should", "please",
}

# Vocabulary from DESIGN.md's architecture map — matched against file
# paths so file memories carry cross-cutting tags ("auth", "migration")
# even when the path itself doesn't spell them out.
_DOMAIN_TAGS = {
    "auth": ("auth", "jwt", "session", "middleware"),
    "migration": ("migration", "migrations", "schema"),
    "webhook": ("webhook", "webhooks", "stripe"),
    "socket": ("socket", "sockets", "realtime", "pubsub"),
    "store": ("store", "stores", "zustand"),
    "route": ("route", "routes", "api"),
    "job": ("job", "jobs", "pg-boss", "queue"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_user_id(user_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]", "_", user_id.strip())
    if not slug:
        raise ValueError("user_id must contain at least one alphanumeric character")
    return slug


def _tokenize(text: str) -> list:
    words = re.findall(r"[a-z0-9_./-]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def _content_key(text: str) -> str:
    return hashlib.sha1(text.strip().lower().encode("utf-8")).hexdigest()[:16]


def _path_tags(file_path: str) -> list:
    lowered = file_path.lower()
    tags = set()
    for tag, needles in _DOMAIN_TAGS.items():
        if any(n in lowered for n in needles):
            tags.add(tag)
    return sorted(tags)


def _age_days(iso_timestamp: str, now: datetime) -> float:
    try:
        ts = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 86400.0)


@dataclass
class MemoryEntry:
    id: int
    kind: str
    key: str
    content: str
    tags: str
    weight: float
    hit_count: int
    created_at: str
    updated_at: str
    score: float = 0.0

    @property
    def tag_list(self) -> list:
        return [t for t in self.tags.split(",") if t] if self.tags else []


def _to_entry(row: sqlite3.Row, score: float = 0.0) -> MemoryEntry:
    return MemoryEntry(
        id=row["id"], kind=row["kind"], key=row["key"], content=row["content"],
        tags=row["tags"] or "", weight=row["weight"], hit_count=row["hit_count"],
        created_at=row["created_at"], updated_at=row["updated_at"], score=score,
    )


class MemoryStore:
    """Per-user persistent memory backed by SQLite. One table per user."""

    def __init__(self, db_path=DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                table_name TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- per-user table management ------------------------------------

    def _table_for(self, user_id: str) -> str:
        table = f"memories_{_sanitize_user_id(user_id)}"
        now = _now()
        row = self._conn.execute(
            "SELECT table_name FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO users (user_id, table_name, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                (user_id, table, now, now),
            )
            self._conn.execute(f"""
                CREATE TABLE IF NOT EXISTS "{table}" (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT DEFAULT '',
                    weight REAL NOT NULL DEFAULT 1.0,
                    hit_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(kind, key)
                )
            """)
            self._conn.commit()
        else:
            self._conn.execute("UPDATE users SET last_seen = ? WHERE user_id = ?", (now, user_id))
            self._conn.commit()
            table = row["table_name"]
        return table

    # -- writing (Rivet decides what to remember) ----------------------

    def remember(self, user_id: str, kind: str, content: str, key: str = None,
                 tags=None, weight: float = 1.0) -> None:
        """Store or reinforce a memory. Same (kind, key) upserts: content is
        replaced with the latest version, weight and hit_count accumulate so
        recurring evidence outranks one-off mentions."""
        table = self._table_for(user_id)
        key = key or _content_key(content)
        tag_str = ",".join(sorted(set(tags))) if tags else ""
        now = _now()
        self._conn.execute(f"""
            INSERT INTO "{table}" (kind, key, content, tags, weight, hit_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(kind, key) DO UPDATE SET
                content = excluded.content,
                tags = CASE WHEN excluded.tags != '' THEN excluded.tags ELSE tags END,
                weight = weight + excluded.weight,
                hit_count = hit_count + 1,
                updated_at = excluded.updated_at
        """, (kind, key, content, tag_str, weight, now, now))
        self._conn.commit()

    def remember_file(self, user_id: str, file_path: str, note: str = "") -> None:
        """A file the user touched, optionally with what they were doing there."""
        self.remember(user_id, KIND_FILE, note or file_path, key=file_path,
                      tags=_path_tags(file_path), weight=1.0)

    def remember_qa(self, user_id: str, intent: str, outcome: str, tags=None) -> None:
        """A question + answer, compressed to intent/outcome (not full transcript)."""
        content = f"Q: {intent.strip()}\nA: {outcome.strip()}"
        self.remember(user_id, KIND_QA, content, key=_content_key(intent), tags=tags, weight=1.0)

    def remember_pattern(self, user_id: str, pattern: str, detail: str = "", tags=None) -> None:
        """A recurring behavior worth flagging preemptively next time it shows up."""
        self.remember(user_id, KIND_PATTERN, detail or pattern, key=pattern,
                      tags=tags, weight=2.0)

    def remember_gate_correction(self, user_id: str, rule: str, message: str,
                                  severity: str = "WARN") -> None:
        """A discipline-gate flag that fired against this user's suggestion."""
        self.remember(user_id, KIND_GATE, message, key=f"{severity}:{rule}",
                      tags=[rule.lower(), severity.lower()], weight=1.5)

    def set_context(self, user_id: str, key: str, value: str) -> None:
        """Single-value session facts: current_branch, recent_pr, etc."""
        self.remember(user_id, KIND_CONTEXT, str(value), key=key, tags=[key], weight=1.0)

    def get_context(self, user_id: str, key: str, default=None):
        table = self._table_for(user_id)
        row = self._conn.execute(
            f'SELECT content FROM "{table}" WHERE kind = ? AND key = ?', (KIND_CONTEXT, key)
        ).fetchone()
        return row["content"] if row else default

    # -- reading (memory decides what to surface) -----------------------

    def recall(self, user_id: str, query: str, top_n: int = 8, kinds=None) -> list:
        """Rank stored memories against a free-text query.

        Scoring is TF-IDF-style keyword overlap (computed on the fly over
        this user's memories — no external embedding model), boosted by
        recency and by how often the memory has recurred.
        """
        table = self._table_for(user_id)
        sql = f'SELECT * FROM "{table}"'
        params = []
        if kinds:
            sql += f" WHERE kind IN ({','.join('?' for _ in kinds)})"
            params.extend(kinds)
        rows = self._conn.execute(sql, params).fetchall()
        if not rows:
            return []

        query_terms = _tokenize(query)
        if not query_terms:
            return self._rank_by_importance(rows, top_n)

        doc_freq = Counter()
        doc_terms = []
        for row in rows:
            terms = Counter(_tokenize(f"{row['key']} {row['content']} {row['tags']}"))
            doc_terms.append(terms)
            doc_freq.update(terms.keys())

        n_docs = len(rows)
        now = datetime.now(timezone.utc)
        scored = []
        for row, terms in zip(rows, doc_terms):
            overlap = sum(
                terms[qt] * math.log(1 + n_docs / doc_freq[qt])
                for qt in query_terms if terms.get(qt)
            )
            if overlap <= 0:
                continue
            recency = 1.0 / (1.0 + _age_days(row["updated_at"], now) / 14.0)
            importance = math.log(1 + row["hit_count"]) * row["weight"]
            score = overlap * (0.6 + 0.4 * recency) * (1.0 + 0.3 * importance)
            scored.append(_to_entry(row, score))

        scored.sort(key=lambda e: e.score, reverse=True)
        return scored[:top_n]

    def _rank_by_importance(self, rows, top_n: int) -> list:
        now = datetime.now(timezone.utc)
        scored = []
        for row in rows:
            recency = 1.0 / (1.0 + _age_days(row["updated_at"], now) / 14.0)
            importance = math.log(1 + row["hit_count"]) * row["weight"]
            scored.append(_to_entry(row, importance * recency))
        scored.sort(key=lambda e: e.score, reverse=True)
        return scored[:top_n]

    def top_files(self, user_id: str, n: int = 5) -> list:
        table = self._table_for(user_id)
        rows = self._conn.execute(
            f'SELECT * FROM "{table}" WHERE kind = ? ORDER BY hit_count DESC, updated_at DESC LIMIT ?',
            (KIND_FILE, n),
        ).fetchall()
        return [_to_entry(r) for r in rows]

    def recent_patterns(self, user_id: str, n: int = 5) -> list:
        table = self._table_for(user_id)
        rows = self._conn.execute(
            f'SELECT * FROM "{table}" WHERE kind = ? ORDER BY weight DESC, updated_at DESC LIMIT ?',
            (KIND_PATTERN, n),
        ).fetchall()
        return [_to_entry(r) for r in rows]

    def recent_gate_corrections(self, user_id: str, n: int = 5) -> list:
        table = self._table_for(user_id)
        rows = self._conn.execute(
            f'SELECT * FROM "{table}" WHERE kind = ? ORDER BY updated_at DESC LIMIT ?',
            (KIND_GATE, n),
        ).fetchall()
        return [_to_entry(r) for r in rows]

    def session_brief(self, user_id: str, first_message: str = "", top_n: int = 8) -> dict:
        """Everything Rivet should know when a session starts: memories
        relevant to the first message, plus standing context that should
        always be visible (frequent files, recurring patterns, gate history,
        branch/PR state) regardless of what was asked."""
        return {
            "relevant": self.recall(user_id, first_message, top_n=top_n) if first_message else [],
            "top_files": self.top_files(user_id),
            "known_patterns": self.recent_patterns(user_id),
            "gate_history": self.recent_gate_corrections(user_id),
            "current_branch": self.get_context(user_id, "current_branch"),
            "recent_pr": self.get_context(user_id, "recent_pr"),
        }

    def to_system_block(self, user_id: str, first_message: str = "") -> str:
        """Format session_brief as a text block ready to inject into the
        model's system context, in the same spirit as RivetContext in
        context_loader.py."""
        brief = self.session_brief(user_id, first_message)
        parts = ["# USER MEMORY\n"]

        if brief["current_branch"] or brief["recent_pr"]:
            parts.append("## Current Work")
            if brief["current_branch"]:
                parts.append(f"- Branch: {brief['current_branch']}")
            if brief["recent_pr"]:
                parts.append(f"- Recent PR: {brief['recent_pr']}")
            parts.append("")

        if brief["top_files"]:
            parts.append("## Frequently Touched Files")
            parts.extend(f"- {e.key}: {e.content}" for e in brief["top_files"])
            parts.append("")

        if brief["known_patterns"]:
            parts.append("## Recurring Patterns (flag preemptively)")
            parts.extend(f"- {e.key}: {e.content}" for e in brief["known_patterns"])
            parts.append("")

        if brief["gate_history"]:
            parts.append("## Discipline Gate History")
            parts.extend(f"- {e.content}" for e in brief["gate_history"])
            parts.append("")

        if brief["relevant"]:
            parts.append("## Relevant To This Question")
            parts.extend(f"- {e.content}" for e in brief["relevant"])
            parts.append("")

        return "\n".join(parts).strip()


def _cli():
    parser = argparse.ArgumentParser(description="Rivet memory database utility")
    parser.add_argument("command", choices=["init", "demo"])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()

    if args.command == "init":
        store = MemoryStore(args.db)
        print(f"Memory database ready at {store.db_path}")
        store.close()
        return

    # demo: quick smoke test exercising the write/recall paths
    store = MemoryStore(args.db)
    user = "demo_user"
    store.remember_file(user, "src/routes/auth.ts", "adding refresh-token rotation")
    store.remember_qa(user, "why does the webhook route 500 on missing signature",
                       "STRIPE_SECRET falls back to '' — Audit C2, reject instead of skip")
    store.remember_pattern(user, "proposes_destructive_migration",
                            "suggested DROP COLUMN twice — always steer to additive migrations")
    store.remember_gate_correction(user, "webhook_bypass",
                                    "Webhook signature bypass pattern (Audit C2) flagged and blocked",
                                    severity="BLOCK")
    store.set_context(user, "current_branch", "feat/refresh-token-rotation")

    print(store.to_system_block(user, first_message="can I touch the webhook signature check?"))
    store.close()


if __name__ == "__main__":
    _cli()
