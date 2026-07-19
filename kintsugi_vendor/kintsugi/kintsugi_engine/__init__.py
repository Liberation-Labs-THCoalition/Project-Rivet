"""Vendored kintsugi_engine — scaffold modules + persona gate.

Full engine (shadow fork, verifier, promoter, evolution, etc.) is in
the main Project-Kintsugi repo. This vendor copy includes the adaptive
scaffold evolution modules and OGPSA persona coherence gate.
"""

from kintsugi.kintsugi_engine.scaffold_generator import (
    ScaffoldGenerator,
    ScaffoldMemory,
    ScaffoldProposal,
)
from kintsugi.kintsugi_engine.scaffold_comparator import (
    ScaffoldComparator,
    ScaffoldComparison,
    ScaffoldMetrics,
    run_scaffold_comparison,
)
from kintsugi.kintsugi_engine.scaffold_memory import (
    InMemoryScaffoldKG,
    ScaffoldRecord,
)
from kintsugi.kintsugi_engine.scaffold_exploration import (
    ExplorationDecision,
    ExplorationResult,
    ScaffoldExplorer,
)
from kintsugi.kintsugi_engine.scaffold_orchestrator import (
    ScaffoldExecutionResult,
    ScaffoldOrchestrator,
    ScaffoldOrchestratorConfig,
)
from kintsugi.kintsugi_engine.persona_gate import (
    PersonaGate,
    PersonaGateConfig,
    PersonaGateResult,
    PersonaMeasurement,
    PersonaStatus,
)

__all__ = [
    "ScaffoldGenerator",
    "ScaffoldMemory",
    "ScaffoldProposal",
    "ScaffoldComparator",
    "ScaffoldComparison",
    "ScaffoldMetrics",
    "run_scaffold_comparison",
    "InMemoryScaffoldKG",
    "ScaffoldRecord",
    "ExplorationDecision",
    "ExplorationResult",
    "ScaffoldExplorer",
    "ScaffoldExecutionResult",
    "ScaffoldOrchestrator",
    "ScaffoldOrchestratorConfig",
    "PersonaGate",
    "PersonaGateConfig",
    "PersonaGateResult",
    "PersonaMeasurement",
    "PersonaStatus",
]
