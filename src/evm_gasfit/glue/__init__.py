"""Glue-opcode estimation, detection, and adjustment."""

from __future__ import annotations

from .adjust import compute_glue_adjustment
from .detect import compute_glue_opcodes_by_test, detect_missing_glue
from .estimate import GlueEstimateOutput, estimate_glue
from .required import (
    CYCLE_GLUE_OPCODES,
    PRICED_GLUE_OPCODES,
    PURE_GLUE_OPCODES,
    REQUIRED_GLUE_TESTS,
    validate_inputs,
)

__all__ = [
    "CYCLE_GLUE_OPCODES",
    "GlueEstimateOutput",
    "PRICED_GLUE_OPCODES",
    "PURE_GLUE_OPCODES",
    "REQUIRED_GLUE_TESTS",
    "compute_glue_adjustment",
    "compute_glue_opcodes_by_test",
    "detect_missing_glue",
    "estimate_glue",
    "validate_inputs",
]
