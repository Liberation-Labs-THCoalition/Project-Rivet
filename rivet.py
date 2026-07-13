"""Rivet — Kintsugi-based code assistant for the Multiverse Campus.

Not a chat wrapper: every request becomes a BDI intention, compiles to a
SkillDAG, executes through evidence-gathering chips, and terminates in
the discipline gate. See ARCHITECTURE.md.

Usage:
    python rivet.py                          # serve on config port (8100)
    python rivet.py --port 8200
    python rivet.py --ask "..." [--user t]   # one-shot CLI
    python rivet.py --show-bdi               # dump seeded beliefs
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

V2_ROOT = Path(__file__).parent
sys.path.insert(0, str(V2_ROOT))

from kintsugi_core import (  # noqa: E402
    KINTSUGI_SOURCE,
    BDIStore,
    DAGExecutor,
    SkillContext,
    SkillRegistry,
)
from engine.beliefs import refresh_git_belief, seed_bdi  # noqa: E402
from engine.config import load_config  # noqa: E402
from engine.model_client import build_client  # noqa: E402
from engine.planner import Planner  # noqa: E402
from engine.session import SessionManager  # noqa: E402
from engine.synthesis import SynthesisChip  # noqa: E402
from pharos.pack_loader import load_router  # noqa: E402
from skills.code_analysis import CodeAnalysisChip  # noqa: E402
from skills.discipline_gate import DisciplineGateChip  # noqa: E402
from skills.migration_safety import MigrationSafetyChip  # noqa: E402
from skills.security_review import SecurityReviewChip  # noqa: E402
from skills.test_runner import TestRunnerChip  # noqa: E402
from tools.file_tools import RepoFiles  # noqa: E402
from tools.git_tools import GitTools  # noqa: E402
from tools.schema_tools import SchemaTools  # noqa: E402
from tools.test_tools import TestTools  # noqa: E402


class RivetAgent:
    def __init__(self, config_path: Path | str | None = None,
                 model_client=None):
        cfg_path = Path(config_path or V2_ROOT / "kintsugi_config.yaml")
        self.config = load_config(cfg_path)
        paths = self.config.get("paths", {}) or {}

        self.context_dir = (cfg_path.parent / paths.get(
            "context_dir", "../context")).resolve()

        # --- BDI ------------------------------------------------------
        org_id = self.config.get("org", {}).get("id", "multiverse_school")
        self.bdi = BDIStore(org_id)
        seed_bdi(self.bdi, self.config, self.context_dir)

        # --- tool harness ---------------------------------------------
        repo = paths.get("campus_repo") or ""
        self.repo_files = None
        self.git_tools = None
        if repo and Path(repo).is_dir():
            write_enabled = bool(
                (self.config.get("tools") or {}).get("repo_write_enabled"))
            self.repo_files = RepoFiles(repo_root=repo,
                                        write_enabled=write_enabled)
            self.git_tools = GitTools(repo_root=repo)
            refresh_git_belief(self.bdi, repo, self.context_dir)
        self.schema_tools = SchemaTools(
            self.context_dir,
            dsn=paths.get("campus_dsn") or "",
            migrations_dir=paths.get("migrations_dir") or "",
        )
        self.test_tools = TestTools(repo_root=repo)

        # --- model + pharos -------------------------------------------
        model_cfg = self.config.get("model", {}) or {}
        self.model = model_client or build_client(model_cfg)
        pharos_cfg = self.config.get("pharos", {}) or {}
        packs_dir = (cfg_path.parent / pharos_cfg.get(
            "packs_dir", "../packs")).resolve()
        self.pharos = load_router(
            packs_dir,
            match_threshold=float(pharos_cfg.get("match_threshold", 0.30)),
            source_threshold=float(pharos_cfg.get("source_threshold", 0.55)),
            max_packs=int(pharos_cfg.get("max_packs", 2)),
        )

        # --- skills ----------------------------------------------------
        self.registry = SkillRegistry()
        self.registry.register(CodeAnalysisChip(self.repo_files, self.git_tools))
        self.registry.register(MigrationSafetyChip(self.schema_tools))
        self.registry.register(SecurityReviewChip(self.repo_files))
        self.registry.register(SynthesisChip(
            self.model, self.bdi, self.pharos,
            max_tokens=int(model_cfg.get("max_answer_tokens", 1536)),
        ))
        self.registry.register(TestRunnerChip(self.test_tools))
        self.registry.register(DisciplineGateChip(self.bdi))

        # --- planning + execution + sessions ---------------------------
        self.planner = Planner(self.bdi, self.registry)
        self.executor = DAGExecutor(self.registry, max_parallel=4)
        sess_cfg = self.config.get("session", {}) or {}
        self.sessions = SessionManager(
            ttl_seconds=int(sess_cfg.get("ttl_seconds", 3600)),
            rate_limit_per_hour=int(sess_cfg.get("rate_limit_per_hour", 60)),
        )
        self.org_id = org_id

    # ------------------------------------------------------------------

    def ask(self, question: str, user: str = "anonymous") -> dict:
        t0 = time.time()
        question = (question or "").strip()
        if not question:
            return {"error": "empty question"}

        if not self.sessions.check_rate_limit(user):
            return {"error": "rate limit exceeded — try again later",
                    "user": user}
        session = self.sessions.get(user)

        plan = self.planner.form_plan(question, user)
        intention = self.bdi.get_intention(plan.intention_id)

        context = SkillContext(
            org_id=self.org_id,
            user_id=user,
            session_id=f"{user}:{int(session.created_at)}",
            metadata={
                "question": question,
                "session": session,
                "belief_ids": intention.belief_ids if intention else [],
            },
        )

        result = asyncio.run(self.executor.execute(
            plan.dag, context, initial_artifacts={"question": question},
        ))

        final = result.artifacts.get("final") or {}
        ok = result.success and bool(final.get("text"))
        self.planner.complete_plan(plan.intention_id, ok)

        if not final.get("text"):
            errors = result.node_errors or {"pipeline": "no final artifact"}
            return {
                "error": "plan execution failed",
                "node_errors": errors,
                "intent": plan.intent,
                "plan": plan.rationale,
                "elapsed_seconds": round(time.time() - t0, 1),
            }

        session.add_turn("user", question)
        session.add_turn("rivet", final["text"][:1000])

        return {
            "response": final["text"],
            "gate_passed": final.get("passed", False),
            "confidence": final.get("confidence", "LOW"),
            "flags": final.get("flags", []),
            "requires_review": final.get("requires_review", []),
            "beliefs_consulted": final.get("beliefs_consulted", []),
            "intent": plan.intent,
            "plan": plan.rationale,
            "packs_used": (result.artifacts.get("draft") or {}).get(
                "packs_used", []),
            "files_read": sorted(session.files_read),
            "user": user,
            "elapsed_seconds": round(time.time() - t0, 1),
        }

    def health(self) -> dict:
        return {
            "status": "ok",
            "agent": self.config.get("org", {}).get("agent_name", "Rivet"),
            "kintsugi_source": KINTSUGI_SOURCE,
            "model_backend": self.config.get("model", {}).get("backend"),
            "model": self.config.get("model", {}).get("name"),
            "skills": self.registry.list_names(),
            "beliefs": len(self.bdi.list_beliefs()),
            "packs": [p.name for p in self.pharos.library.packs],
            "repo_attached": self.repo_files is not None,
            "sessions": self.sessions.stats(),
        }

    def bdi_snapshot(self) -> dict:
        snap = self.bdi.get_snapshot()
        return {
            "org_id": snap.org_id,
            "beliefs": [
                {"id": b.id, "confidence": b.confidence,
                 "source": b.source, "tags": b.tags,
                 "content": b.content[:200]}
                for b in snap.beliefs
            ],
            "desires": [
                {"id": d.id, "priority": d.priority, "content": d.content}
                for d in snap.desires
            ],
            "intentions_active": len([
                i for i in snap.intentions if i.status.value == "active"
            ]),
            "intentions_total": len(snap.intentions),
        }


# ----------------------------------------------------------------------


def make_handler(agent: RivetAgent):
    model_name = (agent.config.get("model") or {}).get("name", "rivet")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
            except (json.JSONDecodeError, ValueError):
                return self._json({"error": "invalid JSON body"}, 400)

            if self.path == "/ask":
                result = agent.ask(body.get("question", ""),
                                   body.get("user", "anonymous"))
                self._json(result, 200 if "error" not in result else 422)
            elif self.path == "/v1/chat/completions":
                self._openai_chat(body)
            else:
                self._json({"error": f"unknown endpoint {self.path}"}, 404)

        def _openai_chat(self, body: dict):
            messages = body.get("messages", [])
            if not messages:
                return self._json({"error": {"message": "messages required",
                                             "type": "invalid_request_error"}}, 400)
            question = messages[-1].get("content", "")
            user = body.get("user", "ide")

            result = agent.ask(question, user)

            if "error" in result:
                return self._json({"error": {"message": result["error"],
                                             "type": "server_error"}}, 500)

            text = result.get("response", "")
            prompt_chars = sum(len(m.get("content", "")) for m in messages)
            self._json({
                "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model_name,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": prompt_chars // 4,
                    "completion_tokens": len(text) // 4,
                    "total_tokens": (prompt_chars + len(text)) // 4,
                },
            })

        def do_GET(self):
            if self.path == "/health":
                self._json(agent.health())
            elif self.path == "/bdi":
                self._json(agent.bdi_snapshot())
            elif self.path == "/v1/models":
                self._json({"object": "list", "data": [{
                    "id": model_name, "object": "model",
                    "owned_by": "rivet",
                }]})
            else:
                self._json({"endpoints": {
                    "POST /ask": '{"question": "...", "user": "..."}',
                    "POST /v1/chat/completions": "OpenAI-compatible chat API",
                    "GET /v1/models": "available models",
                    "GET /health": "status + loaded skills/packs/beliefs",
                    "GET /bdi": "current belief/desire/intention state",
                }})

        def _json(self, data, status=200):
            payload = json.dumps(data, indent=2, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt, *args):
            print(f"[rivet] {args[0] if args else ''}", flush=True)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Rivet code assistant (Kintsugi v2)")
    ap.add_argument("--config", default=str(V2_ROOT / "kintsugi_config.yaml"))
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--ask", help="one-shot question, print JSON and exit")
    ap.add_argument("--user", default="cli")
    ap.add_argument("--show-bdi", action="store_true")
    args = ap.parse_args()

    agent = RivetAgent(args.config)

    if args.show_bdi:
        print(json.dumps(agent.bdi_snapshot(), indent=2, default=str))
        return
    if args.ask:
        print(json.dumps(agent.ask(args.ask, args.user), indent=2, default=str))
        return

    port = args.port or int(
        (agent.config.get("server") or {}).get("port", 8100))
    health = agent.health()
    server = HTTPServer(("0.0.0.0", port), make_handler(agent))
    print(f"Rivet v2 listening on :{port}", flush=True)
    print(f"  kintsugi: {health['kintsugi_source']}", flush=True)
    print(f"  model:    {health['model_backend']}:{health['model']}", flush=True)
    print(f"  skills:   {', '.join(health['skills'])}", flush=True)
    print(f"  beliefs:  {health['beliefs']}  packs: {health['packs']}", flush=True)
    print(f"  repo:     {'attached' if health['repo_attached'] else 'not on this machine'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nRivet shutting down.", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
