"""Verification runners — targeted tests and typecheck for the campus repo.

Used by the test_runner chip to verify generated code instead of
asserting it works. Both runners degrade honestly: when the repo (or
tsc/npm) isn't reachable from where Rivet runs, they return
`available=False` and the discipline gate caps confidence accordingly.
"""

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tools.guard import run_checked


@dataclass
class VerifyResult:
    available: bool
    passed: bool = False
    command: str = ""
    output: str = ""


class TestTools:
    def __init__(self, repo_root: str = ""):
        self.repo_root = repo_root

    def repo_available(self) -> bool:
        return bool(self.repo_root) and Path(self.repo_root).is_dir()

    def typecheck(self, workspace: str = "server") -> VerifyResult:
        """tsc --noEmit over a workspace of the monorepo."""
        if not self.repo_available():
            return VerifyResult(available=False,
                                output="campus repo not on this machine")
        cwd = str(Path(self.repo_root) / workspace)
        result = run_checked(["npx", "tsc", "--noEmit"], cwd=cwd, timeout=300)
        return VerifyResult(
            available=True, passed=result.ok,
            command=f"npx tsc --noEmit (in {workspace}/)",
            output=(result.stdout + result.stderr)[-5000:],
        )

    def run_tests(self, test_file: str = "", workspace: str = "server") -> VerifyResult:
        """npm test, optionally targeted at one file."""
        if not self.repo_available():
            return VerifyResult(available=False,
                                output="campus repo not on this machine")
        cwd = str(Path(self.repo_root) / workspace)
        argv = ["npm", "test"]
        if test_file:
            argv += ["--", test_file]
        result = run_checked(argv, cwd=cwd, timeout=600)
        return VerifyResult(
            available=True, passed=result.ok,
            command=" ".join(argv) + f" (in {workspace}/)",
            output=(result.stdout + result.stderr)[-5000:],
        )

    def syntax_check_snippet(self, code: str, lang: str = "ts") -> VerifyResult:
        """Standalone syntax check of a generated snippet via tsc.

        Weaker than a repo typecheck (no project types) but catches
        outright syntax errors even when the repo isn't present.
        """
        tsc_probe = run_checked(["npx", "tsc", "--version"], timeout=30)
        if not tsc_probe.ok:
            return VerifyResult(available=False,
                                output="tsc not available on this machine")
        suffix = ".ts" if lang in ("ts", "typescript") else ".js"
        with tempfile.NamedTemporaryFile(
            "w", suffix=suffix, delete=False) as tmp:
            tmp.write(code)
            tmp_path = tmp.name
        try:
            result = run_checked(
                ["npx", "tsc", "--noEmit", "--skipLibCheck", "--target",
                 "es2022", "--moduleResolution", "bundler", "--module",
                 "esnext", tmp_path],
                timeout=60,
            )
            return VerifyResult(
                available=True, passed=result.ok,
                command="npx tsc --noEmit <snippet>",
                output=(result.stdout + result.stderr)[-3000:],
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)


def extract_code_blocks(markdown: str) -> list:
    """Pull fenced code blocks out of a draft answer for verification."""
    blocks = []
    for m in re.finditer(r"```(\w*)\n(.*?)```", markdown, re.DOTALL):
        lang = (m.group(1) or "").lower()
        blocks.append({"lang": lang, "code": m.group(2)})
    return blocks
