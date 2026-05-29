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
    glue_contribution_rsquared_threshold: float = 0.5
    ratio_corr_eps: float = 0.05


class ModelingSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bootstrap_iterations: int = 1000
    poor_fit_p_value_threshold: float = 0.05
    poor_fit_rsquared_threshold: float = 0.5
    random_seed: int = 42


class OutputSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plots: bool = True


class FixtureParamSpec(BaseModel):
    """Declare a derived fixture-param column from a raw parsed param."""

    model_config = ConfigDict(extra="forbid")

    source: str
    values: dict[str, float] | None = None
    transform: Literal["bytes_to_words"] | None = None

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

    @model_validator(mode="after")
    def _check_transform_excludes_values(self) -> "FixtureParamSpec":
        if self.transform is not None and self.values is not None:
            raise ValueError(
                "fixture_params: 'transform' and 'values' are mutually exclusive"
            )
        return self


class ModelSpec(BaseModel):
    """One regression recipe: a fixture selector plus a coefficient → gas-param map."""

    model_config = ConfigDict(extra="forbid")

    test_name: str
    target_operation: str | None = None
    target_operation_param: str | None = None
    target_operation_count_source: str | None = None
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
        # The precompile escape hatch only makes sense with a literal target.
        has_count_source = self.target_operation_count_source is not None
        if has_count_source and not has_op:
            raise ValueError(
                "target_operation_count_source is only valid alongside "
                "target_operation (literal precompile display name)"
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
    clients: list[str]
    gas_costs: GasCostsSection
    glue_adjustment: GlueAdjustmentSection = Field(
        default_factory=GlueAdjustmentSection
    )
    modeling: ModelingSection = Field(default_factory=ModelingSection)
    output: OutputSection = Field(default_factory=OutputSection)
    # ``derived`` values are either ``str`` (alias form) or ``{formula: str}``.
    derived: dict[str, Any] = Field(default_factory=dict)
    # Names introduced by the user/preset that are not raw fork fields. ``None``
    # value means "no prior default"; integer value renders as ``current_gas``
    # in the proposal diff column.
    new_params: dict[str, int | None] = Field(default_factory=dict)
    models: ModelsSection

    # Computed at validation time.
    resolved_models: list[ModelSpec] = Field(default_factory=list)
    gas_costs_obj: GasCosts | None = None
    raw_fork_fields: frozenset[str] = Field(default_factory=frozenset)
    param_universe: frozenset[str] = Field(default_factory=frozenset)
    warnings: list[str] = Field(default_factory=list)
    derived_evaluated: dict[str, tuple[str, ast.Expression]] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _cross_validate(self) -> "Config":
        # 0) Validate clients: non-empty list of non-empty unique strings.
        if not self.clients:
            raise ConfigError("clients must be a non-empty list")
        seen_clients: set[str] = set()
        for name in self.clients:
            if not isinstance(name, str) or not name:
                raise ConfigError("clients entries must be non-empty strings")
            if name in seen_clients:
                raise ConfigError(f"duplicate client name {name!r} in clients")
            seen_clients.add(name)

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
            spec_with_label = spec.model_copy(
                update={"source_label": f"models.custom[{i}]"}
            )
            resolved.append(spec_with_label)
        if not resolved:
            raise ConfigError(
                "no models to fit: both models.presets and models.custom are empty"
            )
        # Spec authors write natural param names (``opcode``, ``mem_size``);
        # ``build_fixtures_df`` exposes them as ``param_<key>`` columns so they
        # can't collide with opcode mnemonics. Mirror that prefix on every spec
        # field that points at a parsed-param column so downstream code reads
        # the right column. A ``model_by`` element may instead point at a
        # derived column (a key in ``fixture_params``) materialized at fit
        # time — in that case it must stay unprefixed. ``model_params`` keys
        # are resolved at the use site in ``_build_design`` to handle both
        # raw-param and derived references.
        prefixed: list[ModelSpec] = []
        for spec in resolved:
            derived = set(spec.fixture_params)
            updates: dict[str, object] = {}
            if spec.target_operation_param is not None:
                updates["target_operation_param"] = (
                    f"param_{spec.target_operation_param}"
                )
            if spec.model_by:
                updates["model_by"] = [
                    c if c in derived else f"param_{c}" for c in spec.model_by
                ]
            if spec.fixture_params:
                updates["fixture_params"] = {
                    k: v.model_copy(update={"source": f"param_{v.source}"})
                    for k, v in spec.fixture_params.items()
                }
            prefixed.append(spec.model_copy(update=updates) if updates else spec)
        self.resolved_models = prefixed

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

        # 5) Strict new_params declaration check.
        for key in self.new_params:
            if not isinstance(key, str) or not key:
                raise ConfigError("new_params keys must be non-empty strings")
            if key in self.raw_fork_fields:
                raise ConfigError(
                    f"new_params[{key!r}] is already a raw fork field on "
                    f"{self.gas_costs.fork!r}; use gas_costs.overrides to patch "
                    f"it instead"
                )
        declared_new_params: set[str] = set(self.new_params)

        # 6) Build the universe.
        proposed_by_model_params: set[str] = {
            v for spec in resolved for v in spec.model_params.values()
        }
        proposed_by_derived: set[str] = set(self.derived.keys())
        self.param_universe = frozenset(
            self.raw_fork_fields
            | proposed_by_model_params
            | proposed_by_derived
            | declared_new_params
        )

        # 7) Strict model_params RHS check: every non-raw RHS must be declared
        # in new_params (typo guard + explicit proposal of new names).
        allowed_rhs = self.raw_fork_fields | declared_new_params
        for spec in resolved:
            for coef_name, gas_param in spec.model_params.items():
                if gas_param in allowed_rhs:
                    continue
                hint = get_close_matches(gas_param, list(allowed_rhs), n=1)
                suffix = f"; did you mean {hint[0]!r}?" if hint else ""
                raise ConfigError(
                    f"{spec.source_label} (test_name={spec.test_name!r}): "
                    f"model_params[{coef_name!r}] = {gas_param!r} is not a raw "
                    f"fork field on {self.gas_costs.fork!r} and is not declared "
                    f"in new_params{suffix}"
                )

        # 8) Lenient derived-shadowing check.
        for name in self.derived:
            if name in self.raw_fork_fields:
                msg = (
                    f"derived: {name!r} shadows a raw fork field on "
                    f"{self.gas_costs.fork!r}"
                )
                _log.warning(msg)
                self.warnings.append(msg)

        # 9) Derived-formula AST + identifier resolution. Declaration order.
        derived_referenced: set[str] = set()
        seen_derived: set[str] = set()
        for name, raw_or_formula in self.derived.items():
            if isinstance(raw_or_formula, str):
                raw = raw_or_formula
            elif isinstance(raw_or_formula, dict) and "formula" in raw_or_formula:
                formula = raw_or_formula["formula"]
                if not isinstance(formula, str):
                    raise ConfigError(f"derived[{name!r}].formula must be a string")
                raw = formula
            else:
                raise ConfigError(
                    f"derived[{name!r}] must be a string alias or {{formula: <expr>}} mapping"
                )
            tree = parse_formula(raw)
            universe = (
                self.raw_fork_fields
                | proposed_by_model_params
                | declared_new_params
                | seen_derived
            )
            for ident in names_referenced(tree):
                if ident not in universe:
                    hint = get_close_matches(ident, list(universe), n=1)
                    suffix = f"; did you mean {hint[0]!r}?" if hint else ""
                    raise ConfigError(
                        f"derived[{name!r}]: unknown identifier {ident!r}{suffix}"
                    )
                derived_referenced.add(ident)
            self.derived_evaluated[name] = (raw, tree)
            seen_derived.add(name)

        # 10) Dead-declaration check: every new_params key must be referenced
        # by some model_params RHS, derived alias RHS, or derived formula.
        referenced_names = proposed_by_model_params | derived_referenced
        unreferenced = declared_new_params - referenced_names
        if unreferenced:
            names = ", ".join(repr(n) for n in sorted(unreferenced))
            raise ConfigError(
                f"new_params declared but never referenced by any model_params "
                f"RHS or derived formula: {names}"
            )

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
            cause = (
                err.get("ctx", {}).get("error")
                if isinstance(err.get("ctx"), dict)
                else None
            )
            if isinstance(cause, ConfigError):
                raise cause from exc
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"invalid config {path}: {details}") from exc
