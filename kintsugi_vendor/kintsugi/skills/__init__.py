"""Kintsugi skills package (vendored subset for Rivet).

The upstream __init__ also exports the intent router; Rivet plans via
SkillDAGs instead, so only base + dag + registry are vendored.
"""

from .base import (
    ActivationCondition,
    BaseSkillChip,
    EFEWeights,
    InterventionAction,
    ProgramFunction,
    SkillCapability,
    SkillContext,
    SkillDomain,
    SkillHandler,
    SkillRequest,
    SkillResponse,
)
from .dag import DAGBuilder, DAGExecutor, DAGNode, DAGResult, SkillDAG
from .registry import SkillRegistry

__all__ = [
    "ActivationCondition",
    "BaseSkillChip",
    "DAGBuilder",
    "DAGExecutor",
    "DAGNode",
    "DAGResult",
    "EFEWeights",
    "InterventionAction",
    "ProgramFunction",
    "SkillCapability",
    "SkillContext",
    "SkillDAG",
    "SkillDomain",
    "SkillHandler",
    "SkillRegistry",
    "SkillRequest",
    "SkillResponse",
]
