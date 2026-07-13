"""Schema inspection for the campus PostgreSQL database.

Two modes, in preference order:

  live      pg_dump --schema-only against the configured DSN (read-only
            operation) plus migration status from the migrations table
  snapshot  a schema_snapshot.sql on disk in the context dir

Rivet never runs DDL. This module *inspects* only — the argv allowlist
in tools.guard has no path to a writing psql invocation because every
psql call here is built with a fixed read-only query.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from tools.guard import run_checked


@dataclass
class SchemaInfo:
    source: str            # live | snapshot | none
    table_count: int = 0
    tables: list = None
    migration_status: str = ""
    raw_available: bool = False
    error: str = ""


class SchemaTools:
    def __init__(self, context_dir: Path, dsn: str = "",
                 migrations_dir: str = ""):
        self.context_dir = Path(context_dir)
        self.dsn = dsn
        self.migrations_dir = migrations_dir
        self.snapshot_path = self.context_dir / "schema_snapshot.sql"

    # ------------------------------------------------------------------

    def inspect(self) -> SchemaInfo:
        if self.dsn:
            info = self._inspect_live()
            if not info.error:
                return info
        return self._inspect_snapshot()

    def refresh_snapshot(self) -> SchemaInfo:
        """Pull a fresh schema-only dump to the context dir (live mode)."""
        if not self.dsn:
            return SchemaInfo(source="none", error="no DSN configured")
        result = run_checked(
            ["pg_dump", "--schema-only", "--no-owner", "--no-privileges",
             self.dsn],
            timeout=120,
        )
        if not result.ok:
            return SchemaInfo(source="none",
                              error=result.stderr or result.blocked_reason)
        self.snapshot_path.write_text(result.stdout)
        return self._inspect_snapshot()

    def table_definition(self, table: str) -> str:
        """Extract one table's definition from the snapshot."""
        if not self.snapshot_path.exists():
            return ""
        text = self.snapshot_path.read_text()
        pattern = (r"CREATE TABLE[^;]*?\b" + re.escape(table) + r"\b[^;]*?;")
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        return m.group(0) if m else ""

    # ------------------------------------------------------------------

    def _inspect_live(self) -> SchemaInfo:
        result = run_checked(
            ["psql", self.dsn, "-tAc",
             "SELECT tablename FROM pg_tables WHERE schemaname='public' "
             "ORDER BY tablename"],
            timeout=30,
        )
        if not result.ok:
            return SchemaInfo(source="live",
                              error=result.stderr or result.blocked_reason)
        tables = [t for t in result.stdout.splitlines() if t.strip()]
        return SchemaInfo(
            source="live", table_count=len(tables), tables=tables,
            migration_status=self._migration_status_live(),
            raw_available=self.snapshot_path.exists(),
        )

    def _migration_status_live(self) -> str:
        applied = run_checked(
            ["psql", self.dsn, "-tAc",
             "SELECT count(*) FROM migrations"],
            timeout=15,
        )
        applied_count = applied.stdout.strip() if applied.ok else "?"
        on_disk = "?"
        if self.migrations_dir and Path(self.migrations_dir).is_dir():
            on_disk = str(len(list(Path(self.migrations_dir).glob("*.sql"))))
        return f"applied={applied_count} on_disk={on_disk}"

    def _inspect_snapshot(self) -> SchemaInfo:
        if not self.snapshot_path.exists():
            return SchemaInfo(
                source="none",
                error=("no live DSN and no schema snapshot — schema claims "
                       "will be LOW confidence"),
            )
        text = self.snapshot_path.read_text()
        tables = re.findall(
            r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(?:public\.)?([\w\"]+)",
            text, re.IGNORECASE,
        )
        tables = [t.strip('"') for t in tables]
        return SchemaInfo(
            source="snapshot", table_count=len(tables),
            tables=sorted(set(tables)), raw_available=True,
        )
