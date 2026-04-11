"""Pipeline runner — sequential CAD -> Mesh -> FEA -> CFD -> Results orchestrator."""

from __future__ import annotations

import gc
import logging
from pathlib import Path

from clawgeneer.adapters.base import AdapterResult
from clawgeneer.adapters.cad.build123d_adapter import Build123dAdapter
from clawgeneer.adapters.cad.upload_adapter import UploadAdapter
from clawgeneer.adapters.cfd.openfoam_adapter import OpenFOAMAdapter
from clawgeneer.adapters.fea.calculix_adapter import CalculixAdapter
from clawgeneer.adapters.mesh.gmsh_adapter import GmshAdapter
from clawgeneer.adapters.results.pyvista_adapter import PyvistaAdapter
from clawgeneer.pipeline.state import PipelineState, StageStatus
from clawgeneer.schema.project import GeometrySource, JobType, ProjectConfig

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Sequential pipeline: CAD -> Mesh -> FEA -> CFD -> Results.

    Critically, FEA and CFD are NEVER run simultaneously. Each stage
    releases memory before the next stage begins.

    The runner is resumable: completed stages are skipped on re-run
    unless explicitly reset.
    """

    def __init__(self, project: ProjectConfig, project_dir: Path) -> None:
        self.project = project
        self.project_dir = project_dir
        self.state = PipelineState(project_dir)

    def run(self, resume: bool = True, cad_code: str | None = None) -> dict:
        """Execute the full pipeline.

        Args:
            resume: If True, skip stages already marked as completed.
            cad_code: Build123d code to use for geometry generation (if source=generate).

        Returns:
            Summary dict with per-stage results.
        """
        summary: dict = {"stages": {}}
        logger.info("Pipeline starting for project: %s", self.project.project.name)

        # Stage 1: CAD
        if not (resume and self.state.is_stage_complete("cad")):
            result = self._run_cad(cad_code)
            summary["stages"]["cad"] = {"success": result.success, "error": result.error}
            if not result.success:
                self.state.set_stage("cad", StageStatus.failed, error=result.error)
                logger.error("CAD stage failed: %s", result.error)
                return summary
            self.state.set_stage("cad", StageStatus.completed, summary=result.summary)
        else:
            logger.info("CAD stage already completed, skipping")

        gc.collect()

        # Stage 2: Mesh
        if not (resume and self.state.is_stage_complete("mesh")):
            result = self._run_mesh()
            summary["stages"]["mesh"] = {"success": result.success, "error": result.error}
            if not result.success:
                self.state.set_stage("mesh", StageStatus.failed, error=result.error)
                logger.error("Mesh stage failed: %s", result.error)
                return summary
            self.state.set_stage("mesh", StageStatus.completed, summary=result.summary)
        else:
            logger.info("Mesh stage already completed, skipping")

        gc.collect()

        # Stage 3+: Run jobs (FEA / CFD) sequentially — NEVER simultaneously
        for job in self.project.jobs:
            stage_name = job.type.value
            if resume and self.state.is_stage_complete(stage_name):
                logger.info("%s stage already completed, skipping", stage_name.upper())
                continue

            result = self._run_job(job)
            summary["stages"][stage_name] = {"success": result.success, "error": result.error}
            if not result.success:
                self.state.set_stage(stage_name, StageStatus.failed, error=result.error)
                logger.error("%s stage failed: %s", stage_name.upper(), result.error)
                return summary
            self.state.set_stage(stage_name, StageStatus.completed, summary=result.summary)

            gc.collect()  # Release solver memory before next stage

        # Stage: Results visualisation
        if not (resume and self.state.is_stage_complete("results")):
            result = self._run_results()
            summary["stages"]["results"] = {"success": result.success, "error": result.error}
            if result.success:
                self.state.set_stage("results", StageStatus.completed, summary=result.summary)
            else:
                logger.warning("Results stage failed (non-fatal): %s", result.error)
                self.state.set_stage("results", StageStatus.failed, error=result.error)

        gc.collect()
        logger.info("Pipeline completed for project: %s", self.project.project.name)
        return summary

    def _run_cad(self, cad_code: str | None) -> AdapterResult:
        """Run the appropriate CAD adapter based on geometry source."""
        source = self.project.geometry.source

        if source in (GeometrySource.upload, GeometrySource.freecad_gui):
            file_path = self.project.geometry.file
            if file_path is None:
                return AdapterResult(
                    success=False, error="geometry.file not set in project.yaml"
                )
            adapter = UploadAdapter(self.project, self.project_dir, Path(file_path))
        else:
            adapter = Build123dAdapter(self.project, self.project_dir)
            if cad_code:
                adapter.set_code(cad_code)
            elif not adapter.validate_inputs():
                return AdapterResult(
                    success=False,
                    error=(
                        "No CAD code provided and source=generate. "
                        "Use cad_code parameter or set geometry.file."
                    ),
                )

        if not adapter.validate_inputs():
            return AdapterResult(success=False, error="CAD adapter input validation failed")
        return adapter.run()

    def _run_mesh(self) -> AdapterResult:
        """Run the Gmsh meshing adapter."""
        adapter = GmshAdapter(self.project, self.project_dir)
        if not adapter.validate_inputs():
            return AdapterResult(success=False, error="Gmsh adapter: STEP file not found")
        result = adapter.run()
        if result.success and "surface_map" in result.summary:
            self.project.geometry.surface_map = result.summary["surface_map"]
        return result

    def _run_job(self, job) -> AdapterResult:
        """Run a single FEA or CFD job."""
        if job.type == JobType.fea:
            adapter = CalculixAdapter(self.project, self.project_dir, job)
        elif job.type == JobType.cfd:
            adapter = OpenFOAMAdapter(self.project, self.project_dir, job)
        else:
            return AdapterResult(success=False, error=f"Unknown job type: {job.type}")

        if not adapter.validate_inputs():
            return AdapterResult(
                success=False,
                error=f"{job.type.value} adapter input validation failed",
            )
        return adapter.run()

    def _run_results(self) -> AdapterResult:
        """Run the pyvista visualisation adapter."""
        adapter = PyvistaAdapter(self.project, self.project_dir)
        if not adapter.validate_inputs():
            return AdapterResult(
                success=False, error="No result files available for visualisation"
            )
        return adapter.run()
