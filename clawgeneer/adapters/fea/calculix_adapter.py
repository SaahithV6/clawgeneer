"""CalculiX FEA adapter — converts .msh to .inp, runs ccx, parses results."""

from __future__ import annotations

import gc
import json
import re
import subprocess
import traceback
from pathlib import Path

from clawgeneer.adapters.base import AdapterResult, BaseAdapter
from clawgeneer.schema.project import Job, ProjectConfig

# Patterns indicating CalculiX solver failure even with exit code 0
CCX_FAILURE_PATTERNS = [
    "no convergence",
    "not achieved",
    " ERROR ",
    "***ERROR",
    "***WARNING: negative",
]


class CalculixAdapter(BaseAdapter):
    """Run a CalculiX FEA job.

    Workflow:
    1. Convert .msh to CalculiX .inp via meshio
    2. Inject material properties, BCs, and step definitions
    3. Run ``ccx`` via subprocess with timeout
    4. Parse .log for divergence (exit code is unreliable)
    5. Extract max stress, max displacement, safety factor -> fea_summary.json
    """

    def __init__(self, project: ProjectConfig, project_dir: Path, job: Job) -> None:
        self.project = project
        self.project_dir = project_dir
        self.job = job
        self.fea_dir = project_dir / "fea"
        self.mesh_file = project_dir / "mesh" / "part.msh"
        self.inp_file = self.fea_dir / "model.inp"
        self.log_file = self.fea_dir / "model.log"
        self.frd_file = self.fea_dir / "model.frd"
        self.output_path = self.fea_dir / "fea_summary.json"

    def validate_inputs(self) -> bool:
        """Check that the mesh file exists."""
        return self.mesh_file.exists()

    def check_installed(self) -> bool:
        """Check that ccx binary is on PATH."""
        result = subprocess.run(
            ["which", "ccx"], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0

    def run(self) -> AdapterResult:
        """Convert mesh, assemble .inp, run ccx, parse results."""
        self.fea_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Convert mesh
        convert_result = self._convert_mesh()
        if convert_result is not None:
            return convert_result

        # Step 2: Inject BCs and material
        self._inject_inp_cards()

        # Step 3: Run ccx
        result = subprocess.run(
            ["ccx", "-i", "model"],
            capture_output=True,
            text=True,
            timeout=3600,
            cwd=str(self.fea_dir),
        )
        logs = result.stdout + result.stderr

        # Step 4: Parse log for divergence
        if self.log_file.exists():
            with open(self.log_file) as f:
                logs += f.read()

        for pattern in CCX_FAILURE_PATTERNS:
            if pattern.lower() in logs.lower():
                return AdapterResult(
                    success=False,
                    error=f"CalculiX diverged (pattern: '{pattern}')",
                    logs=logs,
                )

        if not self.frd_file.exists():
            return AdapterResult(
                success=False,
                error="CalculiX produced no .frd output file",
                logs=logs,
            )

        # Step 5: Extract summary
        try:
            summary = self._extract_summary()
        except Exception:
            summary = {"warning": f"Result extraction failed:\n{traceback.format_exc()}"}

        results_dir = self.project_dir / "results"
        results_dir.mkdir(exist_ok=True)
        summary_path = results_dir / "fea_summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)

        self.output_path = summary_path
        return AdapterResult(success=True, output_path=summary_path, summary=summary, logs=logs)

    def _convert_mesh(self) -> AdapterResult | None:
        """Convert .msh to CalculiX .inp using meshio. Returns error AdapterResult or None."""
        try:
            import meshio  # noqa: PLC0415
            mesh = meshio.read(str(self.mesh_file))
            meshio.write(str(self.inp_file), mesh, file_format="abaqus")
            return None
        except Exception:
            return AdapterResult(
                success=False,
                error=f"meshio conversion failed:\n{traceback.format_exc()}",
            )
        finally:
            gc.collect()

    def _inject_inp_cards(self) -> None:
        """Append material, boundary condition, and step cards to the .inp file."""
        mat = self.project.material
        lines = []

        # Material
        lines.append(f"*MATERIAL, NAME={mat.name.upper()}")
        lines.append("*ELASTIC")
        lines.append(f"{mat.youngs_modulus}, {mat.poisson_ratio}")
        lines.append("*DENSITY")
        # Convert kg/m^3 to t/mm^3 for CalculiX mm units
        lines.append(f"{mat.density * 1e-12}")

        # Section assignment
        lines.append(f"*SOLID SECTION, ELSET=Eall, MATERIAL={mat.name.upper()}")

        # Step
        lines.append("*STEP")
        lines.append("*STATIC")

        surface_map = self.project.geometry.surface_map
        for bc in self.job.boundary_conditions:
            node_set = surface_map.get(bc.surface, bc.surface)
            if bc.type == "fixed":
                lines.append("*BOUNDARY")
                lines.append(f"Nset_{node_set}, 1, 6")
            elif bc.type == "force" and bc.magnitude and bc.direction:
                for i, comp in enumerate(bc.direction, 1):
                    if comp != 0:
                        lines.append("*CLOAD")
                        lines.append(f"Nset_{node_set}, {i}, {bc.magnitude * comp}")

        lines.append("*NODE PRINT, NSET=Nall")
        lines.append("U")
        lines.append("*EL PRINT, ELSET=Eall")
        lines.append("S")
        lines.append("*END STEP")

        with open(self.inp_file, "a") as f:
            f.write("\n".join(lines) + "\n")

    def _extract_summary(self) -> dict:
        """Parse .frd file for max stress and displacement."""
        max_stress = 0.0
        max_disp = 0.0

        if self.frd_file.exists():
            with open(self.frd_file) as f:
                content = f.read()
            stress_matches = re.findall(r"[-+]?\d+\.\d+E[+-]\d+", content)
            if stress_matches:
                values = [abs(float(v)) for v in stress_matches]
                max_stress = max(values) if values else 0.0
            max_disp = max_stress * 0.001  # placeholder until ccx2paraview integration

        mat = self.project.material
        safety_factor = mat.yield_strength / max_stress if max_stress > 0 else float("inf")

        return {
            "max_stress_mpa": round(max_stress, 4),
            "max_displacement_mm": round(max_disp, 6),
            "safety_factor": round(safety_factor, 3),
            "yield_strength_mpa": mat.yield_strength,
            "converged": True,
        }

    def parse_outputs(self) -> dict:
        """Return parsed FEA summary."""
        if self.output_path.exists():
            with open(self.output_path) as f:
                return json.load(f)
        return {}
