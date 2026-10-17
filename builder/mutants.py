"""The packet's listed mutants, run without a model: each on a fresh copy of the checkout, killed only when pytest
exits 1 and names a failing test (an import error is not a kill), and every anchor must match exactly once.
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from builder.workspace import Workspace, looks_like_credential


@dataclass
class MutantResult:
    name: str
    status: str            # killed | survived | bad_anchor | error
    detail: str = ""


def _ignore(directory, names):
    return [n for n in names if n in (".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules")
            or looks_like_credential(n)]


def run_mutants(ws: Workspace, mutants: list, tests: list) -> list:
    out = []
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "base"
        shutil.copytree(ws.root, base, ignore=_ignore, symlinks=False)
        ok, text = ws.pytest(tests, root=str(base))
        if not ok:
            return [MutantResult("baseline", "error", "the unmutated copy fails: " + text[-500:])]
        for m in mutants:
            d = Path(td) / "m"
            if d.exists():
                shutil.rmtree(d)
            shutil.copytree(base, d)
            f = d / m["file"]
            if not f.is_file():
                out.append(MutantResult(m["name"], "bad_anchor", f"no file {m['file']}"))
                continue
            s = f.read_text()
            n = s.count(m["old"])
            if n != 1:
                out.append(MutantResult(m["name"], "bad_anchor", f"the anchor occurs {n} times"))
                continue
            f.write_text(s.replace(m["old"], m["new"], 1))
            passed, text = ws.pytest(tests, root=str(d))
            killed = (not passed) and "FAILED" in text and "error during collection" not in text
            out.append(MutantResult(m["name"], "killed" if killed else "survived", text[-300:]))
    return out
