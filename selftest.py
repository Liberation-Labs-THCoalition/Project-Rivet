"""Rivet v2 self-test — the full pipeline, offline, no model required.

Covers: config parsing (fallback parser specifically), BDI seeding,
intent classification, plan construction + DAG validation, Pharos
routing (keyword fallback), the tool guard, and two end-to-end runs
through the real DAG executor with a fake model:

  1. a destructive-SQL draft  -> gate BLOCKS
  2. a clean draft            -> gate passes, confidence graded

Run:  python selftest.py
Exit: 0 all passed, 1 otherwise.
"""

import json
import sys
import tempfile
from pathlib import Path

V2_ROOT = Path(__file__).parent
sys.path.insert(0, str(V2_ROOT))

PASS, FAIL = 0, 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


class FakeModel:
    """ModelClient stand-in with scripted replies."""

    def __init__(self, reply_text: str):
        self.reply_text = reply_text
        self.calls = []

    def generate(self, prompt, system="", knowledge="", max_tokens=1024):
        from engine.model_client import ModelReply
        self.calls.append({"prompt": prompt, "system": system,
                           "knowledge": knowledge})
        return ModelReply(
            text=self.reply_text, ok=True, backend="fake",
            knowledge_injected="system_prompt" if knowledge else "none",
        )


