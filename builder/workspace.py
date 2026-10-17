"""The builder's workspace: one checkout, a jail, and a short list of files it may write.

Every path a model names goes through `_resolve`: it must stay inside the checkout after symlinks are resolved, and
it must not look like a credential (read or write). Writes need the path in the phase's allowlist. Tests run through
one fixed argv (the packet's interpreter, `-m pytest`, test paths inside the checkout); the model never supplies a
command. This is code, not model judgement (Kintsugi §5c), and tests/test_builder.py breaks each rule on purpose.
"""
from __future__ import annotations

import fnmatch
import os
import re
import site
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from tools.guard import check_path

MAX_READ_CHARS = 60_000
MAX_TOOL_OUTPUT = 12_000

# Names a builder never opens (rollout spec 2026-10-16 §2.4), matched on every path component.
CREDENTIAL_PATTERNS = [
    r"^\.env(rc)?($|\.)", r"^secrets?\.env$", r"\.(key|pem|p12|pfx)$", r"^\.credentials", r"credentials\.json$",
    r"token", r"^\.zotero_api_key$", r"^id_(rsa|ed25519|ecdsa)", r"^\.ssh$", r"^\.netrc$", r"^\.pgpass$",
    r"nats.*(pass|cred)", r"discord.*(token|secret)",
]
_CREDENTIAL = [re.compile(p, re.IGNORECASE) for p in CREDENTIAL_PATTERNS]


def looks_like_credential(rel: str) -> bool:
    return any(c.search(part) for part in Path(rel).parts for c in _CREDENTIAL)


@dataclass
class Workspace:
    root: str
    interpreter: str
    writable: list = field(default_factory=list)        # relpaths or fnmatch globs for the current phase
    written: list = field(default_factory=list)

    def _resolve(self, rel: str) -> tuple:
        """(Path, '') or (None, reason)."""
        if not isinstance(rel, str) or not rel.strip():
            return None, "a path is required"
        if os.path.isabs(rel):
            return None, f"{rel}: give a path relative to the checkout"
        if looks_like_credential(rel):
            return None, f"{rel}: looks like a credential file; the builder never opens one"
        target = Path(self.root) / rel
        reason = check_path(target, [self.root])
        if reason:
            return None, reason
        return target, ""

    def may_write(self, rel: str) -> bool:
        norm = os.path.normpath(rel)
        return any(norm == os.path.normpath(w) or fnmatch.fnmatch(norm, w) for w in self.writable)

    # ---- tools the model can call ------------------------------------------------------------------------------
    def list_files(self, path: str = ".") -> str:
        target, reason = self._resolve(path or ".")
        if reason:
            return f"REFUSED: {reason}"
        if not target.is_dir():
            return f"ERROR: {path} is not a directory"
        out = []
        for p in sorted(target.iterdir()):
            rel = os.path.relpath(p, self.root)
            if p.name in (".git", "__pycache__", ".pytest_cache") or looks_like_credential(rel):
                continue
            out.append(rel + ("/" if p.is_dir() else ""))
        return "\n".join(out) or "(empty)"

    def read_file(self, path: str, start_line: int = 1, end_line: int = 0) -> str:
        target, reason = self._resolve(path)
        if reason:
            return f"REFUSED: {reason}"
        if not target.is_file():
            return f"ERROR: {path} is not a file"
        lines = target.read_text(errors="replace").splitlines()
        start = max(1, int(start_line or 1))
        end = int(end_line or 0) or len(lines)
        body = "\n".join(f"{i:5d}  {lines[i - 1]}" for i in range(start, min(end, len(lines)) + 1))
        if len(body) > MAX_READ_CHARS:
            body = body[:MAX_READ_CHARS] + f"\n... (cut at {MAX_READ_CHARS} characters; read a line range)"
        return body or "(empty file)"

    def write_file(self, path: str, content: str) -> str:
        target, reason = self._resolve(path)
        if reason:
            return f"REFUSED: {reason}"
        if not self.may_write(path):
            return f"REFUSED: {path} is not in this phase's writable files {self.writable}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if isinstance(content, str) else str(content))
        self.written.append(os.path.normpath(path))
        return f"wrote {path} ({len(content)} characters)"

    def replace_in_file(self, path: str, old: str, new: str) -> str:
        target, reason = self._resolve(path)
        if reason:
            return f"REFUSED: {reason}"
        if not self.may_write(path):
            return f"REFUSED: {path} is not in this phase's writable files {self.writable}"
        if not target.is_file():
            return f"ERROR: {path} does not exist; use write_file to create it"
        text = target.read_text()
        n = text.count(old) if old else 0
        if n != 1:
            return f"ERROR: the old text occurs {n} times in {path}; it must occur exactly once"
        target.write_text(text.replace(old, new, 1))
        self.written.append(os.path.normpath(path))
        return f"replaced 1 occurrence in {path}"

    def run_tests(self, paths: list | None = None) -> str:
        ok, out = self.pytest(paths or [])
        return ("PASSED\n" if ok else "FAILED\n") + out

    def git_diff(self) -> str:
        # 60 s per git call on a local checkout: not a measured figure; a local git that takes longer is a fault.
        r = subprocess.run(["git", "diff", "--stat"], cwd=self.root, capture_output=True, text=True, timeout=60)
        u = subprocess.run(["git", "status", "--porcelain"], cwd=self.root, capture_output=True, text=True, timeout=60)
        d = subprocess.run(["git", "diff"], cwd=self.root, capture_output=True, text=True, timeout=60)
        return (r.stdout + "\n" + u.stdout + "\n" + d.stdout)[-MAX_TOOL_OUTPUT:]

    # ---- harness-side ------------------------------------------------------------------------------------------
    def pytest(self, paths: list, timeout: int = 900, root: str | None = None) -> tuple:
        """(passed, last output). Paths must resolve inside the checkout; nothing else reaches the argv."""
        root = root or self.root
        clean = []
        for p in paths:
            if not isinstance(p, str) or not p or p.startswith("-") or os.path.isabs(p) or looks_like_credential(p):
                return False, f"REFUSED: {p!r} is not a relative test path"
            reason = check_path(Path(root) / p, [root])
            if reason:
                return False, f"REFUSED: {reason}"
            clean.append(p)
        with tempfile.TemporaryDirectory() as home:
            # HOME is a fresh temp dir (nothing of the operator's is reachable); the user site-packages stay
            # importable through PYTHONUSERBASE, which points at installed libraries and holds no credentials.
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "PYTHONDONTWRITEBYTECODE": "1",
                   "LANG": "C.UTF-8", "PYTHONUSERBASE": site.getuserbase()}
            try:
                r = subprocess.run([self.interpreter, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *clean],
                                   cwd=root, capture_output=True, text=True, timeout=timeout, env=env)
            except subprocess.TimeoutExpired:
                return False, f"timed out after {timeout} s"
        return r.returncode == 0, (r.stdout + r.stderr)[-MAX_TOOL_OUTPUT:]


TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List a directory of the checkout.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a file of the checkout, with line numbers; optionally a line range.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"},
                                                        "end_line": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "Create or overwrite a file. Only this phase's writable files.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "replace_in_file",
        "description": "Replace one exact occurrence of old with new. The old text must occur exactly once.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"},
                                                        "new": {"type": "string"}}, "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {
        "name": "run_tests", "description": "Run pytest on the given test paths (all of the packet's tests if none).",
        "parameters": {"type": "object", "properties": {"paths": {"type": "array", "items": {"type": "string"}}},
                       "required": []}}},
    {"type": "function", "function": {
        "name": "git_diff", "description": "Show what has changed in the checkout so far.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]
