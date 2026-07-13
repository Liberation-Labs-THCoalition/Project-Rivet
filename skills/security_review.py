"""Security review chip + audit findings as structured data.

AUDIT_FINDINGS holds confirmed findings from your codebase audit.
engine/beliefs.py seeds BDI beliefs from this list; the discipline gate
checks drafts against the same list. Replace the examples below with
your own findings.

See examples/multiverse-campus/ in the delivery repo for a full
deployment with 13 real findings.
"""

import re
from dataclasses import dataclass, field

from kintsugi_core import (
    BaseSkillChip,
    EFEWeights,
    SkillCapability,
    SkillContext,
    SkillDomain,
    SkillRequest,
    SkillResponse,
)


@dataclass(frozen=True)
class AuditFinding:
    id: str
    severity: str          # CRITICAL | HIGH | MEDIUM | LOW
    title: str
    area: str              # auth | webhook | transaction | realtime | schema | moderation | cost
    file_hint: str
    advice: str
    patterns: tuple = ()   # regexes that indicate the same mistake recurring


# Replace these with your own audit findings. Each finding becomes a BDI
# belief and a pattern the discipline gate checks drafts against.
# See examples/multiverse-campus/ for a full deployment with real findings.
AUDIT_FINDINGS = [
    AuditFinding(
        id="EX1", severity="CRITICAL", area="webhook",
        title="Example: webhook signature bypass when env var is unset",
        file_hint="",
        advice=("Never skip signature verification when a secret env var "
                "is missing — reject the request instead."),
        patterns=(r"if\s*\(\s*!\s*secret\s*\)\s*return\s+true",
                  r"WEBHOOK_SECRET\s*\|\|\s*['\"]['\"]"),
    ),
    AuditFinding(
        id="EX2", severity="HIGH", area="auth",
        title="Example: JWT_SECRET falls back to empty string",
        file_hint="",
        advice=("Startup must be fatal when JWT_SECRET is empty. "
                "Never sign or verify with a defaulted secret."),
        patterns=(r"JWT_SECRET\s*(\?\?|\|\|)\s*['\"]",),
    ),
    AuditFinding(
        id="EX3", severity="HIGH", area="transaction",
        title="Example: paired writes without transaction protection",
        file_hint="",
        advice=("Debit + credit or any paired DB writes must share one "
                "transaction. A crash between them loses data."),
        patterns=(),
    ),
]

# Recurring anti-patterns the audit told the team to grep for.
RECURRING_PATTERNS = [
    ("env_fallback_disables_security",
     r"(SECRET|_KEY|TOKEN)\w*\s*(\?\?|\|\|)\s*['\"]",
     "Env var fallback that silently degrades security (audit pattern 4)."),
    ("parseint_no_nan_guard",
     r"parseInt\(req\.params",
     "parseInt on route params without a NaN guard (audit pattern 3)."),
    ("socket_on_without_off",
     r"socket\.on\(",
     "Socket listener registration — confirm matching socket.off cleanup "
     "(audit pattern 2)."),
]

AUTH_SIGNALS = [
    r"\bJWT_SECRET\b", r"\bverifyToken\b", r"\brequireAdmin\b",
    r"\brequireModerator\b", r"\bauthMiddleware\b", r"\bsession\s*cookie\b",
    r"\brejectIfIneligible\b", r"\bBearer\b", r"\brefresh[_ ]?token\b",
    r"\bwebhook\b", r"\bsignature\b",
]


def findings_relevant_to(text: str) -> list:
    """Findings whose area keywords or patterns appear in the text."""
    hits = []
    lower = text.lower()
    for f in AUDIT_FINDINGS:
        matched = any(re.search(p, text, re.IGNORECASE) for p in f.patterns)
        area_hit = f.area in lower
        if matched or area_hit:
            hits.append({"id": f.id, "severity": f.severity,
                         "title": f.title, "advice": f.advice,
                         "pattern_matched": matched})
    return hits


def recurring_pattern_hits(text: str) -> list:
    hits = []
    for name, pattern, message in RECURRING_PATTERNS:
        if re.search(pattern, text):
            hits.append({"pattern": name, "message": message})
    return hits


def touches_auth(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in AUTH_SIGNALS)


class SecurityReviewChip(BaseSkillChip):
    """Reviews a request (and any code in it) against the audit's
    confirmed findings and the campus auth architecture. Optionally greps
    the live repo for the same patterns near files the request names."""

    name = "security_review"
    description = "Audit-findings and auth-impact review"
    version = "2.0.0"
    domain = SkillDomain.SECURITY
    efe_weights = EFEWeights(
        mission_alignment=0.15, stakeholder_benefit=0.30,
        resource_efficiency=0.10, transparency=0.30, equity=0.15,
    )
    capabilities = [SkillCapability.READ_DATA]

    def __init__(self, repo_files=None):
        super().__init__()
        self.repo_files = repo_files  # tools.file_tools.RepoFiles or None

    async def handle(self, request: SkillRequest,
                     context: SkillContext) -> SkillResponse:
        question = context.metadata.get("question", request.raw_input)
        session = context.metadata.get("session")

        auth = touches_auth(question)
        findings = findings_relevant_to(question)
        recurring = recurring_pattern_hits(question)

        repo_hits = []
        if self.repo_files is not None and auth:
            # Where does the repo actually enforce auth today?
            for probe in (r"verifyToken", r"requireAdmin"):
                repo_hits.extend(self.repo_files.search(probe)[:5])

        if session:
            for f in findings:
                session.record_evidence("audit_finding", f["id"], self.name)

        report = {
            "auth_touched": auth,
            "relevant_findings": findings,
            "recurring_pattern_hits": recurring,
            "repo_auth_sites": repo_hits,
        }
        summary = (
            f"auth_touched={auth}; {len(findings)} audit finding(s) relevant; "
            f"{len(recurring)} recurring pattern hit(s)"
        )
        # NOTE: data IS the artifact — DAGExecutor assigns the whole data
        # dict to this node's single output key ("security_report").
        return SkillResponse(
            content=summary, success=True,
            data=report,
            requires_consensus=auth,
            consensus_action="auth_change_review" if auth else None,
        )
