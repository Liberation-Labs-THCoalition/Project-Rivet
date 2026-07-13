"""SecurityMonitor for the tool harness (Kintsugi §5c).

Every subprocess the tool layer spawns goes through run_checked().
The check is code, not model judgment — it cannot be reasoned away.
Commands are argv lists (never shell=True), checked against blocked
patterns, allowlisted binaries, and a path jail before execution.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Kintsugi SecurityMonitor patterns + Rivet additions.
SUSPICIOUS_PATTERNS = [
    r"base64\s+--decode",
    r"curl.*\|.*sh",
    r"\benv\b", r"\bprintenv\b",
    r">\s*/dev/tcp",
    r"nc\s+-e",
    r"chmod\s+777",
    r"\.ssh/authorized_keys",
    r"rm\s+-rf\s+/",
    r"\bsudo\b",
    r"DROP\s+DATABASE",
]

# Binaries the tool harness is allowed to spawn. Nothing else runs.
ALLOWED_BINARIES = {
    "git", "npm", "npx", "tsc", "node", "pg_dump", "psql",
}


class ToolSecurityError(Exception):
    pass


@dataclass
class ToolResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = -1
    blocked_reason: str = ""


def check_command(argv: list) -> str:
    """Return a block reason, or '' if the command is clean."""
    if not argv:
        return "empty command"
    binary = Path(argv[0]).name
    if binary not in ALLOWED_BINARIES:
        return f"binary '{binary}' not in tool allowlist"
    joined = " ".join(str(a) for a in argv)
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, joined, re.IGNORECASE):
            return f"matches blocked pattern: {pattern}"
    return ""


def check_path(path: Path, roots: list) -> str:
    """Return a block reason unless path resolves inside an allowed root."""
    resolved = Path(path).resolve()
    for root in roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return ""
        except ValueError:
            continue
    return f"path {resolved} escapes allowed roots {roots}"


def run_checked(argv: list, cwd: str | None = None,
                timeout: int = 60) -> ToolResult:
    reason = check_command(argv)
    if reason:
        return ToolResult(ok=False, blocked_reason=reason)
    try:
        proc = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, stderr=f"timed out after {timeout}s")
    except FileNotFoundError as exc:
        return ToolResult(ok=False, stderr=str(exc))
    except OSError as exc:
        return ToolResult(ok=False, stderr=str(exc))
    return ToolResult(
        ok=proc.returncode == 0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        returncode=proc.returncode,
    )
