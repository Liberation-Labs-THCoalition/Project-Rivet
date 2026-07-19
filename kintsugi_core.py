"""Kintsugi core loader — real package first, vendored copy as fallback.

Resolution order:
  1. `KINTSUGI_PATH` env var pointing at a Project-Kintsugi checkout
  2. an installed/importable `kintsugi` package
  3. the vendored verbatim copy in v2/kintsugi_vendor/

Everything downstream imports Kintsugi symbols from THIS module, so the
same Rivet code runs against the real engine on a dev box and against
the vendor copy on a bare deployment target.
"""

import os
import sys
from pathlib import Path

_VENDOR = Path(__file__).parent / "kintsugi_vendor"

KINTSUGI_SOURCE = "unresolved"


def _resolve() -> None:
    global KINTSUGI_SOURCE

    env_path = os.environ.get("KINTSUGI_PATH")
    if env_path and (Path(env_path) / "kintsugi" / "__init__.py").exists():
        sys.path.insert(0, env_path)
        KINTSUGI_SOURCE = f"checkout:{env_path}"
        return

    try:
        import kintsugi  # noqa: F401
        KINTSUGI_SOURCE = "installed"
        return
    except ImportError:
        pass

    sys.path.insert(0, str(_VENDOR))
    KINTSUGI_SOURCE = f"vendor:{_VENDOR}"


_resolve()

from kintsugi.bdi import (  # noqa: E402
    BDIBelief,
    BDIDesire,
    BDIIntention,
    BDISnapshot,
    BDIStore,
    BeliefStatus,
    DesireStatus,
    IntentionStatus,
)
from kintsugi.skills.base import (  # noqa: E402
    BaseSkillChip,
    EFEWeights,
    SkillCapability,
    SkillContext,
    SkillDomain,
    SkillRequest,
    SkillResponse,
)
from kintsugi.skills.dag import (  # noqa: E402
    DAGBuilder,
    DAGExecutor,
    DAGNode,
    DAGResult,
    SkillDAG,
)
from kintsugi.skills.registry import SkillRegistry  # noqa: E402
from kintsugi.kintsugi_engine.scaffold_orchestrator import (  # noqa: E402
    ScaffoldExecutionResult,
    ScaffoldOrchestrator,
    ScaffoldOrchestratorConfig,
)
from kintsugi.kintsugi_engine.scaffold_generator import (  # noqa: E402
    ScaffoldGenerator,
    ScaffoldMemory,
    ScaffoldProposal,
)
from kintsugi.kintsugi_engine.scaffold_comparator import (  # noqa: E402
    ScaffoldComparator,
    ScaffoldComparison,
)
from kintsugi.kintsugi_engine.scaffold_memory import (  # noqa: E402
    InMemoryScaffoldKG,
)
from kintsugi.kintsugi_engine.scaffold_exploration import (  # noqa: E402
    ExplorationDecision,
    ScaffoldExplorer,
)
from kintsugi.kintsugi_engine.persona_gate import (  # noqa: E402
    PersonaGate,
    PersonaGateConfig,
    PersonaStatus,
)

__all__ = [
    "KINTSUGI_SOURCE",
    "BDIBelief",
    "BDIDesire",
    "BDIIntention",
    "BDISnapshot",
    "BDIStore",
    "BeliefStatus",
    "DesireStatus",
    "IntentionStatus",
    "BaseSkillChip",
    "EFEWeights",
    "SkillCapability",
    "SkillContext",
    "SkillDomain",
    "SkillRequest",
    "SkillResponse",
    "DAGBuilder",
    "DAGExecutor",
    "DAGNode",
    "DAGResult",
    "SkillDAG",
    "SkillRegistry",
    # Adaptive scaffold evolution
    "ScaffoldExecutionResult",
    "ScaffoldOrchestrator",
    "ScaffoldOrchestratorConfig",
    "ScaffoldGenerator",
    "ScaffoldMemory",
    "ScaffoldProposal",
    "ScaffoldComparator",
    "ScaffoldComparison",
    "InMemoryScaffoldKG",
    "ExplorationDecision",
    "ScaffoldExplorer",
    # Persona coherence gate (OGPSA)
    "PersonaGate",
    "PersonaGateConfig",
    "PersonaStatus",
]
