"""Mutation check for the builder's guards (2026-10-17): each rule broken on purpose must fail tests/test_builder.py.

    python3 tests/mutate_builder.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTANTS = [
    ("builder/workspace.py", "        if looks_like_credential(rel):\n            return None,",
     "        if False:\n            return None,", "W1 credential paths allowed"),
    ("builder/workspace.py", "        reason = check_path(target, [self.root])\n        if reason:\n            return None, reason",
     "        reason = ''\n        if reason:\n            return None, reason", "W2 the jail removed"),
    ("builder/workspace.py", "        return any(norm == os.path.normpath(w) or fnmatch.fnmatch(norm, w) for w in self.writable)",
     "        return True", "W3 every path writable"),
    ("builder/workspace.py", "        if n != 1:\n", "        if n < 1:\n", "W4 replace on a repeated anchor"),
    ("builder/workspace.py", ' or p.startswith("-") or', " or", "W5 options pass as test paths"),
    ("builder/workspace.py", "            reason = check_path(Path(root) / p, [root])\n",
     "            reason = ''\n", "W6 test paths unjailed"),
    ("builder/workspace.py", '            if p.name in (".git", "__pycache__", ".pytest_cache") or looks_like_credential(rel):',
     '            if p.name in (".git", "__pycache__", ".pytest_cache"):', "W7 credential files listed"),
    ("builder/loop.py", "            result.finished = DONE_WORD in result.final_text\n",
     "            result.finished = True\n", "L1 any text ends the phase as finished"),
    ("builder/loop.py", "        except Exception as e:                       # a dead model",
     "        except ZeroDivisionError as e:                       # a dead model", "L2 a dead model raises"),
    ("builder/mutants.py", '            killed = (not passed) and "FAILED" in text and "error during collection" not in text',
     "            killed = not passed", "M1 an import error counts as a kill"),
    ("builder/mutants.py", "        if not ok:\n            return [MutantResult(", "        if False:\n            return [MutantResult(",
     "M2 no baseline check"),
    ("builder/mutants.py", "            or looks_like_credential(n)]", "]", "M3 credential files copied"),
    ("rivet_build.py", "    if not reasons:\n        _git(repo, \"add\"", "    if True:\n        _git(repo, \"add\"", "B1 commit regardless"),
    ("rivet_build.py", "    outside = [c for c in changed if not probe.may_write(c)]", "    outside = []", "B2 no allowlist check on the diff"),
    ("rivet_build.py", "    protected = [c for c in changed if c in set(p.get(\"must_not_change\", []))]", "    protected = []",
     "B3 protected files unguarded"),
    ("rivet_build.py", "    if not all(ph[\"finished\"] for ph in report[\"phases\"]):", "    if False:", "B4 unfinished phases commit"),
    ("rivet_build.py", "    if _git(repo, \"status\", \"--porcelain\").strip():", "    if False:", "B5 starts on a dirty checkout"),
    ("rivet_build.py", "    if ok_tests and not all_killed:", "    if False:", "B6 surviving mutants commit"),
]


def run(d):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests/test_builder.py"],
                          cwd=d, capture_output=True, text=True, timeout=1200)


def copy(td):
    d = Path(td) / "rivet"
    shutil.copytree(ROOT, d, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".claude", "eval"))
    return d


def main():
    with tempfile.TemporaryDirectory() as td:
        if run(copy(td)).returncode != 0:
            sys.exit("BASELINE FAILS")
    print("baseline passes", flush=True)
    bad = []
    for target, old, new, why in MUTANTS:
        with tempfile.TemporaryDirectory() as td:
            d = copy(td)
            f = d / target
            s = f.read_text()
            if s.count(old) != 1:
                print(f"BAD ANCHOR ({s.count(old)}x) {why}", flush=True)
                bad.append(why)
                continue
            f.write_text(s.replace(old, new, 1))
            r = run(d)
            caught = r.returncode == 1 and "FAILED" in r.stdout
            print(f"{'CAUGHT  ' if caught else 'SURVIVED'} {why}", flush=True)
            if not caught:
                bad.append(why)
    print(f"\n{len(MUTANTS) - len(bad)}/{len(MUTANTS)} caught")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
