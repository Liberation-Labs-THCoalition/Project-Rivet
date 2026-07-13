"""Build the campus_architecture Pharos pack from Rivet's context files.

Deterministic extraction — no LLM in the loop. Parses the markdown
tables and section structure of architecture_map.md and imports the
audit findings from their single source of truth
(skills/security_review.py). Output is a standard Pharos pack
(triples.json + stats.json in a directory), loadable by pack_loader and
KV-warmable by kv_injector.

Usage:
    python pharos/build_campus_pack.py [--context-dir ../context] [--out ../packs]
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _table_rows(section: str) -> list:
    rows = []
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and not set(cells[0]) <= {"-", " ", ":"}:
            rows.append(cells)
    return rows[1:] if rows else []   # drop header


def build_triples(context_dir: Path) -> list:
    triples = []
    arch_path = context_dir / "architecture_map.md"
    if arch_path.exists():
        text = arch_path.read_text()

        # Stack table: | Layer | Technology |
        stack = re.search(r"## Stack\n(.*?)\n---", text, re.DOTALL)
        if stack:
            for cells in _table_rows(stack.group(1)):
                if len(cells) >= 2:
                    triples.append({"subject": "campus_" + cells[0].lower().replace(" ", "_"),
                                    "predicate": "implemented_with",
                                    "object": cells[1]})

        # External services table: | Service | Purpose | Notes |
        services = re.search(r"## External Services\n(.*?)\n---", text, re.DOTALL)
        if services:
            for cells in _table_rows(services.group(1)):
                if len(cells) >= 2:
                    triples.append({"subject": "campus",
                                    "predicate": "integrates",
                                    "object": f"{cells[0]} ({cells[1]})"})

    # Hard operational facts.
    triples += [
        {"subject": "staging_environment", "predicate": "shares_database_with",
         "object": "production_environment"},
        {"subject": "campus_migrations", "predicate": "must_be",
         "object": "additive_only"},
        {"subject": "campus_migrations", "predicate": "must_be",
         "object": "backward_compatible_and_reversible"},
        {"subject": "campus_auth", "predicate": "uses",
         "object": "bearer_jwt_15min_access_7day_refresh"},
        {"subject": "campus_auth", "predicate": "middleware_chain",
         "object": "verifyToken -> rejectIfIneligible -> requireAdmin/requireModerator"},
        {"subject": "websocket_auth", "predicate": "verified_via",
         "object": "handshake.auth.token -> verifyToken()"},
        {"subject": "campus_deploy", "predicate": "flows_through",
         "object": "push_to_main -> coolify_rebuild (webhook flaky)"},
        {"subject": "deploy-safe.sh", "predicate": "provides",
         "object": "snapshot + smoke test + rollback"},
    ]

    # Audit findings from their single source of truth.
    from skills.security_review import AUDIT_FINDINGS, RECURRING_PATTERNS
    for f in AUDIT_FINDINGS:
        triples.append({"subject": f"audit_finding_{f.id}",
                        "predicate": "severity", "object": f.severity})
        triples.append({"subject": f"audit_finding_{f.id}",
                        "predicate": "describes", "object": f.title})
        triples.append({"subject": f"audit_finding_{f.id}",
                        "predicate": "remediation", "object": f.advice})
        if f.file_hint:
            triples.append({"subject": f"audit_finding_{f.id}",
                            "predicate": "located_at", "object": f.file_hint})
    for name, _, message in RECURRING_PATTERNS:
        triples.append({"subject": f"antipattern_{name}",
                        "predicate": "warns", "object": message})

    return triples


def main() -> None:
    ap = argparse.ArgumentParser()
    default_ctx = Path(__file__).parent.parent.parent / "context"
    default_out = Path(__file__).parent.parent.parent / "packs"
    ap.add_argument("--context-dir", default=str(default_ctx))
    ap.add_argument("--out", default=str(default_out))
    args = ap.parse_args()

    triples = build_triples(Path(args.context_dir))
    pack_dir = Path(args.out) / "campus_architecture"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "triples.json").write_text(json.dumps({
        "description": ("Multiverse Campus system architecture, operational "
                        "constraints, and confirmed audit findings"),
        "triples": triples,
    }, indent=2))
    (pack_dir / "stats.json").write_text(json.dumps({
        "triples": len(triples), "source": "build_campus_pack.py",
    }, indent=2))
    print(f"campus_architecture pack: {len(triples)} triples -> {pack_dir}")


if __name__ == "__main__":
    main()
