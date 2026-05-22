"""Pydantic v2 schema for the YAML config and ``load_config`` entry point.

The schema is described in the package's top-level design notes; this module
is the executable form. ``Config.model_validate`` enforces every cross-field
rule needed to drive the rest of the pipeline (preset resolution, fork
selection, override-key validation, derived-formula identifier resolution).
"""

from __future__ import annotations

import ast
import logging
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from evm_gasfit.defaults import GasCosts, get_gas_costs
from evm_gasfit.errors import ConfigError
from evm_gasfit.proposal.derived import names_referenced, parse_formula

_log = logging.getLogger("evm_gasfit")


# ---------------------------------------------------------------------------
# Helper: scalar-or-list normalization for ``filter_by`` / ``model_by``.
# ---------------------------------------------------------------------------


def _normalize_str_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = list(value)
    else:
        raise ValueError(f"{field_name} must be a string or list of strings")
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} entries must be strings (got {item!r})")
        if item == "":
            raise ValueError(f"{field_name} entries must be non-empty strings")
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Leaf sections.
# ---------------------------------------------------------------------------


class GasCostsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fork: str
    overrides: dict[str, int] = Field(default_factory=dict)


class GlueAdjustmentSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    glue_contribution_p_value_threshold: float = 0.05
    ratio_corr_eps: float = 0.05


class ModelingSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bootstrap_iterations: int = 1000
    poor_fit_p_value_threshold: float = 0.05
    random_seed: int = 42


class OutputSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plots: bool = True


