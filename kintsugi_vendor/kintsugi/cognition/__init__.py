"""Vendored cognition — EFE calculator only.

Full cognition package (orchestrator, model router, LLM client, active
inference) is in the main Project-Kintsugi repo. This vendor copy
includes only EFE needed by the scaffold exploration module.
"""

from kintsugi.cognition.efe import (
    EFECalculator,
    EFEScore,
    EFEWeights,
)

__all__ = [
    "EFECalculator",
    "EFEScore",
    "EFEWeights",
]
