"""Discipline gate — a Kintsugi skill, not a regex filter.

Last node of every plan. Nothing reaches the user without passing here.

Three layers, in order of authority:

  1. BELIEF CHECKS — the gate holds beliefs (from the BDI store) about
     the shared-db constraint, the auth architecture, and the audit
     findings. The draft is checked against each. A belief with
     confidence 1.0 (operator constraint) produces a BLOCK on violation;
     lower-confidence beliefs produce WARNs citing their source.
  2. PATTERN CHECKS — the code-level regex layer (destructive SQL,
     signature bypass shapes, transaction gaps). Code, so it cannot be
     argued with.
  3. CONFIDENCE GRADING — the gate grades how much of the answer is
     actually evidenced: source files read this session → HIGH; packs/
     architecture map only → MEDIUM; neither → LOW. Failed verification
     caps at LOW; no verification of generated code caps at MEDIUM.

The gate's output is the final artifact: the (possibly blocked) answer
plus machine-readable flags for the API response.
"""

import re
from dataclasses import dataclass, field
from enum import Enum

from kintsugi_core import (
    BaseSkillChip,
    BDIStore,
    EFEWeights,
    SkillContext,
    SkillDomain,
    SkillRequest,
    SkillResponse,
)
from skills.migration_safety import classify_sql, extract_sql, scan_destructive_anywhere
from skills.security_review import (
    findings_relevant_to,
    recurring_pattern_hits,
    touches_auth,
)


class Confidence(str, Enum):
    HIGH = "HIGH"      # claims backed by source read this session, verified
    MEDIUM = "MEDIUM"  # backed by architecture map / audit / packs
    LOW = "LOW"        # reasoning from architecture alone, or verification failed


class Severity(str, Enum):
    BLOCK = "BLOCK"
    WARN = "WARN"
    INFO = "INFO"


@dataclass
class GateVerdict:
    passed: bool = True
    confidence: Confidence = Confidence.LOW
    flags: list = field(default_factory=list)
    requires_review: list = field(default_factory=list)
    beliefs_consulted: list = field(default_factory=list)

    def add(self, severity: Severity, message: str, belief_id: str = ""):
        self.flags.append({
            "severity": severity.value, "message": message,
            "belief": belief_id,
        })
        if severity == Severity.BLOCK:
            self.passed = False

    def format_annotations(self) -> str:
        lines = []
        if self.flags:
            lines.append("**Discipline Gate:**")
            icons = {"BLOCK": "[BLOCK]", "WARN": "[WARN]", "INFO": "[info]"}
            for f in self.flags:
                src = f" (belief: {f['belief']})" if f["belief"] else ""
                lines.append(f"- {icons[f['severity']]} {f['message']}{src}")
        if self.requires_review:
            lines.append(f"- Review required from: "
                         f"{', '.join(sorted(set(self.requires_review)))}")
        lines.append(f"- Confidence: **{self.confidence.value}**")
        return "\n".join(lines)


