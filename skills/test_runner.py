"""Test runner chip — verify the draft instead of asserting it works.

Pulls fenced TS/JS blocks out of the model's draft and syntax-checks
them with tsc; when the campus repo is present, can also run a targeted
workspace typecheck. The result is a verification artifact the
discipline gate uses: verified code can earn HIGH confidence, unverified
code is explicitly labeled as such.
"""

from kintsugi_core import (
    BaseSkillChip,
    EFEWeights,
    SkillCapability,
    SkillContext,
    SkillDomain,
    SkillRequest,
    SkillResponse,
)
from tools.test_tools import TestTools, extract_code_blocks

MAX_BLOCKS = 3


class TestRunnerChip(BaseSkillChip):
    name = "test_runner"
    description = "Syntax-check and test generated code"
    version = "2.0.0"
    domain = SkillDomain.OPERATIONS
    efe_weights = EFEWeights(
        mission_alignment=0.15, stakeholder_benefit=0.25,
        resource_efficiency=0.20, transparency=0.30, equity=0.10,
    )
    capabilities = [SkillCapability.EXECUTE_SHELL]

    def __init__(self, test_tools: TestTools | None = None):
        super().__init__()
        self.test_tools = test_tools or TestTools()

    async def handle(self, request: SkillRequest,
                     context: SkillContext) -> SkillResponse:
        session = context.metadata.get("session")
        draft_artifact = request.parameters.get("draft") or {}
        draft_text = (
            draft_artifact.get("text", "")
            if isinstance(draft_artifact, dict) else str(draft_artifact)
        )

        blocks = [
            b for b in extract_code_blocks(draft_text)
            if b["lang"] in ("ts", "tsx", "typescript", "js", "javascript", "")
            and b["code"].strip()
        ][:MAX_BLOCKS]

        checks = []
        for block in blocks:
            lang = "ts" if block["lang"] in ("", "ts", "tsx", "typescript") else "js"
            result = self.test_tools.syntax_check_snippet(block["code"], lang)
            checks.append({
                "lang": lang,
                "available": result.available,
                "passed": result.passed,
                "command": result.command,
                "output": result.output[:1500],
                "snippet_head": block["code"][:120],
            })
            if session and result.available:
                session.record_evidence(
                    "verification",
                    f"{result.command}: {'pass' if result.passed else 'FAIL'}",
                    self.name,
                )

        ran = [c for c in checks if c["available"]]
        failed = [c for c in ran if not c["passed"]]
        report = {
            "code_blocks_found": len(blocks),
            "checks_run": len(ran),
            "checks_failed": len(failed),
            "tooling_available": bool(ran) or not blocks,
            "checks": checks,
        }
        summary = (
            f"{len(blocks)} code block(s); {len(ran)} checked, "
            f"{len(failed)} failed"
            if blocks else "no code blocks in draft — nothing to verify"
        )
        return SkillResponse(content=summary, success=True, data=report)
