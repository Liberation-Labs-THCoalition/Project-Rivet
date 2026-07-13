"""Git-aware file access for the campus repo.

Reads are jailed to the configured repo root. Writes additionally
require the target to be clean in git (no clobbering uncommitted work)
and are disabled entirely unless the operator turns them on in config —
Rivet is a colleague that suggests; writing into the repo is a
consensus-gated action.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from tools.guard import ToolResult, check_path, run_checked

MAX_READ_BYTES = 200_000


@dataclass
class FileReadResult:
    ok: bool
    path: str = ""
    content: str = ""
    truncated: bool = False
    git_status: str = ""      # '' = clean/untracked-unknown, else porcelain code
    error: str = ""


@dataclass
class RepoFiles:
    repo_root: str
    write_enabled: bool = False
    files_read: list = field(default_factory=list)

    def _jail(self, rel_path: str) -> tuple:
        target = Path(self.repo_root) / rel_path
        reason = check_path(target, [self.repo_root])
        return target, reason

    def read(self, rel_path: str) -> FileReadResult:
        target, reason = self._jail(rel_path)
        if reason:
            return FileReadResult(ok=False, path=rel_path, error=reason)
        if not target.exists():
            return FileReadResult(ok=False, path=rel_path,
                                  error=f"not found: {rel_path}")
        if not target.is_file():
            return FileReadResult(ok=False, path=rel_path,
                                  error=f"not a file: {rel_path}")
        raw = target.read_bytes()
        truncated = len(raw) > MAX_READ_BYTES
        content = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        self.files_read.append(rel_path)
        return FileReadResult(
            ok=True, path=rel_path, content=content, truncated=truncated,
            git_status=self._git_status(rel_path),
        )

    def write(self, rel_path: str, content: str) -> ToolResult:
        if not self.write_enabled:
            return ToolResult(
                ok=False,
                blocked_reason=("repo writes are disabled by config "
                                "(tools.repo_write_enabled) — Rivet suggests, "
                                "humans apply"),
            )
        target, reason = self._jail(rel_path)
        if reason:
            return ToolResult(ok=False, blocked_reason=reason)
        status = self._git_status(rel_path)
        if status and status != "??":
            return ToolResult(
                ok=False,
                blocked_reason=(f"{rel_path} has uncommitted changes "
                                f"(git status '{status}') — refusing to clobber"),
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return ToolResult(ok=True, stdout=f"wrote {rel_path}")

    def search(self, pattern: str, glob: str = "", max_results: int = 50) -> list:
        """Regex search across the repo via git grep (respects .gitignore)."""
        argv = ["git", "grep", "-n", "-E", "--", pattern]
        if glob:
            argv += [glob]
        result = run_checked(argv, cwd=self.repo_root, timeout=30)
        if not result.ok and not result.stdout:
            return []
        hits = []
        for line in result.stdout.splitlines()[:max_results]:
            m = re.match(r"^([^:]+):(\d+):(.*)$", line)
            if m:
                hits.append({"path": m.group(1), "line": int(m.group(2)),
                             "text": m.group(3).strip()[:200]})
        return hits

    def list_dir(self, rel_path: str = ".") -> list:
        target, reason = self._jail(rel_path)
        if reason or not target.is_dir():
            return []
        return sorted(
            p.name + ("/" if p.is_dir() else "")
            for p in target.iterdir() if p.name != ".git"
        )

    def _git_status(self, rel_path: str) -> str:
        result = run_checked(
            ["git", "status", "--porcelain", "--", rel_path],
            cwd=self.repo_root, timeout=10,
        )
        if result.stdout.strip():
            return result.stdout.strip()[:2]
        return ""
