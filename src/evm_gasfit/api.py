"""Public entry point: :class:`GasFit` drives the pipeline stages end-to-end."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from evm_gasfit.config import Config, load_config
from evm_gasfit.glue import GlueEstimateOutput, estimate_glue
from evm_gasfit.io.fixtures import build_fixtures_df
from evm_gasfit.io.opcounts import load_opcounts
from evm_gasfit.io.runtimes import load_runtimes
from evm_gasfit.modeling.estimate import EstimateOutput, estimate_models
from evm_gasfit.proposal.build import ProposalOutput, build_proposal
from evm_gasfit.reports.glue import write_glue_report
from evm_gasfit.reports.proposal import write_proposal_report
from evm_gasfit.reports.runtime import write_runtime_report

_log = logging.getLogger("evm_gasfit")


class GasFit:
    """High-level driver wrapping the pipeline stages.

    Attributes:
        config: The validated configuration loaded from YAML.
        runtimes_df: The raw runtimes frame loaded from CSV.
        opcounts: The parsed opcounts mapping loaded from JSON.
        fixtures_df: The merged per-fixture frame consumed by every stage.
        estimate_output: The output of :meth:`estimate_models`.
        glue_estimate_output: The output of :meth:`estimate_glue`, or ``None``.
        proposal_output: The output of :meth:`build_proposal`.
    """

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.runtimes_df: pd.DataFrame | None = None
        self.opcounts: dict[str, dict[str, float]] | None = None
        self.fixtures_df: pd.DataFrame | None = None
        self.estimate_output: EstimateOutput | None = None
        self.glue_estimate_output: GlueEstimateOutput | None = None
        self.proposal_output: ProposalOutput | None = None

    @classmethod
    def from_config(cls, path: Path) -> "GasFit":
        """Load and validate the YAML config at ``path`` and return a fresh driver."""
        config = load_config(Path(path))
        return cls(config)

    def load_runtimes(self, path: Path) -> None:
        """Load the runtimes CSV at ``path``."""
        self.runtimes_df = load_runtimes(Path(path))

    def load_opcounts(self, path: Path) -> None:
        """Load the opcounts JSON at ``path``."""
        self.opcounts = load_opcounts(Path(path))

    def _ensure_fixtures(self) -> pd.DataFrame:
        if self.fixtures_df is not None:
            return self.fixtures_df
        if self.runtimes_df is None or self.opcounts is None:
            raise RuntimeError(
                "load_runtimes() and load_opcounts() must be called before fitting"
            )
        self.fixtures_df = build_fixtures_df(self.runtimes_df, self.opcounts)
        return self.fixtures_df

    def estimate_models(self) -> pd.DataFrame:
        """Fit each ``ModelSpec`` and populate :attr:`estimate_output`."""
        fixtures_df = self._ensure_fixtures()
        self.estimate_output = estimate_models(self.config, fixtures_df)
        return self.estimate_output.results_df

    def estimate_glue(self) -> pd.DataFrame:
        """Fit the priced glue opcodes and populate :attr:`glue_estimate_output`."""
        fixtures_df = self._ensure_fixtures()
        self.glue_estimate_output = estimate_glue(self.config, fixtures_df)
        return self.glue_estimate_output.results_df

    def build_proposal(self) -> pd.DataFrame:
        """Aggregate and apply derived params; populate :attr:`proposal_output`."""
        if self.estimate_output is None:
            self.estimate_models()
        assert self.estimate_output is not None
        fixtures_df = self._ensure_fixtures()
        self.proposal_output = build_proposal(
            self.config,
            self.estimate_output.results_df,
            self.glue_estimate_output,
            fixtures_df,
        )
        return self.proposal_output.new_gas_df

    @property
    def results_df(self) -> pd.DataFrame:
        if self.estimate_output is None:
            raise RuntimeError("call estimate_models() first")
        return self.estimate_output.results_df

    @property
    def glue_results_df(self) -> pd.DataFrame:
        if self.glue_estimate_output is None:
            raise RuntimeError("call estimate_glue() first")
        return self.glue_estimate_output.results_df

    @property
    def proposal_df(self) -> pd.DataFrame:
        if self.proposal_output is None:
            raise RuntimeError("call build_proposal() first")
        return self.proposal_output.new_gas_df

    def write_reports(self, out_dir: Path) -> None:
        """Write every CSV + markdown artifact (and figs, if plots enabled)."""
        if self.proposal_output is None:
            self.build_proposal()
        assert self.estimate_output is not None
        assert self.proposal_output is not None

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # CSVs first so plot/markdown writers can co-locate figs.
        self.estimate_output.results_df.to_csv(
            out_dir / "results.csv", index=False, lineterminator="\n"
        )
        self.proposal_output.new_gas_df.to_csv(
            out_dir / "new_gas.csv", index=False, lineterminator="\n"
        )
        self.proposal_output.new_gas_all_df.to_csv(
            out_dir / "new_gas_all_params.csv", index=False, lineterminator="\n"
        )

        glue_enabled = (
            self.config.glue_adjustment.enabled
            and self.glue_estimate_output is not None
        )
        if glue_enabled:
            assert self.glue_estimate_output is not None
            self.glue_estimate_output.results_df.to_csv(
                out_dir / "glue_results.csv", index=False, lineterminator="\n"
            )
            self.proposal_output.glue_opcodes_by_test_df.to_csv(
                out_dir / "glue_opcodes_by_test.csv",
                index=False,
                lineterminator="\n",
            )

        write_runtime_report(
            out_dir,
            self.estimate_output.results_df,
            self.estimate_output.fits,
            self.config,
        )
        if glue_enabled:
            assert self.glue_estimate_output is not None
            write_glue_report(
                out_dir,
                self.glue_estimate_output.results_df,
                self.glue_estimate_output.fits,
                self.config,
            )
        write_proposal_report(out_dir, self.proposal_output, self.config)