def main() -> int:
    print("== config ==")
    from engine.config import _parse_subset, load_config
    cfg_text = (V2_ROOT / "kintsugi_config.yaml").read_text()
    cfg_fallback = _parse_subset(cfg_text)
    check("fallback parser: model section",
          cfg_fallback.get("model", {}).get("name") == "qwen2.5-coder:32b",
          str(cfg_fallback.get("model")))
    check("fallback parser: constraints list",
          len(cfg_fallback.get("beliefs", {}).get("constraints", [])) == 2)
    check("fallback parser: nested scalar types",
          cfg_fallback["session"]["ttl_seconds"] == 3600
          and cfg_fallback["tools"]["repo_write_enabled"] is False)
    cfg = load_config(V2_ROOT / "kintsugi_config.yaml")
    check("load_config parses", isinstance(cfg, dict) and "org" in cfg)

    print("== kintsugi core ==")
    from kintsugi_core import KINTSUGI_SOURCE, BDIStore
    print(f"  (kintsugi source: {KINTSUGI_SOURCE})")
    check("kintsugi imports resolve", True)

    print("== BDI seeding ==")
    from engine.beliefs import seed_bdi
    context_dir = (V2_ROOT / ".." / "context").resolve()
    bdi = BDIStore("selftest_org")
    seed_bdi(bdi, cfg, context_dir)
    beliefs = bdi.list_beliefs()
    gate = bdi.get_belief("belief_constraint_gate_is_loadbearing")
    check("beliefs seeded", len(beliefs) >= 5, f"got {len(beliefs)}")
    check("gate constraint at confidence 1.0",
          gate is not None and gate.confidence == 1.0)
    check("audit findings became beliefs",
          bdi.get_belief("belief_audit_ex1") is not None)
    check("desires seeded", len(bdi.list_desires()) == 4)

    print("== tool guard ==")
    from tools.guard import check_command
    check("guard blocks unlisted binary",
          check_command(["curl", "http://x"]) != "")
    check("guard blocks suspicious pattern",
          check_command(["git", "log", ";rm -rf /"]) != "")
    check("guard allows clean git", check_command(["git", "log"]) == "")

    print("== migration classification ==")
    from skills.migration_safety import classify_sql, extract_sql
    destructive = classify_sql("ALTER TABLE students DROP COLUMN score;")
    additive = classify_sql(
        "ALTER TABLE students ADD COLUMN score int DEFAULT 0;")
    check("DROP COLUMN classified destructive", destructive["destructive"])
    check("ADD COLUMN classified additive",
          additive["additive"] and not additive["destructive"])
    prose = extract_sql("You should never drop a table in prod.")
    fenced = extract_sql("```sql\nDROP TABLE users;\n```")
    check("prose SQL not extracted", prose == [])
    check("fenced SQL extracted", len(fenced) == 1)

    print("== intent classification ==")
    from engine.planner import classify_intent
    check("migration intent",
          classify_intent("Add a migration for a new lessons column") == "migration")
    check("auth intent",
          classify_intent("Why does verifyToken reject the Bearer token?") == "auth")
    check("question intent",
          classify_intent("What does the campus use for state?") == "question")

    print("== pharos routing (keyword fallback) ==")
    with tempfile.TemporaryDirectory() as tmp:
        import subprocess
        result = subprocess.run(
            [sys.executable, str(V2_ROOT / "pharos" / "build_campus_pack.py"),
             "--context-dir", str(context_dir), "--out", tmp],
            capture_output=True, text=True,
        )
        check("campus pack builds", result.returncode == 0, result.stderr[:300])
        from pharos.pack_loader import PackLibrary, PharosRouter
        library = PackLibrary(tmp)
        check("campus pack loads",
              len(library.packs) == 1
              and len(library.packs[0].triples) >= 40,
              f"packs={[p.name for p in library.packs]}")
        router = PharosRouter(library)
        # force keyword path even if sentence-transformers is installed —
        # the fallback must work on a bare box
        router._st_model = None
        routed = router.route(
            "How do I write a safe migration for the shared database?")
        check("keyword routing finds campus pack",
              "campus_architecture" in routed.pack_names,
              routed.explanation)

    print("== end-to-end: destructive draft is BLOCKED ==")
    import rivet as rivet_mod
    bad_model = FakeModel(
        "Just drop the old column:\n```sql\nALTER TABLE students DROP "
        "COLUMN legacy_score;\n```\nThat cleans it up."
    )
    agent = rivet_mod.RivetAgent(model_client=bad_model)
    out = agent.ask("Write a migration to remove legacy_score from students",
                    user="selftest")
    check("plan used migration intent", out.get("intent") == "migration",
          json.dumps(out)[:300])
    check("gate blocked destructive SQL", out.get("gate_passed") is False,
          json.dumps(out.get("flags", []))[:300])
    check("blocked answer withholds the SQL",
          "DROP COLUMN" not in out.get("response", "").upper()
          or "BLOCKED" in out.get("response", ""))
    check("gate flags include destructive pattern",
          any("destructive" in f.get("message", "").lower() for f in out.get("flags", [])))

    print("== end-to-end: clean draft passes ==")
    good_model = FakeModel(
        "Add the column additively:\n```sql\nALTER TABLE students ADD "
        "COLUMN score integer DEFAULT 0;\n```\nWhat it changes: one new "
        "nullable-with-default column. What could break: nothing — old "
        "code ignores it. Tests: extend the students model test."
    )
    agent2 = rivet_mod.RivetAgent(model_client=good_model)
    out2 = agent2.ask("Add a migration for a students score column",
                      user="selftest2")
    check("gate passed clean answer", out2.get("gate_passed") is True,
          json.dumps(out2.get("flags", []))[:400])
    check("confidence graded (MEDIUM without source reads)",
          out2.get("confidence") == "MEDIUM", out2.get("confidence"))
    check("gate annotations attached",
          "Discipline Gate" in out2.get("response", "")
          or "Confidence" in out2.get("response", ""))
    check("synthesis saw beliefs in prompt",
          "ORGANIZATIONAL BELIEFS" in good_model.calls[0]["prompt"]
          and "share one PostgreSQL" in good_model.calls[0]["prompt"])

    print("== end-to-end: auth question flags review ==")
    auth_model = FakeModel(
        "The middleware order is verifyToken then rejectIfIneligible. "
        "To add a route guard use requireAdmin."
    )
    agent3 = rivet_mod.RivetAgent(model_client=auth_model)
    out3 = agent3.ask("How should I change the JWT auth middleware to add "
                      "a new admin route?", user="selftest3")
    check("auth intent planned", out3.get("intent") == "auth", out3.get("intent"))
    check("security review required",
          "security" in out3.get("requires_review", []),
          json.dumps(out3)[:300])

    print("== OpenAI-compatible endpoint ==")
    import io
    from http.server import BaseHTTPRequestHandler
    from unittest.mock import MagicMock

    oai_model = FakeModel("Zustand is the state management library used across 49 stores.")
    oai_agent = rivet_mod.RivetAgent(model_client=oai_model)
    HandlerClass = rivet_mod.make_handler(oai_agent)

    oai_body = json.dumps({
        "model": "rivet",
        "messages": [{"role": "user", "content": "What state library does campus use?"}],
    }).encode()

    mock_rfile = io.BytesIO(oai_body)
    mock_wfile = io.BytesIO()
    mock_request = MagicMock()
    handler = HandlerClass.__new__(HandlerClass)
    handler.rfile = mock_rfile
    handler.wfile = mock_wfile
    handler.path = "/v1/chat/completions"
    handler.headers = {"Content-Length": str(len(oai_body))}
    handler.requestline = "POST /v1/chat/completions HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.command = "POST"
    handler.client_address = ("127.0.0.1", 0)
    handler._headers_buffer = []
    handler.responses = BaseHTTPRequestHandler.responses
    handler.do_POST()

    raw = mock_wfile.getvalue().decode()
    body_start = raw.find("\r\n\r\n")
    resp = json.loads(raw[body_start + 4:]) if body_start >= 0 else {}
    check("OpenAI response has id",
          resp.get("id", "").startswith("chatcmpl-"), str(resp.get("id")))
    check("OpenAI response has choices",
          len(resp.get("choices", [])) == 1)
    check("OpenAI response content from pipeline",
          "Zustand" in resp.get("choices", [{}])[0].get("message", {}).get("content", ""))
    check("OpenAI response has usage",
          "total_tokens" in resp.get("usage", {}))
    check("OpenAI object type correct",
          resp.get("object") == "chat.completion")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
