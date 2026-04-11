"""Gmsh meshing adapter — converts STEP to .msh and extracts surface names."""

from __future__ import annotations

import gc
import json
import traceback
from pathlib import Path

from clawgeneer.adapters.base import AdapterResult, BaseAdapter
from clawgeneer.schema.project import ProjectConfig


class GmshAdapter(BaseAdapter):
    """Import a STEP file into Gmsh, mesh it, and output a v2.2 .msh file.

    Critical behaviours:
    - Forces Gmsh mesh format v2.2 (required by OpenFOAM's gmshToFoam)
    - Extracts surface entity names (from STEP metadata / Build123d labels)
    - Saves surface name -> gmsh tag mapping to surface_map.json
    """

    def __init__(self, project: ProjectConfig, project_dir: Path) -> None:
        self.project = project
        self.project_dir = project_dir
        self.mesh_dir = project_dir / "mesh"
        self.step_file = project_dir / "geometry" / "part.step"
        self.output_path = self.mesh_dir / "part.msh"
        self.surface_map_path = self.mesh_dir / "surface_map.json"

    def validate_inputs(self) -> bool:
        """Check that the STEP file exists."""
        return self.step_file.exists()

    def check_installed(self) -> bool:
        """Check that gmsh is importable."""
        try:
            import gmsh  # noqa: F401
            return True
        except ImportError:
            return False

    def run(self) -> AdapterResult:
        """Mesh the STEP file and save .msh + surface_map.json."""
        self.mesh_dir.mkdir(parents=True, exist_ok=True)

        try:
            import gmsh  # noqa: PLC0415

            gmsh.initialize()
            gmsh.model.add("part")

            # Import STEP — Build123d face labels are embedded here
            gmsh.model.occ.importShapes(str(self.step_file))
            gmsh.model.occ.synchronize()

            # Mesh settings
            mesh = self.project.mesh
            gmsh.option.setNumber("Mesh.CharacteristicLengthMax", mesh.element_size)
            gmsh.option.setNumber("Mesh.CharacteristicLengthMin", mesh.element_size * 0.1)
            gmsh.option.setNumber("Mesh.ElementOrder", mesh.element_order)
            gmsh.option.setNumber("Mesh.Algorithm", mesh.algorithm)

            # CRITICAL: Force v2.2 format for gmshToFoam compatibility
            gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)

            gmsh.model.mesh.generate(3)

            # Extract surface name -> tag mapping
            surface_map: dict[str, int] = {}
            for dim, tag in gmsh.model.getEntities(2):
                name = gmsh.model.getEntityName(dim, tag)
                if name:
                    surface_map[name] = tag

            # Save mesh
            gmsh.write(str(self.output_path))
            gmsh.finalize()

            # Persist surface map
            with open(self.surface_map_path, "w") as f:
                json.dump(surface_map, f, indent=2)

            # Update project config surface_map
            self.project.geometry.surface_map = surface_map

            summary = {
                "msh_file": str(self.output_path),
                "surface_map": surface_map,
                "num_surfaces": len(surface_map),
            }
            return AdapterResult(success=True, output_path=self.output_path, summary=summary)

        except Exception:
            try:
                import gmsh as _gmsh
                _gmsh.finalize()
            except Exception:
                pass
            return AdapterResult(
                success=False,
                error=f"Gmsh meshing failed:\n{traceback.format_exc()}",
            )
        finally:
            gc.collect()

    def parse_outputs(self) -> dict:
        """Return surface map and mesh file path."""
        if self.surface_map_path.exists():
            with open(self.surface_map_path) as f:
                return {"surface_map": json.load(f), "msh_file": str(self.output_path)}
        return {}
