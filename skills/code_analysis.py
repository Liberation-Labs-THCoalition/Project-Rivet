"""Code analysis chip — reads actual campus source before anyone opines.

Extracts file paths and symbols from the request, reads those files from
the repo (when present), follows one level of imports, and grabs recent
git history for each file. Everything read is recorded in the session's
evidence log — this is what lets the discipline gate honestly say HIGH
confidence ("I read the file") vs LOW ("I'm reasoning from the map").
"""

import re

from kintsugi_core import (
    BaseSkillChip,
    EFEWeights,
    SkillCapability,
    SkillContext,
    SkillDomain,
    SkillRequest,
    SkillResponse,
)

PATH_RE = re.compile(
    r"\b((?:server|client|shared|design-system)/[\w./-]+\.(?:ts|tsx|js|jsx|sql|json))\b"
)
SYMBOL_RE = re.compile(r"\b([a-z][a-zA-Z0-9]+(?:Handler|Store|Service|Middleware|Router))\b")
IMPORT_RE = re.compile(r"""from\s+['"]([^'"]+)['"]""")

MAX_FILES = 6
MAX_EXCERPT = 4000


class CodeAnalysisChip(BaseSkillChip):
    name = "code_analysis"
    description = "Read and trace campus source files named in the request"
    version = "2.0.0"
    domain = SkillDomain.RESEARCH
    efe_weights = EFEWeights(
        mission_alignment=0.20, stakeholder_benefit=0.20,
        resource_efficiency=0.25, transparency=0.25, equity=0.10,
    )
    capabilities = [SkillCapability.READ_DATA]

    def __init__(self, repo_files=None, git_tools=None):
        super().__init__()
        self.repo_files = repo_files
        self.git_tools = git_tools

    async def handle(self, request: SkillRequest,
                     context: SkillContext) -> SkillResponse:
        question = context.metadata.get("question", request.raw_input)
        session = context.metadata.get("session")

        paths = list(dict.fromkeys(PATH_RE.findall(question)))[:MAX_FILES]
        symbols = list(dict.fromkeys(SYMBOL_RE.findall(question)))[:5]

        analysis = {
            "repo_available": self.repo_files is not None,
            "requested_paths": paths,
            "symbols": symbols,
            "files": [],       # [{path, excerpt, git_status, recent_history, imports}]
            "symbol_sites": [],
            "notes": [],
        }

        if self.repo_files is None:
            analysis["notes"].append(
                "Campus repo is not on this machine — analysis is limited "
                "to the architecture map. Confidence will be capped."
            )
            return self._done(analysis, session)

        # Locate files for bare symbols the user named.
        for sym in symbols:
            hits = self.repo_files.search(rf"\b{re.escape(sym)}\b")[:3]
            analysis["symbol_sites"].extend(hits)
            for h in hits:
                if h["path"] not in paths and len(paths) < MAX_FILES:
                    paths.append(h["path"])

        for path in paths[:MAX_FILES]:
            read = self.repo_files.read(path)
            if not read.ok:
                analysis["notes"].append(f"could not read {path}: {read.error}")
                continue
            entry = {
                "path": path,
                "excerpt": read.content[:MAX_EXCERPT],
                "truncated": read.truncated or len(read.content) > MAX_EXCERPT,
                "git_status": read.git_status,
                "imports": IMPORT_RE.findall(read.content)[:20],
                "recent_history": (
                    self.git_tools.recent_authors(path) if self.git_tools else ""
                ),
            }
            analysis["files"].append(entry)
            if session:
                session.record_file_read(path, self.name)

        # One level of local-import tracing for the first file.
        if analysis["files"]:
            first = analysis["files"][0]
            local = [imp for imp in first["imports"] if imp.startswith(".")][:3]
            for imp in local:
                resolved = self._resolve_import(first["path"], imp)
                if resolved and len(analysis["files"]) < MAX_FILES:
                    read = self.repo_files.read(resolved)
                    if read.ok:
                        analysis["files"].append({
                            "path": resolved,
                            "excerpt": read.content[:MAX_EXCERPT // 2],
                            "truncated": True,
                            "git_status": read.git_status,
                            "imports": [],
                            "recent_history": "",
                            "traced_from": first["path"],
                        })
                        if session:
                            session.record_file_read(resolved, self.name)

        return self._done(analysis, session)

    def _resolve_import(self, from_path: str, import_spec: str) -> str:
        base = "/".join(from_path.split("/")[:-1])
        candidate = f"{base}/{import_spec.lstrip('./')}"
        for suffix in (".ts", ".tsx", "/index.ts", ".js"):
            probe = candidate + suffix
            if self.repo_files.read(probe).ok:
                return probe
        return ""

    def _done(self, analysis: dict, session) -> SkillResponse:
        n_files = len(analysis["files"])
        summary = (
            f"read {n_files} file(s), "
            f"{len(analysis['symbol_sites'])} symbol site(s); "
            f"repo_available={analysis['repo_available']}"
        )
        return SkillResponse(content=summary, success=True, data=analysis)
