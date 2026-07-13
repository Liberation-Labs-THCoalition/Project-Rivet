"""Rivet's planner — from a request to a BDI intention to a SkillDAG.

This is the piece that makes Rivet an agent rather than a chat endpoint.
Every request is classified, turned into an explicit intention (recorded
in the BDI store with the beliefs it rests on), and compiled into a
Kintsugi SkillDAG whose structure enforces the discipline:

  - migration questions CANNOT reach the model without a schema/safety
    pass first — the DAG edge makes it structural, not aspirational
  - auth questions always carry a security review artifact
  - every plan terminates in the discipline gate; there is no path from
    the model's draft to the user that bypasses it

Intent classification is deterministic keyword scoring. A misclassified
intent degrades to a *stricter* plan, never a looser one: the gate runs
its full check suite regardless of which plan produced the draft.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from kintsugi_core import (
    BDIIntention,
    BDIStore,
    DAGBuilder,
    IntentionStatus,
    SkillDAG,
    SkillRegistry,
)


@dataclass
class Plan:
    intent: str
    intention_id: str
    dag: SkillDAG
    rationale: str


INTENT_KEYWORDS = {
    "migration": [
        "migration", "migrate", "alter table", "add column", "drop",
        "schema", "create table", "constraint", "index", "pg-boss",
        "postgres", "database change", "column", "table", "retire",
        "deprecate",
    ],
    "auth": [
        "auth", "jwt", "token", "login", "session cookie", "middleware",
        "permission", "admin", "moderator", "webhook", "signature",
        "verifytoken", "bearer",
    ],
    "codegen": [
        "write", "implement", "add a", "create a", "generate", "fix",
        "refactor", "build", "endpoint", "handler", "component",
    ],
    "review": [
        "review", "check this", "audit", "look at", "is this safe",
        "what's wrong", "debug", "why does", "why is",
    ],
}

# Plan templates in DAGBuilder.from_intention() step format.
# Layers: 0 = evidence gathering (parallel), 1 = synthesis,
#         2 = verification, 3 = gate. Every template ends at the gate.
PLAN_TEMPLATES = {
    "migration": [
        {"id": "analyze", "skill": "code_analysis", "layer": 0,
         "inputs": ["question"], "outputs": ["analysis"]},
        {"id": "migration_check", "skill": "migration_safety", "layer": 0,
         "inputs": ["question"], "outputs": ["migration_report"]},
        {"id": "synthesize", "skill": "synthesis", "layer": 1,
         "inputs": ["question", "analysis", "migration_report"],
         "outputs": ["draft"],
         "depends_on": ["analyze", "migration_check"]},
        {"id": "gate", "skill": "discipline_gate", "layer": 2,
         "inputs": ["question", "draft", "analysis", "migration_report"],
         "outputs": ["final"], "depends_on": ["synthesize"]},
    ],
    "auth": [
        {"id": "analyze", "skill": "code_analysis", "layer": 0,
         "inputs": ["question"], "outputs": ["analysis"]},
        {"id": "security", "skill": "security_review", "layer": 0,
         "inputs": ["question"], "outputs": ["security_report"]},
        {"id": "synthesize", "skill": "synthesis", "layer": 1,
         "inputs": ["question", "analysis", "security_report"],
         "outputs": ["draft"], "depends_on": ["analyze", "security"]},
        {"id": "gate", "skill": "discipline_gate", "layer": 2,
         "inputs": ["question", "draft", "analysis", "security_report"],
         "outputs": ["final"], "depends_on": ["synthesize"]},
    ],
    "codegen": [
        {"id": "analyze", "skill": "code_analysis", "layer": 0,
         "inputs": ["question"], "outputs": ["analysis"]},
        {"id": "security", "skill": "security_review", "layer": 0,
         "inputs": ["question"], "outputs": ["security_report"]},
        {"id": "synthesize", "skill": "synthesis", "layer": 1,
         "inputs": ["question", "analysis", "security_report"],
         "outputs": ["draft"], "depends_on": ["analyze", "security"]},
        {"id": "verify", "skill": "test_runner", "layer": 2,
         "inputs": ["question", "draft"], "outputs": ["verification"],
         "depends_on": ["synthesize"]},
        {"id": "gate", "skill": "discipline_gate", "layer": 3,
         "inputs": ["question", "draft", "analysis", "security_report",
                    "verification"],
         "outputs": ["final"], "depends_on": ["verify"]},
    ],
    "review": [
        {"id": "analyze", "skill": "code_analysis", "layer": 0,
         "inputs": ["question"], "outputs": ["analysis"]},
        {"id": "security", "skill": "security_review", "layer": 0,
         "inputs": ["question"], "outputs": ["security_report"]},
        {"id": "migration_check", "skill": "migration_safety", "layer": 0,
         "inputs": ["question"], "outputs": ["migration_report"]},
        {"id": "synthesize", "skill": "synthesis", "layer": 1,
         "inputs": ["question", "analysis", "security_report",
                    "migration_report"],
         "outputs": ["draft"],
         "depends_on": ["analyze", "security", "migration_check"]},
        {"id": "gate", "skill": "discipline_gate", "layer": 2,
         "inputs": ["question", "draft", "analysis", "security_report",
                    "migration_report"],
         "outputs": ["final"], "depends_on": ["synthesize"]},
    ],
    "question": [
        {"id": "synthesize", "skill": "synthesis", "layer": 0,
         "inputs": ["question"], "outputs": ["draft"]},
        {"id": "gate", "skill": "discipline_gate", "layer": 1,
         "inputs": ["question", "draft"], "outputs": ["final"],
         "depends_on": ["synthesize"]},
    ],
}


def classify_intent(question: str) -> str:
    """Deterministic keyword scoring. Ties break toward the stricter plan
    (migration > auth > review > codegen > question)."""
    q = question.lower()
    scores = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        scores[intent] = sum(1 for kw in keywords if kw in q)
    # SQL in the question forces the migration plan regardless of phrasing
    if re.search(r"\b(alter|create|drop|truncate)\s+table\b", q):
        scores["migration"] = scores.get("migration", 0) + 10

    strictness = ["migration", "auth", "review", "codegen"]
    best, best_score = "question", 0
    for intent in strictness:
        if scores.get(intent, 0) > best_score:
            best, best_score = intent, scores[intent]
    return best


class Planner:
    def __init__(self, bdi: BDIStore, registry: SkillRegistry):
        self.bdi = bdi
        self.registry = registry
        self._counter = 0

    def form_plan(self, question: str, user_id: str) -> Plan:
        intent = classify_intent(question)
        steps = PLAN_TEMPLATES[intent]

        dag = DAGBuilder.from_intention(
            {"goal": question, "steps": steps},
            self.registry,
            strategy="quality",
        )
        errors = dag.validate(self.registry)
        if errors:
            raise RuntimeError(f"Plan for intent '{intent}' is invalid: {errors}")

        # Record the intention in the BDI store, linked to the beliefs
        # this plan rests on.
        self._counter += 1
        intention_id = f"intention_{user_id}_{self._counter}"
        relevant_beliefs = self._relevant_beliefs(intent)
        self.bdi.add_intention(BDIIntention(
            id=intention_id,
            goal=f"[{intent}] {question[:200]}",
            status=IntentionStatus.ACTIVE,
            belief_ids=relevant_beliefs,
            desire_ids=[d.id for d in self.bdi.list_desires()],
            created_at=datetime.now(timezone.utc),
        ))

        skills = " -> ".join(
            f"L{s['layer']}:{s['skill']}" for s in steps
        )
        return Plan(
            intent=intent,
            intention_id=intention_id,
            dag=dag,
            rationale=f"intent={intent}; plan: {skills}",
        )

    def complete_plan(self, intention_id: str, success: bool) -> None:
        status = IntentionStatus.COMPLETED if success else IntentionStatus.FAILED
        try:
            self.bdi.update_intention(
                intention_id, status=status,
                progress=1.0 if success else 0.0,
            )
        except KeyError:
            pass

    def _relevant_beliefs(self, intent: str) -> list:
        tag_map = {
            "migration": {"constraint", "schema", "audit"},
            "auth": {"constraint", "audit", "auth_flow", "architecture"},
            "codegen": {"architecture", "audit"},
            "review": {"constraint", "audit", "architecture"},
            "question": {"architecture"},
        }
        wanted = tag_map.get(intent, set())
        return [
            b.id for b in self.bdi.list_beliefs()
            if wanted & set(b.tags)
        ]
