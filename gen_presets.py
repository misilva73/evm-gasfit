"""Render the bundled ModelSpec preset catalog into a virtual docs page.

Runs at ``mkdocs build`` time via ``mkdocs-gen-files``. The output lives at
``reference/presets.md`` in the rendered site and is never committed to the
repo, so the page stays in sync with ``evm_gasfit.defaults.models.PRESETS``.
"""

from __future__ import annotations

from urllib.parse import quote

import mkdocs_gen_files

from evm_gasfit.config import FixtureParamSpec, ModelSpec
from evm_gasfit.defaults.models import PRESETS

# ``test_name`` is the pytest function name (e.g. ``def test_arithmetic(...)``,
# ``def test_sload_bloated(...)``), not a file name. We render a repo-scoped
# code search for the exact ``def <test_name>`` token — always resolves to the
# file holding the function, no path map to maintain.
_EELS_SEARCH_URL = (
    "https://github.com/search?type=code&q=repo%3Aethereum%2Fexecution-specs+{query}"
)


def _test_name_link(test_name: str) -> str:
    query = quote(f'"def {test_name}"')
    return f"[`{test_name}`]({_EELS_SEARCH_URL.format(query=query)})"


# Display name + ordered prefix matchers. The first matching prefix wins, so
# the more specific ``precompile_bls_`` must precede ``precompile_``. ``keccak``
# is matched exactly because it's a single preset that doesn't share a prefix.
CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("Arithmetic", ("arithmetic_",)),
    ("Bitwise", ("bitwise_",)),
    ("Comparison", ("comparison_",)),
    ("Stack", ("stack_",)),
    ("Control flow", ("control_flow_",)),
    ("Block & transaction context", ("block_", "tx_")),
    ("Call context", ("call_",)),
    ("Memory", ("memory_",)),
    ("Account / storage / state", ("warm_", "cold_", "account_")),
    ("Transient storage", ("storage_",)),
    ("Hashing", ("keccak",)),
    ("System", ("system_",)),
    ("BLS12-381 precompiles", ("precompile_bls_",)),
    ("Precompiles", ("precompile_",)),
]


def categorize(name: str) -> str:
    for category, prefixes in CATEGORIES:
        for prefix in prefixes:
            if name == prefix or name.startswith(prefix):
                return category
    raise RuntimeError(
        f"preset {name!r} doesn't match any category prefix in gen_presets.py; "
        "add a new entry to CATEGORIES if this is a new opcode family."
    )


def _render_fixture_params(fixture_params: dict[str, FixtureParamSpec]) -> str:
    if not fixture_params:
        return "—"
    parts = []
    for name, spec in fixture_params.items():
        bits = [f"source=`{spec.source}`"]
        if spec.transform is not None:
            bits.append(f"transform=`{spec.transform}`")
        if spec.values is not None:
            mapping = ", ".join(f"`{k}`→`{v}`" for k, v in spec.values.items())
            bits.append(f"values={{{mapping}}}")
        parts.append(f"`{name}` ({'; '.join(bits)})")
    return "<br>".join(parts)


def _render_model_params(model_params: dict[str, str]) -> str:
    return "<br>".join(f"`{k}` → `{v}`" for k, v in model_params.items())


def _render_list(values: list[str]) -> str:
    if not values:
        return "—"
    return ", ".join(f"`{v}`" for v in values)


def _render_target(spec: ModelSpec) -> str:
    if spec.target_operation is not None:
        suffix = (
            f" (counts from `{spec.target_operation_count_source}`)"
            if spec.target_operation_count_source is not None
            else ""
        )
        return f"`{spec.target_operation}`{suffix}"
    return f"param `{spec.target_operation_param}`"


def render_preset(name: str, spec: ModelSpec) -> list[str]:
    rows = [
        f"### `{name}`",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| `test_name` | {_test_name_link(spec.test_name)} |",
        f"| target | {_render_target(spec)} |",
        f"| `filter_by` | {_render_list(spec.filter_by)} |",
        f"| `model_by` | {_render_list(spec.model_by)} |",
        f"| `model_params` | {_render_model_params(spec.model_params)} |",
        f"| `fixture_params` | {_render_fixture_params(spec.fixture_params)} |",
        "",
    ]
    return rows


def build_page() -> str:
    grouped: dict[str, list[str]] = {category: [] for category, _ in CATEGORIES}
    for name in PRESETS:
        grouped[categorize(name)].append(name)

    lines = [
        "# Preset catalog",
        "",
        (
            f"The package ships {len(PRESETS)} `ModelSpec` presets. Selecting a "
            "preset under `models.presets` in the YAML config is equivalent to "
            "pasting its literal into `models.custom`."
        ),
        "",
        (
            "Each preset binds a `test_name` (an EEST test file), a target "
            "(literal opcode or `target_operation_param`), optional fixture "
            "selectors (`filter_by`), grouping dimensions (`model_by`), derived "
            "columns (`fixture_params`), and a `model_params` map from regression "
            "coefficient names to gas-param names."
        ),
        "",
        (
            "See [Writing custom ModelSpecs](../guides/custom-modelspecs.md) for "
            "what each field means and [Deriving gas params](../concepts/gas-params.md) "
            "for how the fitted coefficients become the final proposal."
        ),
        "",
    ]
    for category, _ in CATEGORIES:
        names = grouped[category]
        if not names:
            continue
        lines.append(f"## {category} ({len(names)})")
        lines.append("")
        for name in names:
            lines.extend(render_preset(name, PRESETS[name]))
    return "\n".join(lines)


with mkdocs_gen_files.open("reference/presets.md", "w") as fp:
    fp.write(build_page())
