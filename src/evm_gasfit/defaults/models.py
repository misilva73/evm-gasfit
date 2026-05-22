"""Bundled :class:`ModelSpec` presets.

A preset is a frozen, named model recipe that users can list under
``models.presets`` in their YAML config to avoid copy-pasting the full spec.
Selecting a preset is equivalent to pasting its literal into ``models.custom``.
"""

from __future__ import annotations

from evm_gasfit.config import ModelSpec


PRESETS: dict[str, ModelSpec] = {
    "arithmetic_add": ModelSpec(
        test_name="test_arithmetic",
        target_operation="ADD",
        model_params={"target_coef": "OPCODE_ADD"},
    ),
    "account_access": ModelSpec(
        test_name="test_account_access",
        target_operation_param="opcode",
        model_by=["opcode"],
        model_params={"target_coef": "COLD_ACCOUNT_ACCESS"},
    ),
    "storage_access": ModelSpec(
        test_name="test_sload_bloated",
        target_operation="SLOAD",
        model_params={"target_coef": "COLD_STORAGE_ACCESS"},
    ),
}


def get_preset(name: str) -> ModelSpec:
    """Return the preset registered under ``name``.

    Raises:
        KeyError: when ``name`` is not a known preset.
    """
    return PRESETS[name]
