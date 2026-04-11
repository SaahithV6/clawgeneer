"""OpenFOAM CFD adapter — uses ESI v2312 (openfoam.com), simpleFoam."""

from __future__ import annotations

import gc
import json
import re
import shutil
import subprocess
import traceback
from pathlib import Path

from clawgeneer.adapters.base import AdapterResult, BaseAdapter
from clawgeneer.schema.project import Job, ProjectConfig

# Path to OpenFOAM ESI v2312 environment script
OPENFOAM_BASHRC = Path("/usr/lib/openfoam/openfoam2312/etc/bashrc")

# OpenFOAM patch types from BC type names in project.yaml
BC_TYPE_MAP = {
    "wall": "wall",
    "inlet": "patch",
    "outlet": "patch",
    "symmetry": "symmetryPlane",
    "fixed": "wall",
}


def _foam_cmd(cmd: str, cwd: Path, timeout: int = 3600) -> tuple[int, str]:
    """Run an OpenFOAM command with the ESI environment sourced."""
    bash_cmd = f"source {OPENFOAM_BASHRC} && {cmd}"
    result = subprocess.run(
        ["bash", "-c", bash_cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd),
    )
    return result.returncode, result.stdout + result.stderr


class OpenFOAMAdapter(BaseAdapter):
    """Run an OpenFOAM CFD job using simpleFoam (steady incompressible RANS).

    Workflow:
    1. Copy case templates from repo's templates/openfoam/
    2. Run gmshToFoam to convert .msh
    3. Post-process boundary file to fix patch types
    4. Run simpleFoam
    5. Parse log.simpleFoam for residuals
    6. Extract summary -> cfd_summary.json
    """

    def __init__(self, project: ProjectConfig, project_dir: Path, job: Job) -> None:
        self.project = project
        self.project_dir = project_dir
        self.job = job
        self.cfd_dir = project_dir / "cfd"
        self.mesh_file = project_dir / "mesh" / "part.msh"
        self.log_file = self.cfd_dir / "log.simpleFoam"
        self.output_path = project_dir / "results" / "cfd_summary.json"

        # Templates directory (repo root / templates / openfoam)
        self._templates_dir = Path(__file__).parents[4] / "templates" / "openfoam"

    def validate_inputs(self) -> bool:
        """Check that the mesh file exists."""
        return self.mesh_file.exists()

    def check_installed(self) -> bool:
        """Check that simpleFoam is callable via the ESI environment."""
        rc, _ = _foam_cmd("simpleFoam -help", self.project_dir, timeout=30)
        return rc == 0

    def run(self) -> AdapterResult:
        """Set up case, run simpleFoam, parse results."""
        try:
            self.cfd_dir.mkdir(parents=True, exist_ok=True)
            (self.project_dir / "results").mkdir(exist_ok=True)

            # 1. Copy templates
            self._setup_case()

            # 2. Run gmshToFoam
            rc, logs = _foam_cmd(
                f"gmshToFoam {self.mesh_file} -case {self.cfd_dir}",
                self.project_dir,
                timeout=300,
            )
            if rc != 0:
                return AdapterResult(success=False, error="gmshToFoam failed", logs=logs)

            # 3. Fix patch types
            self._fix_boundary_types()

            # 4. Run simpleFoam
            rc, solve_logs = _foam_cmd("simpleFoam", self.cfd_dir, timeout=7200)
            all_logs = logs + solve_logs

            # 5. Check convergence
            if self.log_file.exists():
                with open(self.log_file) as f:
                    all_logs += f.read()

            conv_error = self._check_convergence(all_logs)
            if conv_error:
                return AdapterResult(success=False, error=conv_error, logs=all_logs)

            # 6. Extract summary
            summary = self._extract_summary(all_logs)
            with open(self.output_path, "w") as f:
                json.dump(summary, f, indent=2)

            return AdapterResult(
                success=True, output_path=self.output_path, summary=summary, logs=all_logs
            )

        except Exception:
            return AdapterResult(
                success=False,
                error=f"OpenFOAM adapter error:\n{traceback.format_exc()}",
            )
        finally:
            gc.collect()

    def _setup_case(self) -> None:
        """Copy OpenFOAM case templates to the cfd directory."""
        if self._templates_dir.exists():
            for item in self._templates_dir.iterdir():
                dest = self.cfd_dir / item.name
                if item.is_dir() and not dest.exists():
                    shutil.copytree(item, dest)
                elif item.is_file() and not dest.exists():
                    shutil.copy2(item, dest)

    def _fix_boundary_types(self) -> None:
        """Post-process constant/polyMesh/boundary to set correct patch types.

        gmshToFoam sets everything to 'patch'. Wall functions only apply to 'wall' type.
        """
        boundary_file = self.cfd_dir / "constant" / "polyMesh" / "boundary"
        if not boundary_file.exists():
            return

        with open(boundary_file) as f:
            content = f.read()

        bc_types: dict[str, str] = {}
        for bc in self.job.boundary_conditions:
            foam_type = BC_TYPE_MAP.get(bc.type, "patch")
            bc_types[bc.surface] = foam_type

        # Replace patch type for known surfaces
        for surface_name, patch_type in bc_types.items():
            content = re.sub(
                rf"({re.escape(surface_name)}\s*\{{[^}}]*type\s+)patch",
                rf"\g<1>{patch_type}",
                content,
            )

        with open(boundary_file, "w") as f:
            f.write(content)

    @staticmethod
    def _check_convergence(logs: str) -> str | None:
        """Return error string if diverged, else None."""
        if "nan" in logs.lower() or "inf" in logs.lower():
            return "OpenFOAM diverged: NaN/Inf detected in residuals"

        residual_pattern = re.compile(
            r"GAMG.*?final residual = ([\d.eE+\-]+)", re.IGNORECASE
        )
        simple_pattern = re.compile(
            r"smoothSolver.*?Final residual: ([\d.eE+\-]+)", re.IGNORECASE
        )

        all_residuals: list[float] = []
        for pattern in (residual_pattern, simple_pattern):
            for match in pattern.finditer(logs):
                try:
                    all_residuals.append(float(match.group(1)))
                except ValueError:
                    pass

        if all_residuals and max(all_residuals) > 1e-2:
            return (
                f"OpenFOAM residuals too high: max={max(all_residuals):.3e} (threshold 1e-2)"
            )
        return None

    @staticmethod
    def _extract_summary(logs: str) -> dict:
        """Extract key CFD metrics from solver logs."""
        p_residuals = re.findall(r"p.*?Final residual:\s*([\d.eE+\-]+)", logs, re.IGNORECASE)
        u_residuals = re.findall(
            r"Ux.*?Final residual:\s*([\d.eE+\-]+)", logs, re.IGNORECASE
        )

        return {
            "converged": True,
            "final_p_residual": float(p_residuals[-1]) if p_residuals else None,
            "final_U_residual": float(u_residuals[-1]) if u_residuals else None,
        }

    def parse_outputs(self) -> dict:
        """Return parsed CFD summary."""
        if self.output_path.exists():
            with open(self.output_path) as f:
                return json.load(f)
        return {}
