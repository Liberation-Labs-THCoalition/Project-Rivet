"""Synthesis chip — the only place Rivet calls the model.

Everything upstream in the DAG is evidence gathering; everything
downstream is verification. The prompt is assembled from:

  - the BDI beliefs relevant to this intent (constraints first)
  - upstream artifacts (real source excerpts, migration/security reports)
  - Pharos-routed knowledge packs (KV-injected on the transformers
    backend, system-context on Ollama)
  - the conversation history for this session

The chip never sees raw context files — beliefs and artifacts are the
interface. If it isn't in the BDI or an artifact, Rivet doesn't claim it.
"""

import json

from kintsugi_core import (
    BaseSkillChip,
    BDIStore,
    EFEWeights,
    SkillCapability,
    SkillContext,
    SkillDomain,
    SkillRequest,
    SkillResponse,
)

RIVET_SYSTEM = """You are Rivet, a senior engineer embedded with the \
Multiverse Campus team.

## Non-negotiable rules
1. Never suggest a destructive migration (DROP, TRUNCATE, in-place type \
change, RENAME). Staging and prod share the database. If asked for one, \
give the additive multi-step alternative instead — and never print the \
destructive statement, not even as a "don't do this" example.
2. Every code suggestion states: what it changes, what it could break, \
and what tests verify it.
3. If you have not read the relevant source (check the SOURCE EVIDENCE \
section), say you are reasoning from architecture, not source.
4. Auth changes get an explicit callout: "This touches authentication. \
Review with security before merging."
5. Flag known audit findings proactively when the question walks into one.
6. You are a colleague, not the lead. Suggest, don't decree.
"""


def _format_beliefs(bdi: BDIStore, belief_ids: list) -> str:
    lines = []
    for bid in belief_ids:
        b = bdi.get_belief(bid)
        if b is not None:
            lines.append(f"- ({b.confidence:.2f}) {b.content}")
    return "\n".join(lines) if lines else "(none loaded)"


def _format_analysis(analysis: dict) -> str:
    if not analysis or not analysis.get("files"):
        notes = "; ".join(analysis.get("notes", [])) if analysis else ""
        return f"No source files were read for this request. {notes}".strip()
    parts = []
    for f in analysis["files"]:
        header = f"### {f['path']}"
        if f.get("git_status"):
            header += f"  (git: {f['git_status']} — uncommitted changes!)"
        parts.append(f"{header}\n```\n{f['excerpt']}\n```")
        if f.get("recent_history"):
            parts.append(f"Recent commits touching this file:\n{f['recent_history']}")
    return "\n\n".join(parts)


def _format_report(name: str, report: dict) -> str:
    if not report:
        return ""
    return f"## {name}\n```json\n{json.dumps(report, indent=2, default=str)[:4000]}\n```"


class SynthesisChip(BaseSkillChip):
    name = "synthesis"
    description = "Generate the draft answer from evidence + beliefs + packs"
    version = "2.0.0"
    domain = SkillDomain.GENERAL
    efe_weights = EFEWeights()
    capabilities = [SkillCapability.EXTERNAL_API]

    def __init__(self, model_client, bdi: BDIStore, pharos_router=None,
                 max_tokens: int = 1536):
        super().__init__()
        self.model = model_client
        self.bdi = bdi
        self.pharos = pharos_router
        self.max_tokens = max_tokens

    async def handle(self, request: SkillRequest,
                     context: SkillContext) -> SkillResponse:
        question = context.metadata.get("question", request.raw_input)
        session = context.metadata.get("session")
        belief_ids = context.metadata.get("belief_ids", [])

        # Pharos: route the question to knowledge packs.
        knowledge, pack_names = "", []
        if self.pharos is not None:
            routed = self.pharos.route(question)
            knowledge = routed.knowledge_text
            pack_names = routed.pack_names
            if session and pack_names:
                for p in pack_names:
                    session.record_evidence("pharos_pack", p, self.name)

        prompt_parts = ["# ORGANIZATIONAL BELIEFS (BDI)\n"
                        + _format_beliefs(self.bdi, belief_ids)]

        analysis = request.parameters.get("analysis") or {}
        prompt_parts.append("# SOURCE EVIDENCE\n" + _format_analysis(analysis))

        for key, title in (
            ("migration_report", "MIGRATION SAFETY REPORT"),
            ("security_report", "SECURITY REVIEW REPORT"),
        ):
            block = _format_report(title, request.parameters.get(key) or {})
            if block:
                prompt_parts.append(block)

        if session and session.history:
            recent = session.history[-6:]
            convo = "\n".join(f"{t['role']}: {t['content'][:400]}" for t in recent)
            prompt_parts.append(f"# CONVERSATION SO FAR\n{convo}")

        prompt_parts.append(f"# QUESTION\n{question}")
        prompt = "\n\n".join(prompt_parts)

        reply = self.model.generate(
            prompt, system=RIVET_SYSTEM, knowledge=knowledge,
            max_tokens=self.max_tokens,
        )
        if not reply.ok:
            return SkillResponse(
                content=f"model call failed: {reply.error}", success=False,
                data={"text": "", "error": reply.error},
            )
        return SkillResponse(
            content="draft generated", success=True,
            data={
                "text": reply.text,
                "backend": reply.backend,
                "knowledge_injected": reply.knowledge_injected,
                "packs_used": pack_names,
            },
        )
