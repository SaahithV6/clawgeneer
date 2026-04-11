"""pyvista results adapter — offscreen rendering of FEA/CFD results."""

from __future__ import annotations

import gc
import traceback
from pathlib import Path

from clawgeneer.adapters.base import AdapterResult, BaseAdapter
from clawgeneer.schema.project import ProjectConfig


class PyvistaAdapter(BaseAdapter):
    """Generate PNG visualisations from FEA (.vtu) or CFD (OpenFOAM) results.

    On a headless Ubuntu Server, pyvista requires Xvfb. The adapter calls
    ``pv.start_xvfb()`` before any rendering operation.
    """

    def __init__(self, project: ProjectConfig, project_dir: Path) -> None:
        self.project = project
        self.project_dir = project_dir
        self.results_dir = project_dir / "results"
        self.fea_vtu = project_dir / "fea" / "model.vtu"
        self.cfd_dir = project_dir / "cfd"

    def validate_inputs(self) -> bool:
        """Check that FEA or CFD result files exist."""
        return self.fea_vtu.exists() or self.cfd_dir.exists()

    def check_installed(self) -> bool:
        """Check that pyvista is importable."""
        try:
            import pyvista  # noqa: F401
            return True
        except ImportError:
            return False

    def run(self) -> AdapterResult:
        """Render available results to PNG files in the results directory."""
        self.results_dir.mkdir(parents=True, exist_ok=True)
        generated: list[str] = []
        errors: list[str] = []

        try:
            import pyvista as pv  # noqa: PLC0415
            # CRITICAL: start Xvfb for headless rendering on Ubuntu Server
            pv.start_xvfb()

            if self.fea_vtu.exists():
                try:
                    png = self._render_fea(pv)
                    generated.append(png)
                except Exception:
                    errors.append(f"FEA render failed: {traceback.format_exc()}")

            cfd_vtk = self._find_cfd_vtk()
            if cfd_vtk:
                try:
                    png = self._render_cfd(pv, cfd_vtk)
                    generated.append(png)
                except Exception:
                    errors.append(f"CFD render failed: {traceback.format_exc()}")

        except ImportError:
            return AdapterResult(success=False, error="pyvista not installed")
        except Exception:
            return AdapterResult(
                success=False,
                error=f"pyvista adapter error:\n{traceback.format_exc()}",
            )
        finally:
            gc.collect()

        if not generated:
            return AdapterResult(
                success=False,
                error="No result files rendered",
                logs="\n".join(errors),
            )

        return AdapterResult(
            success=True,
            output_path=self.results_dir,
            summary={"rendered": generated, "errors": errors},
        )

    def _render_fea(self, pv: object) -> str:
        """Render Von Mises stress contour from FEA .vtu file."""
        import pyvista as pv  # noqa: PLC0415

        mesh = pv.read(str(self.fea_vtu))
        plotter = pv.Plotter(off_screen=True)
        scalars = "S_Mises" if "S_Mises" in mesh.point_data else None
        plotter.add_mesh(mesh, scalars=scalars, cmap="jet", show_scalar_bar=True)
        plotter.add_title("FEA - Von Mises Stress")
        output = str(self.results_dir / "fea_stress.png")
        plotter.screenshot(output)
        plotter.close()
        return output

    def _render_cfd(self, pv: object, vtk_path: Path) -> str:
        """Render pressure field from CFD results."""
        import pyvista as pv  # noqa: PLC0415

        mesh = pv.read(str(vtk_path))
        plotter = pv.Plotter(off_screen=True)
        scalars = "p" if "p" in mesh.point_data else None
        plotter.add_mesh(mesh, scalars=scalars, cmap="coolwarm", show_scalar_bar=True)
        plotter.add_title("CFD - Pressure Field")
        output = str(self.results_dir / "cfd_pressure.png")
        plotter.screenshot(output)
        plotter.close()
        return output

    def _find_cfd_vtk(self) -> Path | None:
        """Find the latest OpenFOAM VTK output file."""
        vtk_dir = self.cfd_dir / "VTK"
        if not vtk_dir.exists():
            return None
        vtk_files = sorted(vtk_dir.glob("**/*.vtk"))
        return vtk_files[-1] if vtk_files else None

    def parse_outputs(self) -> dict:
        """Return list of rendered PNG files."""
        pngs = list(self.results_dir.glob("*.png"))
        return {"rendered_images": [str(p) for p in pngs]}
