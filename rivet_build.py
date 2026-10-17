#!/usr/bin/env python3
"""Rivet builder: a local model works one build packet in its own checkout, with Agent Army roles, and a person
reviews the result before anything merges.

    python3 rivet_build.py PACKET.json [--url http://127.0.0.1:11450] [--model qwen3.8:27b-mxfp8] [--dry-run]

A packet is JSON: id, repo (the builder's own checkout), base_commit, interpreter, tests, phases (role, writable
files, task), must_not_change, mutants (name, file, old, new), commit_message, max_turns.

What the harness guarantees, whatever the model does:
- it starts only on a clean checkout at the packet's base commit;
- the model reads and writes only through builder.workspace (a jail, no credential files, writes only to the
  phase's listed files) and runs only the packet's tests;
- afterwards every changed path must be one the packet allowed and none on its must-not-change list;
- it commits LOCALLY, as "Rivet builder", only when the final tests pass and every listed mutant is killed. It never
  pushes. Anything less is left uncommitted, with the report saying why.
The report and the full tool-call log are written beside the checkout as <id>_report.json.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from builder.loop import OllamaChat, now, run_phase  # noqa: E402
from builder.mutants import run_mutants  # noqa: E402
from builder.workspace import Workspace  # noqa: E402

ARMY = Path(os.environ.get("AGENT_ARMY", str(Path.home() / "Project-Agent-Army")))
REQUIRED = ("id", "repo", "base_commit", "interpreter", "tests", "phases", "commit_message")
BUILDER_NAME, BUILDER_EMAIL = "Rivet builder", "builder@liberation-labs.org"


class PacketError(ValueError):
    pass


def _git(repo, *args) -> str:
    # 60 s per git call on a local checkout: not a measured figure; a local git that takes longer is a fault.
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise PacketError(f"git {' '.join(args)} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def role_text(role: str) -> str:
    f = ARMY / role / "SKILL.md"
    if f.is_file():
        return f.read_text()
    return ("Implement the task exactly as specified. Change only the files you are allowed to write. Keep the "
            "existing style. Make the packet's tests pass without weakening any test.")


def check_packet(p: dict) -> None:
    missing = [k for k in REQUIRED if k not in p]
    if missing:
        raise PacketError(f"packet lacks {missing}")
    repo = Path(p["repo"])
    if not (repo / ".git").exists():
        raise PacketError(f"{repo} is not a git checkout")
    head = _git(repo, "rev-parse", "HEAD").strip()
    if not head.startswith(p["base_commit"]):
        raise PacketError(f"{repo} is at {head[:7]}, not the packet's base {p['base_commit']}")
    if _git(repo, "status", "--porcelain").strip():
        raise PacketError(f"{repo} has uncommitted changes; the builder starts only on a clean checkout")
    for ph in p["phases"]:
        if not ph.get("writable"):
            raise PacketError(f"phase {ph.get('role')} lists no writable files")


def changed_paths(repo) -> list:
    out = []
    for line in _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines():
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        out.append(path.strip('"'))
    return out


def build(p: dict, model, log=print) -> dict:
    check_packet(p)
    repo = p["repo"]
    allowed = sorted({w for ph in p["phases"] for w in ph["writable"]})
    ws = Workspace(root=repo, interpreter=p["interpreter"])
    report = {"id": p["id"], "started": now(), "base_commit": p["base_commit"], "phases": [], "committed": None}
    for ph in p["phases"]:
        ws.writable = list(ph["writable"])
        log(f"phase {ph['role']}: writable {ws.writable}")
        res = run_phase(ws, model, ph["role"], role_text(ph["role"]), ph["task"], max_turns=p.get("max_turns", 40),
                        log=log)
        report["phases"].append(asdict(res))
    changed = changed_paths(repo)
    probe = Workspace(root=repo, interpreter=p["interpreter"], writable=allowed)
    outside = [c for c in changed if not probe.may_write(c)]
    protected = [c for c in changed if c in set(p.get("must_not_change", []))]
    ok_tests, test_out = ws.pytest(p["tests"])
    report.update(changed=changed, outside_allowlist=outside, touched_protected=protected, tests_passed=ok_tests,
                  tests_tail=test_out[-1500:])
    mutants = run_mutants(ws, p.get("mutants", []), p["tests"]) if ok_tests and not outside and not protected else []
    report["mutants"] = [asdict(m) for m in mutants]
    all_killed = all(m.status == "killed" for m in mutants)
    reasons = []
    if not changed:
        reasons.append("nothing changed")
    if outside:
        reasons.append(f"changed paths outside the packet's writable files: {outside}")
    if protected:
        reasons.append(f"touched must-not-change files: {protected}")
    if not ok_tests:
        reasons.append("the final tests fail")
    if ok_tests and not all_killed:
        reasons.append("not every listed mutant was killed: "
                       + ", ".join(f"{m.name}={m.status}" for m in mutants if m.status != "killed"))
    if not all(ph["finished"] for ph in report["phases"]):
        reasons.append("a phase ended without the model saying it was finished")
    report["not_committed_because"] = reasons
    if not reasons:
        _git(repo, "add", "--", *changed)
        msg = (p["commit_message"].rstrip() + f"\n\nBuilt by {BUILDER_NAME} ({getattr(model, 'model', 'model')}) from "
               f"packet {p['id']}; {len(mutants)} listed mutants killed. Not reviewed yet: CC reviews before merge.\n")
        subprocess.run(["git", "-c", f"user.name={BUILDER_NAME}", "-c", f"user.email={BUILDER_EMAIL}", "commit", "-q",
                        "-m", msg], cwd=repo, check=True, timeout=60)
        report["committed"] = _git(repo, "rev-parse", "HEAD").strip()
    report["finished"] = now()
    out = Path(repo).parent / f"{p['id']}_report.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    log(f"report: {out}; committed: {report['committed']}; reasons: {reasons}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rivet_build.py")
    ap.add_argument("packet")
    ap.add_argument("--url", default=os.environ.get("RIVET_BUILDER_URL", "http://127.0.0.1:11450"))
    ap.add_argument("--model", default=os.environ.get("RIVET_BUILDER_MODEL", "qwen3.8:27b-mxfp8"))
    ap.add_argument("--dry-run", action="store_true", help="check the packet and the checkout; run nothing")
    a = ap.parse_args(argv)
    p = json.loads(Path(a.packet).read_text())
    if a.dry_run:
        check_packet(p)
        print(f"packet {p['id']} ok: {p['repo']} at {p['base_commit']}, clean")
        return 0
    rep = build(p, OllamaChat(a.url, a.model))
    return 0 if rep["committed"] else 1


if __name__ == "__main__":
    sys.exit(main())
