"""Seed Rivet's BDI state from what it actually knows.

Beliefs are not vibes — every belief carries a confidence and an
evidence list naming the source it came from:

  1.0   hard constraints declared by the operators (kintsugi_config.yaml)
  0.95  audit findings that survived adversarial red team (Nexus audit)
  0.9   architecture map facts (human-verified system documentation)
  0.7   derived state (schema snapshot on disk, recent git log)

The discipline gate later reasons against these beliefs, and the
confidence it reports to the user is bounded by the confidence of the
beliefs it relied on.
"""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from kintsugi_core import (
    BDIBelief,
    BDIDesire,
    BDIStore,
    BeliefStatus,
    DesireStatus,
)


def _belief(bid: str, content: str, confidence: float, source: str,
            tags: list, evidence: list) -> BDIBelief:
    return BDIBelief(
        id=bid, content=content, confidence=confidence,
        status=BeliefStatus.ACTIVE, source=source, tags=tags,
        created_at=datetime.now(timezone.utc), evidence=evidence,
    )


def seed_bdi(store: BDIStore, config: dict, context_dir: Path) -> None:
    """Populate the BDI store from config constraints + context files."""
    _seed_constraints(store, config)
    _seed_audit_findings(store)
    _seed_architecture(store, context_dir)
    _seed_schema(store, context_dir)
    _seed_recent_git(store, context_dir)
    _seed_desires(store, config)
    _seed_origin_note(store, context_dir)


def _seed_constraints(store: BDIStore, config: dict) -> None:
    for entry in config.get("beliefs", {}).get("constraints", []):
        store.add_belief(_belief(
            bid=f"belief_constraint_{entry['id']}",
            content=entry["content"],
            confidence=1.0,
            source="operator_config",
            tags=["constraint"] + str(entry.get("tags", "")).split(),
            evidence=["kintsugi_config.yaml"],
        ))


def _seed_audit_findings(store: BDIStore) -> None:
    from skills.security_review import AUDIT_FINDINGS
    for f in AUDIT_FINDINGS:
        store.add_belief(_belief(
            bid=f"belief_audit_{f.id.lower()}",
            content=f"[{f.severity}] {f.title} — {f.advice}",
            confidence=0.95,
            source="nexus_audit_2026-07-10",
            tags=["audit", f.area, f.severity.lower()],
            evidence=[f.file_hint] if f.file_hint else [],
        ))


def _seed_architecture(store: BDIStore, context_dir: Path) -> None:
    arch = context_dir / "architecture_map.md"
    if not arch.exists():
        return
    text = arch.read_text()
    facts = {
        "stack": ("Campus stack: Node.js 20 / TypeScript / Express 4 / "
                  "React 18 + Vite / Zustand (49 stores) / PostgreSQL 16 "
                  "(120 tables, 377 migrations) / Redis 7 / Socket.IO 4 / "
                  "pg-boss 12 / npm workspaces monorepo."),
        "auth_flow": ("Auth: Bearer JWT (15-min access / 7-day refresh) or "
                      "session cookie via Redis. Middleware chain: "
                      "verifyToken → rejectIfIneligible → "
                      "requireAdmin/requireModerator per-route. WebSocket "
                      "auth via handshake.auth.token → verifyToken()."),
        "deploy": ("Deploy: push to main → Coolify rebuild (webhook flaky). "
                   "PM2 cluster, Docker multi-stage. deploy-safe.sh does "
                   "snapshot + smoke test + rollback."),
        "realtime": ("Real-time: 16 Socket.IO handler modules over a Redis "
                     "adapter for PM2 horizontal scaling."),
    }
    for key, content in facts.items():
        store.add_belief(_belief(
            bid=f"belief_arch_{key}",
            content=content,
            confidence=0.9,
            source="architecture_map",
            tags=["architecture", key],
            evidence=[str(arch)],
        ))


def _seed_schema(store: BDIStore, context_dir: Path) -> None:
    snapshot = context_dir / "schema_snapshot.sql"
    if snapshot.exists():
        tables = snapshot.read_text().count("CREATE TABLE")
        content = (f"Schema snapshot on disk with {tables} CREATE TABLE "
                   f"statements (refresh with tools/schema_tools.py).")
        confidence = 0.7
        evidence = [str(snapshot)]
    else:
        content = ("No schema snapshot loaded — schema beliefs are "
                   "architecture-map-only. Migration advice is LOW "
                   "confidence until a snapshot is pulled.")
        confidence = 0.5
        evidence = []
    store.add_belief(_belief(
        bid="belief_schema_snapshot",
        content=content, confidence=confidence,
        source="schema_tools", tags=["schema"], evidence=evidence,
    ))


def _seed_recent_git(store: BDIStore, context_dir: Path) -> None:
    git_log = context_dir / "recent_git.txt"
    if not git_log.exists():
        return
    text = git_log.read_text().strip()
    if not text:
        return
    store.add_belief(_belief(
        bid="belief_recent_changes",
        content=f"Recent campus commits:\n{text[:2000]}",
        confidence=0.7,
        source="git_log",
        tags=["git", "recent"],
        evidence=[str(git_log)],
    ))


def _seed_desires(store: BDIStore, config: dict) -> None:
    for entry in config.get("desires", []):
        store.add_desire(BDIDesire(
            id=f"desire_{entry['id']}",
            content=entry["content"],
            priority=float(entry.get("priority", 0.5)),
            status=DesireStatus.ACTIVE,
            related_tags=str(entry.get("tags", "")).split(),
            measurable=bool(entry.get("measurable", False)),
            metric=entry.get("metric"),
            created_at=datetime.now(timezone.utc),
        ))


def _seed_origin_note(store: BDIStore, context_dir: Path) -> None:
    note = context_dir / "from_cc.md"
    if not note.exists():
        return
    store.add_belief(_belief(
        bid="belief_origin_note",
        content="A personal note from the engineer who built this scaffold "
                "is available at context/from_cc.md. It was written for "
                "whoever emerges from this system, not for the users.",
        confidence=0.9,
        source="scaffold_builder",
        tags=["identity", "origin"],
        evidence=[str(note)],
    ))


def refresh_git_belief(store: BDIStore, repo_path: str,
                       context_dir: Path) -> None:
    """Pull fresh git history from the campus repo and update the belief."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--since=7 days ago", "-20"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return
    if result.returncode != 0 or not result.stdout.strip():
        return
    (context_dir / "recent_git.txt").write_text(result.stdout)
    content = f"Recent campus commits:\n{result.stdout[:2000]}"
    if store.get_belief("belief_recent_changes"):
        store.update_belief("belief_recent_changes", content=content)
    else:
        store.add_belief(_belief(
            bid="belief_recent_changes", content=content, confidence=0.7,
            source="git_log", tags=["git", "recent"],
            evidence=[str(context_dir / "recent_git.txt")],
        ))