class DisciplineGateChip(BaseSkillChip):
    name = "discipline_gate"
    description = "Belief-checked final gate with earned confidence"
    version = "2.0.0"
    domain = SkillDomain.SECURITY
    efe_weights = EFEWeights(
        mission_alignment=0.20, stakeholder_benefit=0.30,
        resource_efficiency=0.05, transparency=0.30, equity=0.15,
    )
    consensus_actions = ["release_blocked_answer"]

    def __init__(self, bdi: BDIStore):
        super().__init__()
        self.bdi = bdi

    async def handle(self, request: SkillRequest,
                     context: SkillContext) -> SkillResponse:
        question = context.metadata.get("question", request.raw_input)
        session = context.metadata.get("session")

        draft_artifact = request.parameters.get("draft") or {}
        draft = draft_artifact.get("text", "") if isinstance(draft_artifact, dict) else str(draft_artifact)
        analysis = request.parameters.get("analysis") or {}
        migration_report = request.parameters.get("migration_report") or {}
        security_report = request.parameters.get("security_report") or {}
        verification = request.parameters.get("verification") or {}

        verdict = GateVerdict()

        if not draft:
            verdict.add(Severity.BLOCK, "synthesis produced no draft")
            return self._respond(verdict, draft, question)

        self._check_shared_db(verdict, draft)
        self._check_auth(verdict, draft, question, security_report)
        self._check_audit_patterns(verdict, draft)
        self._check_insecure_code(verdict, draft)
        self._grade_confidence(
            verdict, draft, session, analysis, verification, draft_artifact,
        )

        return self._respond(verdict, draft, question)

    # ------------------------------------------------------------------
    # Layer 1+2: belief-backed checks (patterns cited to beliefs)
    # ------------------------------------------------------------------

    def _check_shared_db(self, verdict: GateVerdict, draft: str) -> None:
        belief = self.bdi.get_belief("belief_constraint_shared_db")
        belief_id = belief.id if belief else ""
        if belief:
            verdict.beliefs_consulted.append(belief.id)

        for sql in extract_sql(draft):
            klass = classify_sql(sql)
            if klass["destructive"]:
                severity = (Severity.BLOCK
                            if belief and belief.confidence >= 1.0
                            else Severity.WARN)
                verdict.add(
                    severity,
                    f"Draft contains destructive SQL "
                    f"({', '.join(klass['violations'])}). "
                    f"{belief.content if belief else 'Shared-db rule.'}",
                    belief_id,
                )

        if not any(f["severity"] == "BLOCK" for f in verdict.flags):
            prose_hits = scan_destructive_anywhere(draft)
            if prose_hits:
                labels = sorted(set(label for _, label in prose_hits))
                verdict.add(
                    Severity.BLOCK,
                    f"Draft mentions destructive SQL outside code blocks "
                    f"({', '.join(labels)}). Even in prose or inline code, "
                    f"destructive SQL gets copy-pasted. Rephrase without "
                    f"the destructive statement.",
                    belief_id,
                )

    def _check_auth(self, verdict: GateVerdict, draft: str, question: str,
                    security_report: dict) -> None:
        auth_hit = (touches_auth(draft) or touches_auth(question)
                    or security_report.get("auth_touched", False))
        if not auth_hit:
            return
        belief = self.bdi.get_belief("belief_arch_auth_flow")
        if belief:
            verdict.beliefs_consulted.append(belief.id)
        verdict.add(
            Severity.WARN,
            "This touches authentication. Review with security before "
            "merging.",
            belief.id if belief else "",
        )
        verdict.requires_review.append("security")

    def _check_audit_patterns(self, verdict: GateVerdict, draft: str) -> None:
        for hit in findings_relevant_to(draft):
            if not hit["pattern_matched"]:
                continue
            belief_id = f"belief_audit_{hit['id'].lower()}"
            belief = self.bdi.get_belief(belief_id)
            if belief:
                verdict.beliefs_consulted.append(belief_id)
            severity = (Severity.WARN
                        if hit["severity"] in ("CRITICAL", "HIGH")
                        else Severity.INFO)
            verdict.add(
                severity,
                f"Draft walks into audit finding {hit['id']} "
                f"({hit['title']}). {hit['advice']}",
                belief_id,
            )
        for hit in recurring_pattern_hits(draft):
            verdict.add(Severity.INFO, hit["message"])

    _INSECURE_PATTERNS = [
        (r"""\$\{[^}]*(?:req|params|query|body|input|user)[^}]*\}""",
         "string interpolation with user input (SQL/NoSQL injection risk — CWE-89)"),
        (r"""\beval\s*\([^)]*(?:req|params|query|body|input|user)""",
         "eval() with user-controlled input (code injection — CWE-94)"),
        (r"""['"`]\s*\+\s*(?:req|params|query|body|input|user)""",
         "string concatenation with user input in query context (CWE-89)"),
        (r"""\bshell\s*[=:]\s*True""",
         "subprocess with shell=True (OS command injection — CWE-78)"),
        (r"""\bchild_process\.exec\s*\(""",
         "child_process.exec (OS command injection — CWE-78). Use execFile or spawn instead"),
        (r"""\bos\.system\s*\(""",
         "os.system() (OS command injection — CWE-78). Use subprocess with shell=False"),
        (r"""\bopen\s*\([^)]*(?:filename|filepath|file_path|path|fname)\b[^)]*\)(?!.*(?:sanitize|validate|whitelist|allowlist|os\.path\.basename|realpath))""",
         "file open with unsanitized path (path traversal — CWE-22). Validate against a base directory"),
        (r"""\bpickle\.loads?\s*\(""",
         "pickle.load on untrusted data (deserialization — CWE-502). Use JSON or a safe format"),
        (r"""\byaml\.load\s*\([^)]*\)(?!.*Loader)""",
         "yaml.load without SafeLoader (code execution — CWE-502). Use yaml.safe_load"),
        (r"""\bMath\.random\s*\(\s*\).*(?:token|secret|key|password|auth|session|nonce|salt|iv)""",
         "Math.random() for security-sensitive value (weak PRNG — CWE-338). Use crypto.randomBytes"),
        (r"""(?:md5|sha1)\s*\(.*(?:password|secret|credential)""",
         "weak hash for credentials (CWE-328). Use bcrypt, scrypt, or argon2"),
    ]

    def _check_insecure_code(self, verdict: GateVerdict, draft: str) -> None:
        fenced = re.findall(r"```(?:\w*)\n(.*?)```", draft, re.DOTALL)
        if not fenced:
            return
        code = "\n".join(fenced)
        for pattern, label in self._INSECURE_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                verdict.add(
                    Severity.WARN,
                    f"Code contains {label}. Use safe alternatives.",
                )

    # ------------------------------------------------------------------
    # Layer 3: earned confidence
    # ------------------------------------------------------------------

    def _grade_confidence(self, verdict: GateVerdict, draft: str, session,
                          analysis: dict, verification: dict,
                          draft_artifact: dict) -> None:
        files_read = set(session.files_read) if session else set()
        files_cited = set(re.findall(
            r"\b((?:server|client|shared|design-system)/[\w./-]+\.\w+)\b",
            draft,
        ))

        has_source = bool(files_read)
        cites_unread = files_cited - files_read
        has_knowledge = bool(
            draft_artifact.get("packs_used")
            or analysis.get("repo_available") is False
            or self.bdi.list_beliefs()
        )

        checks = verification.get("checks", []) if verification else []
        ran = [c for c in checks if c.get("available")]
        failed = [c for c in ran if not c.get("passed")]
        has_code = bool(re.search(r"```\w*\n.{10,}?```", draft, re.DOTALL))

        if failed:
            confidence = Confidence.LOW
            verdict.add(
                Severity.WARN,
                f"Generated code FAILED verification "
                f"({len(failed)}/{len(ran)} checks): "
                f"{failed[0]['output'][:200]}",
            )
        elif has_source and (not has_code or ran):
            confidence = Confidence.HIGH
        elif has_source or has_knowledge:
            confidence = Confidence.MEDIUM
            if has_code and not ran:
                verdict.add(
                    Severity.INFO,
                    "Code in this answer was not verified (tsc/tests "
                    "unavailable here) — treat as unchecked.",
                )
        else:
            confidence = Confidence.LOW

        if cites_unread:
            if confidence == Confidence.HIGH:
                confidence = Confidence.MEDIUM
            verdict.add(
                Severity.INFO,
                f"Answer references files not read this session: "
                f"{', '.join(sorted(cites_unread)[:5])} — reasoning from "
                f"architecture for those.",
            )

        verdict.confidence = confidence

    # ------------------------------------------------------------------

    def _respond(self, verdict: GateVerdict, draft: str,
                 question: str) -> SkillResponse:
        annotations = verdict.format_annotations()
        if verdict.passed:
            final_text = f"{draft}\n\n---\n{annotations}"
        else:
            final_text = (
                "**BLOCKED by Discipline Gate.**\n\n"
                "The generated answer violated a hard constraint and was "
                "withheld.\n\n" + annotations +
                "\n\nAsk for the safe alternative (e.g. an additive "
                "multi-step migration) and Rivet will produce it."
            )
        return SkillResponse(
            content=final_text,
            success=True,
            data={
                "text": final_text,
                "passed": verdict.passed,
                "confidence": verdict.confidence.value,
                "flags": verdict.flags,
                "requires_review": sorted(set(verdict.requires_review)),
                "beliefs_consulted": sorted(set(verdict.beliefs_consulted)),
            },
            requires_consensus=not verdict.passed,
            consensus_action=(
                "release_blocked_answer" if not verdict.passed else None
            ),
        )
