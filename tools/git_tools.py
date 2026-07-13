"""Git history tools — log, diff, blame — over the campus repo."""

from dataclasses import dataclass

from tools.guard import run_checked


@dataclass
class GitTools:
    repo_root: str

    def log(self, since: str = "7 days ago", limit: int = 20,
            path: str = "") -> str:
        argv = ["git", "log", "--oneline", f"--since={since}", f"-{limit}"]
        if path:
            argv += ["--", path]
        result = run_checked(argv, cwd=self.repo_root, timeout=15)
        return result.stdout if result.ok else f"(git log failed: {result.stderr or result.blocked_reason})"

    def diff(self, ref: str = "HEAD~5..HEAD", path: str = "",
             stat_only: bool = False) -> str:
        argv = ["git", "diff", ref]
        if stat_only:
            argv.append("--stat")
        if path:
            argv += ["--", path]
        result = run_checked(argv, cwd=self.repo_root, timeout=30)
        out = result.stdout if result.ok else f"(git diff failed: {result.stderr or result.blocked_reason})"
        return out[:20_000]

    def blame(self, path: str, line_start: int = 1,
              line_end: int = 50) -> str:
        argv = ["git", "blame", "-L", f"{line_start},{line_end}",
                "--date=short", "--", path]
        result = run_checked(argv, cwd=self.repo_root, timeout=20)
        return result.stdout if result.ok else f"(git blame failed: {result.stderr or result.blocked_reason})"

    def recent_authors(self, path: str, limit: int = 5) -> str:
        argv = ["git", "log", f"-{limit}", "--format=%h %ad %an %s",
                "--date=short", "--", path]
        result = run_checked(argv, cwd=self.repo_root, timeout=15)
        return result.stdout if result.ok else ""