class FixtureParamSpec(BaseModel):
    """Declare a derived fixture-param column from a raw parsed param."""

    model_config = ConfigDict(extra="forbid")

    source: str
    values: dict[str, float] | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_values(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        vals = data.get("values")
        if vals is None:
            return data
        if not isinstance(vals, dict):
            raise ValueError("fixture_params.values must be a mapping")
        # Keys may be YAML booleans/numbers; coerce to ``str`` and float values.
        data = dict(data)
        data["values"] = {str(k): float(v) for k, v in vals.items()}
        return data


class ModelSpec(BaseModel):
    """One regression recipe: a fixture selector plus a coefficient → gas-param map."""

    model_config = ConfigDict(extra="forbid")

    test_name: str
    target_operation: str | None = None
    target_operation_param: str | None = None
    filter_by: list[str] = Field(default_factory=list)
    model_by: list[str] = Field(default_factory=list)
    model_params: dict[str, str] = Field(default_factory=dict)
    fixture_params: dict[str, FixtureParamSpec] = Field(default_factory=dict)
    # Set by ``Config``'s top-level validator so diagnostic messages can name
    # the source of a spec (``presets[...]`` vs. ``models.custom[i]``).
    source_label: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_lists(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "filter_by" in data:
            data["filter_by"] = _normalize_str_list(data["filter_by"], "filter_by")
        if "model_by" in data:
            data["model_by"] = _normalize_str_list(data["model_by"], "model_by")
        return data

    @model_validator(mode="after")
    def _check(self) -> "ModelSpec":
        # Exactly one of target_operation / target_operation_param.
        has_op = self.target_operation is not None
        has_param = self.target_operation_param is not None
        if has_op == has_param:
            raise ValueError(
                "exactly one of target_operation / target_operation_param must be set"
            )
        # model_params must be non-empty and carry a target_coef key.
        if not self.model_params:
            raise ValueError("model_params must be non-empty")
        if "target_coef" not in self.model_params:
            raise ConfigError(
                f"model_params missing required 'target_coef' key on spec test_name={self.test_name!r}"
            )
        # Derived names must not collide with target_operation_param.
        if has_param and self.target_operation_param in self.fixture_params:
            raise ConfigError(
                f"derived param name {self.target_operation_param!r} collides "
                f"with target_operation_param on spec test_name={self.test_name!r}"
            )
        return self


class ModelsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presets: list[str] = Field(default_factory=list)
    custom: list[ModelSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reject_duplicate_presets(self) -> "ModelsSection":
        seen: set[str] = set()
        for name in self.presets:
            if name in seen:
                raise ConfigError(f"duplicate preset name {name!r} in models.presets")
            seen.add(name)
        return self


class Config(BaseModel):
    """The full validated config.

    Beyond the YAML fields, exposes ``resolved_models``, ``gas_costs_obj``,
    ``raw_fork_fields``, ``param_universe``, ``derived_evaluated``, and
    ``warnings`` computed at validation time so downstream stages can consume
    them without redoing the work.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    version: Literal[1]
    anchor_rate: float
    gas_costs: GasCostsSection
    glue_adjustment: GlueAdjustmentSection = Field(default_factory=GlueAdjustmentSection)
    modeling: ModelingSection = Field(default_factory=ModelingSection)
    output: OutputSection = Field(default_factory=OutputSection)
    # ``derived`` values are either ``str`` (alias form) or ``{formula: str}``.
    derived: dict[str, Any] = Field(default_factory=dict)
    models: ModelsSection

    # Computed at validation time.
    resolved_models: list[ModelSpec] = Field(default_factory=list)
    gas_costs_obj: GasCosts | None = None
    raw_fork_fields: frozenset[str] = Field(default_factory=frozenset)
    param_universe: frozenset[str] = Field(default_factory=frozenset)
    warnings: list[str] = Field(default_factory=list)
    derived_evaluated: dict[str, tuple[str, ast.Expression]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _cross_validate(self) -> "Config":
        # 1) Resolve presets.
        from evm_gasfit.defaults.models import PRESETS  # local: avoid import cycle.

        resolved: list[ModelSpec] = []
        for name in self.models.presets:
            if name not in PRESETS:
                hint = get_close_matches(name, list(PRESETS), n=1)
                suffix = f"; did you mean {hint[0]!r}?" if hint else ""
                raise ConfigError(f"unknown preset {name!r}{suffix}")
            preset = PRESETS[name]
            # Copy so source_label can be set without mutating the registry.
            spec = preset.model_copy(update={"source_label": f"presets[{name}]"})
            resolved.append(spec)
        for i, spec in enumerate(self.models.custom):
            spec_with_label = spec.model_copy(update={"source_label": f"models.custom[{i}]"})
            resolved.append(spec_with_label)
        if not resolved:
            raise ConfigError(
                "no models to fit: both models.presets and models.custom are empty"
            )
        self.resolved_models = resolved

        # 2) Instantiate the fork's GasCosts.
        gc = get_gas_costs(self.gas_costs.fork)
        self.raw_fork_fields = gc.field_names

        # 3) Strict override-key check.
        for key in self.gas_costs.overrides:
            if key not in self.raw_fork_fields:
                hint = get_close_matches(key, list(self.raw_fork_fields), n=1)
                suffix = f"; did you mean {hint[0]!r}?" if hint else ""
                raise ConfigError(
                    f"unknown override key {key!r} for fork {self.gas_costs.fork!r}{suffix}"
                )
        # 4) Apply overrides.
        for key, value in self.gas_costs.overrides.items():
            gc[key] = value
        self.gas_costs_obj = gc

        # 5) Build the universe.
        proposed_by_model_params: set[str] = {
            v for spec in resolved for v in spec.model_params.values()
        }
        proposed_by_derived: set[str] = set(self.derived.keys())
        self.param_universe = frozenset(
            self.raw_fork_fields | proposed_by_model_params | proposed_by_derived
        )

        # 6) Lenient model_params RHS check.
        for spec in resolved:
            for coef_name, gas_param in spec.model_params.items():
                if gas_param in self.raw_fork_fields:
                    continue
                candidates = (
                    self.raw_fork_fields | proposed_by_model_params
                ) - {gas_param}
                hint = get_close_matches(gas_param, list(candidates), n=1)
                suffix = f"; did you mean {hint[0]!r}?" if hint else ""
                msg = (
                    f"{spec.source_label} (test_name={spec.test_name!r}): "
                    f"model_params[{coef_name!r}] = {gas_param!r} is not a raw fork "
                    f"field on {self.gas_costs.fork!r}{suffix}"
                )
                _log.warning(msg)
                self.warnings.append(msg)

        # 7) Lenient derived-shadowing check.
        for name in self.derived:
            if name in self.raw_fork_fields:
                msg = (
                    f"derived: {name!r} shadows a raw fork field on "
                    f"{self.gas_costs.fork!r}"
                )
                _log.warning(msg)
                self.warnings.append(msg)

        # 8) Derived-formula AST + identifier resolution. Declaration order.
        seen_derived: set[str] = set()
        for name, raw_or_formula in self.derived.items():
            if isinstance(raw_or_formula, str):
                raw = raw_or_formula
            elif isinstance(raw_or_formula, dict) and "formula" in raw_or_formula:
                formula = raw_or_formula["formula"]
                if not isinstance(formula, str):
                    raise ConfigError(
                        f"derived[{name!r}].formula must be a string"
                    )
                raw = formula
            else:
                raise ConfigError(
                    f"derived[{name!r}] must be a string alias or {{formula: <expr>}} mapping"
                )
            tree = parse_formula(raw)
            universe = (
                self.raw_fork_fields | proposed_by_model_params | seen_derived
            )
            for ident in names_referenced(tree):
                if ident not in universe:
                    hint = get_close_matches(ident, list(universe), n=1)
                    suffix = f"; did you mean {hint[0]!r}?" if hint else ""
                    raise ConfigError(
                        f"derived[{name!r}]: unknown identifier {ident!r}{suffix}"
                    )
            self.derived_evaluated[name] = (raw, tree)
            seen_derived.add(name)

        return self


def load_config(path: Path) -> Config:
    """Load and validate the YAML config at ``path``.

    Args:
        path: Filesystem path to the YAML config file.

    Returns:
        The validated ``Config`` with all computed-at-load-time fields set.

    Raises:
        ConfigError: When the file is missing, the YAML is malformed, or any
            Pydantic / cross-field validation rule fails.
    """
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"config {path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config {path} must be a YAML mapping at the top level")
    try:
        return Config.model_validate(raw)
    except ConfigError:
        raise
    except ValidationError as exc:
        # Unwrap a ConfigError raised inside a validator (Pydantic wraps it).
        for err in exc.errors():
            cause = err.get("ctx", {}).get("error") if isinstance(err.get("ctx"), dict) else None
            if isinstance(cause, ConfigError):
                raise cause from exc
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"invalid config {path}: {details}") from exc
