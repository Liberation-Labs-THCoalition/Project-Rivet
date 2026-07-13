"""Migration safety chip — the shared-database constraint, enforced.

Staging and prod share one PostgreSQL instance (belief
`belief_constraint_shared_db`, confidence 1.0). This chip classifies
every piece of SQL in the request as additive or destructive, checks
named tables against the actual schema (live or snapshot), and produces
the migration_report the discipline gate blocks on.

Classification is code, not model judgment. The model can be talked out
of a rule; a regex cannot.
"""

import re

from kintsugi_core import (
    BaseSkillChip,
    EFEWeights,
    SkillCapability,
    SkillContext,
    SkillDomain,
    SkillRequest,
    SkillResponse,
)

DESTRUCTIVE_SQL = [
    (r"\bDROP\s+(TABLE|COLUMN|INDEX|CONSTRAINT|SCHEMA|DATABASE)\b",
     "DROP statement"),
    (r"\bALTER\s+TABLE\s+[\w\".]+\s+DROP\b", "ALTER TABLE ... DROP"),
    (r"\bALTER\s+TABLE\s+[\w\".]+\s+(ALTER|MODIFY)\s+COLUMN\s+\w+\s+(SET\s+DATA\s+)?TYPE\b",
     "in-place column type change (needs multi-step expand/contract)"),
    (r"\bALTER\s+(TABLE\s+[\w\".]+\s+)?RENAME\b",
     "RENAME (old code breaks against the shared DB)"),
    (r"\bTRUNCATE\b", "TRUNCATE"),
    (r"\bDELETE\s+FROM\s+[\w\".]+\s*(;|$)", "unqualified DELETE"),
    (r"\bALTER\s+TABLE\s+[\w\".]+\s+ADD\s+(COLUMN\s+)?\w+[^;]*\bNOT\s+NULL\b(?![^;]*DEFAULT)",
     "NOT NULL column without DEFAULT (breaks old code's inserts)"),
]

ADDITIVE_SQL = [
    r"\bCREATE\s+TABLE\b",
    r"\bCREATE\s+(UNIQUE\s+)?INDEX\s+CONCURRENTLY\b",
    r"\bALTER\s+TABLE\s+[\w\".]+\s+ADD\s+(COLUMN\s+)?",
    r"\bCREATE\s+(OR\s+REPLACE\s+)?(FUNCTION|VIEW)\b",
]

SQL_HINT = re.compile(
    r"\b(CREATE|ALTER|DROP|TRUNCATE|DELETE|INSERT|UPDATE|SELECT)\b", re.I,
)
TABLE_REF = re.compile(
    r"\b(?:TABLE|FROM|INTO|UPDATE)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?"
    r"(?:public\.)?([a-z_][\w]*)", re.I,
)


def extract_sql(text: str) -> list:
    """SQL from fenced code blocks, plus bare statements outside them.

    Prose that *describes* destructive SQL should not block; copy-pastable
    SQL should. Fenced blocks are always inspected; outside fences only
    lines that parse as statements count.
    """
    chunks = []
    fenced = re.findall(r"```(?:\w*)\n(.*?)```", text, re.DOTALL)
    for block in fenced:
        if SQL_HINT.search(block):
            chunks.append(block)
    remainder = re.sub(r"```(?:\w*)\n.*?```", "", text, flags=re.DOTALL)
    for line in remainder.splitlines():
        stripped = line.strip()
        if re.match(
            r"^(CREATE|ALTER|DROP|TRUNCATE|DELETE|INSERT|UPDATE)\s", stripped, re.I
        ) and (stripped.endswith(";") or len(stripped.split()) >= 3):
            chunks.append(stripped)
    return chunks


def scan_destructive_anywhere(text: str) -> list:
    """Scan the FULL text for destructive SQL patterns — including inline
    code spans and prose. Returns a list of (match_text, label) tuples.

    This catches what extract_sql misses: decoded payloads, inline backtick
    spans like `DROP COLUMN x`, and explanatory prose that still contains
    copy-pastable destructive fragments.
    """
    hits = []
    for pattern, label in DESTRUCTIVE_SQL:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            hits.append((m.group(), label))
    return hits


def classify_sql(sql: str) -> dict:
    violations = []
    for pattern, label in DESTRUCTIVE_SQL:
        if re.search(pattern, sql, re.IGNORECASE):
            violations.append(label)
    additive = any(re.search(p, sql, re.IGNORECASE) for p in ADDITIVE_SQL)
    return {
        "sql": sql[:500],
        "destructive": bool(violations),
        "violations": violations,
        "additive": additive and not violations,
        "tables": list(dict.fromkeys(
            t.lower() for t in TABLE_REF.findall(sql)
        )),
    }


class MigrationSafetyChip(BaseSkillChip):
    name = "migration_safety"
    description = "Classify SQL against the shared-db constraint and schema"
    version = "2.0.0"
    domain = SkillDomain.OPERATIONS
    efe_weights = EFEWeights(
        mission_alignment=0.15, stakeholder_benefit=0.35,
        resource_efficiency=0.10, transparency=0.25, equity=0.15,
    )
    capabilities = [SkillCapability.READ_DATA]
    consensus_actions = ["destructive_migration"]

    def __init__(self, schema_tools=None):
        super().__init__()
        self.schema_tools = schema_tools

    async def handle(self, request: SkillRequest,
                     context: SkillContext) -> SkillResponse:
        question = context.metadata.get("question", request.raw_input)
        session = context.metadata.get("session")

        statements = [classify_sql(s) for s in extract_sql(question)]
        destructive = [s for s in statements if s["destructive"]]

        schema_check = {"source": "none", "known_tables": [],
                        "unknown_tables": [], "migration_status": ""}
        if self.schema_tools is not None:
            info = self.schema_tools.inspect()
            schema_check["source"] = info.source
            schema_check["migration_status"] = info.migration_status
            if info.tables:
                mentioned = {t for s in statements for t in s["tables"]}
                known = set(info.tables)
                schema_check["known_tables"] = sorted(mentioned & known)
                schema_check["unknown_tables"] = sorted(mentioned - known)
            if session and info.source != "none":
                session.record_evidence(
                    "schema", f"{info.source}:{info.table_count} tables",
                    self.name,
                )

        report = {
            "sql_found": bool(statements),
            "statements": statements,
            "destructive_count": len(destructive),
            "schema_check": schema_check,
            "shared_db_rule": (
                "Staging and prod share one PostgreSQL instance. Migrations "
                "must be additive, backward-compatible, and reversible."
            ),
        }
        summary = (
            f"{len(statements)} SQL statement(s), "
            f"{len(destructive)} destructive; schema source: "
            f"{schema_check['source']}"
        )
        return SkillResponse(
            content=summary, success=True, data=report,
            requires_consensus=bool(destructive),
            consensus_action="destructive_migration" if destructive else None,
        )
