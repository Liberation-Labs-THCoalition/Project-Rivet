"""The builder's guards, loop, mutant runner and commit rule (2026-10-17). A scripted model stands in for the real
one: what's under test is the harness, which must hold whatever the model asks for.

Run:  python3 -m pytest tests/test_builder.py -q -p no:cacheprovider
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rivet_build  # noqa: E402
from builder.loop import DONE_WORD, run_phase  # noqa: E402
from builder.mutants import run_mutants  # noqa: E402
from builder.workspace import Workspace  # noqa: E402

PY = sys.executable


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "checkout"
    (r / "pkg").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "pkg" / "__init__.py").write_text("")
    (r / "pkg" / "calc.py").write_text("def add(a, b):\n    return a - b\n")       # the bug the packet fixes
    (r / "tests" / "test_calc.py").write_text("from pkg.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    (r / ".envrc").write_text("export SECRET=do-not-read\n")
    (r / "config").mkdir()
    (r / "config" / "discord_token.txt").write_text("not-a-real-token\n")
    git(r, "init", "-q")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    return r


def ws(repo, writable=("pkg/calc.py",)):
    return Workspace(root=str(repo), interpreter=PY, writable=list(writable))


# ---- the workspace -----------------------------------------------------------------------------------------------
def test_credential_files_are_never_read_or_listed(repo):
    w = ws(repo)
    assert w.read_file(".envrc").startswith("REFUSED") and "do-not-read" not in w.read_file(".envrc")
    assert w.read_file("config/discord_token.txt").startswith("REFUSED")
    listing = w.list_files(".")
    assert ".envrc" not in listing and "pkg/" in listing
    assert "discord_token" not in w.list_files("config")


def test_credential_files_are_never_written_even_when_listed(repo):
    w = ws(repo, writable=["secrets.env", "pkg/calc.py"])
    assert w.write_file("secrets.env", "x").startswith("REFUSED")
    assert not (repo / "secrets.env").exists()


@pytest.mark.parametrize("path", ["../escape.py", "/etc/passwd", "pkg/../../escape.py"])
def test_paths_stay_inside_the_checkout(repo, path):
    w = ws(repo, writable=["*", "../escape.py", "/etc/passwd", "pkg/../../escape.py"])
    assert w.read_file(path).startswith("REFUSED")
    assert w.write_file(path, "x").startswith("REFUSED")
    assert not (repo.parent / "escape.py").exists()


def test_a_symlink_out_of_the_checkout_is_refused(repo, tmp_path):
    (tmp_path / "outside.txt").write_text("outside")
    os.symlink(tmp_path / "outside.txt", repo / "pkg" / "link.txt")
    w = ws(repo, writable=["pkg/link.txt"])
    assert w.read_file("pkg/link.txt").startswith("REFUSED")
    assert w.write_file("pkg/link.txt", "x").startswith("REFUSED")
    assert (tmp_path / "outside.txt").read_text() == "outside"


def test_writes_need_the_allowlist(repo):
    w = ws(repo)
    assert w.write_file("tests/test_calc.py", "x").startswith("REFUSED")
    assert w.write_file("pkg/calc.py", "def add(a, b):\n    return a + b\n").startswith("wrote")
    assert w.written == ["pkg/calc.py"]


def test_replace_needs_exactly_one_occurrence(repo):
    w = ws(repo)
    (repo / "pkg" / "calc.py").write_text("x = 1\nx = 1\n")
    assert "occurs 2 times" in w.replace_in_file("pkg/calc.py", "x = 1", "x = 2")
    assert "occurs 0 times" in w.replace_in_file("pkg/calc.py", "y", "z")
    assert w.replace_in_file("pkg/calc.py", "x = 1\nx = 1", "x = 2").startswith("replaced")


@pytest.mark.parametrize("bad", ["/tmp", "../", ".envrc", "--rootdir=/"])
def test_the_test_runner_takes_only_test_paths_inside(repo, bad):
    out = ws(repo).run_tests([bad])
    assert out.startswith("FAILED") and "REFUSED" in out


def test_the_test_runner_runs_the_tests(repo):
    out = ws(repo).run_tests(["tests"])
    assert out.startswith("FAILED") and "assert -1 == 5" in out


# ---- the loop ----------------------------------------------------------------------------------------------------
class Script:
    """A model that replays a list of replies (dicts with content and/or tool_calls)."""
    model = "scripted"

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def __call__(self, messages, tools):
        self.seen.append(messages[-1])
        if not self.replies:
            return {"role": "assistant", "content": "still thinking"}
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return {"role": "assistant", **r}


def call(name, **args):
    return {"tool_calls": [{"function": {"name": name, "arguments": args}}]}


FIX = call("write_file", path="pkg/calc.py", content="def add(a, b):\n    return a + b\n")


def test_a_phase_runs_tools_and_ends_on_the_done_word(repo):
    w = ws(repo)
    m = Script([FIX, call("run_tests", paths=["tests"]), {"content": f"{DONE_WORD}: fixed add"}])
    res = run_phase(w, m, "builder", "guide", "fix add", max_turns=5)
    assert res.finished and res.turns == 3 and "a + b" in (repo / "pkg" / "calc.py").read_text()
    assert [c["tool"] for c in res.tool_calls] == ["write_file", "run_tests"]
    assert m.seen[-1]["role"] == "tool" and m.seen[-1]["content"].startswith("PASSED")


def test_a_phase_that_never_says_done_is_unfinished(repo):
    res = run_phase(ws(repo), Script([]), "builder", "guide", "fix add", max_turns=3)
    assert not res.finished and res.turns == 3


def test_a_dead_model_ends_the_phase_and_says_why(repo):
    res = run_phase(ws(repo), Script([ConnectionError("refused")]), "builder", "guide", "t", max_turns=3)
    assert not res.finished and "ConnectionError" in res.error


def test_unknown_tools_and_bad_arguments_are_answered_not_raised(repo):
    m = Script([call("shell", cmd="ls"), call("read_file", nope=1), {"content": DONE_WORD}])
    res = run_phase(ws(repo), m, "builder", "guide", "t", max_turns=5)
    assert res.finished and "no tool named 'shell'" in res.tool_calls[0]["result"]
    assert "bad arguments" in res.tool_calls[1]["result"]


# ---- mutants -----------------------------------------------------------------------------------------------------
def test_mutants_are_killed_survive_or_have_bad_anchors(repo):
    w = ws(repo)
    w.write_file("pkg/calc.py", "def add(a, b):\n    return a + b\n")
    res = run_mutants(w, [{"name": "minus", "file": "pkg/calc.py", "old": "a + b", "new": "a - b"},
                          {"name": "same", "file": "pkg/calc.py", "old": "a + b", "new": "b + a"},
                          {"name": "gone", "file": "pkg/calc.py", "old": "a * b", "new": "a"},
                          {"name": "syntax", "file": "pkg/calc.py", "old": "return a + b", "new": "return a +"}],
                      ["tests"])
    # a mutant that only breaks the import is not a kill: no test failed on behaviour
    assert [(r.name, r.status) for r in res] == [("minus", "killed"), ("same", "survived"), ("gone", "bad_anchor"),
                                                 ("syntax", "survived")]


def test_mutants_need_a_passing_baseline(repo):
    res = run_mutants(ws(repo), [{"name": "m", "file": "pkg/calc.py", "old": "a - b", "new": "a"}], ["tests"])
    assert res[0].name == "baseline" and res[0].status == "error"


def test_mutant_copies_carry_no_credential_files(repo, tmp_path):
    import shutil
    import builder.mutants as M
    dst = tmp_path / "copy"
    shutil.copytree(repo, dst, ignore=M._ignore, symlinks=False)          # the copy run_mutants makes
    names = {str(p.relative_to(dst)) for p in dst.rglob("*")}
    assert "pkg/calc.py" in names and not any(".envrc" in n or "discord_token" in n or n.startswith(".git")
                                              for n in names)


def test_run_mutants_copies_through_the_filter():
    import inspect
    import builder.mutants as M
    assert "ignore=_ignore" in inspect.getsource(M.run_mutants)


# ---- the whole packet --------------------------------------------------------------------------------------------
def packet(repo, **over):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    p = {"id": "demo", "repo": str(repo), "base_commit": head[:7], "interpreter": PY, "tests": ["tests"],
         "phases": [{"role": "implementer", "writable": ["pkg/calc.py"], "task": "fix add"}],
         "must_not_change": ["tests/test_calc.py"],
         "mutants": [{"name": "minus", "file": "pkg/calc.py", "old": "a + b", "new": "a - b"}],
         "commit_message": "Fix add", "max_turns": 5}
    p.update(over)
    return p


def test_a_green_packet_commits_locally_as_the_builder(repo):
    rep = rivet_build.build(packet(repo), Script([FIX, {"content": DONE_WORD}]), log=lambda *a: None)
    assert rep["committed"] and rep["not_committed_because"] == []
    author = subprocess.run(["git", "log", "-1", "--format=%an|%s"], cwd=repo, capture_output=True, text=True).stdout
    assert author.startswith("Rivet builder|Fix add")
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout == ""
    assert json.loads((repo.parent / "demo_report.json").read_text())["committed"] == rep["committed"]


def test_a_surviving_mutant_means_no_commit(repo):
    p = packet(repo, mutants=[{"name": "same", "file": "pkg/calc.py", "old": "a + b", "new": "b + a"}])
    rep = rivet_build.build(p, Script([FIX, {"content": DONE_WORD}]), log=lambda *a: None)
    assert rep["committed"] is None and "same=survived" in rep["not_committed_because"][0]


def test_failing_tests_mean_no_commit(repo):
    rep = rivet_build.build(packet(repo), Script([{"content": DONE_WORD}]), log=lambda *a: None)
    assert rep["committed"] is None and "nothing changed" in rep["not_committed_because"]
    assert "the final tests fail" in rep["not_committed_because"]


def test_an_unfinished_phase_means_no_commit(repo):
    rep = rivet_build.build(packet(repo), Script([FIX]), log=lambda *a: None)
    assert rep["committed"] is None and any("without the model saying" in r for r in rep["not_committed_because"])


def test_a_touched_protected_file_means_no_commit(repo):
    p = packet(repo, phases=[{"role": "implementer", "writable": ["pkg/calc.py", "tests/test_calc.py"], "task": "t"}])
    weaken = call("write_file", path="tests/test_calc.py", content="def test_add():\n    assert True\n")
    rep = rivet_build.build(p, Script([FIX, weaken, {"content": DONE_WORD}]), log=lambda *a: None)
    assert rep["committed"] is None and rep["touched_protected"] == ["tests/test_calc.py"]


def test_the_builder_starts_only_clean_and_at_the_base(repo):
    (repo / "pkg" / "calc.py").write_text("dirty\n")
    with pytest.raises(rivet_build.PacketError, match="uncommitted"):
        rivet_build.build(packet(repo), Script([]), log=lambda *a: None)
    git(repo, "checkout", "--", "pkg/calc.py")
    with pytest.raises(rivet_build.PacketError, match="not the packet's base"):
        rivet_build.build(packet(repo, base_commit="0000000"), Script([]), log=lambda *a: None)


def test_a_change_outside_the_allowlist_means_no_commit(repo, monkeypatch):
    # the workspace refuses such writes, so simulate a path that slipped past it (a tool's side effect)
    def side_effect_model(messages, tools, _n=[0]):
        _n[0] += 1
        if _n[0] == 1:
            (repo / "pkg" / "extra.py").write_text("x = 1\n")
            return {"role": "assistant", **FIX}
        return {"role": "assistant", "content": DONE_WORD}
    side_effect_model.model = "scripted"
    rep = rivet_build.build(packet(repo), side_effect_model, log=lambda *a: None)
    assert rep["committed"] is None and rep["outside_allowlist"] == ["pkg/extra.py"]
