"""Build123d CAD adapter — PRIMARY CAD backend for ClawGeneer."""

from __future__ import annotations

import gc
import shutil
import traceback
from pathlib import Path
from typing import Any

from clawgeneer.adapters.base import AdapterResult, BaseAdapter
from clawgeneer.schema.project import ProjectConfig


# System prompt used when generating Build123d code via LLM
BUILD123D_SYSTEM_PROMPT = """You are a Python CAD code generator. Generate valid Build123d Python code.

RULES:
1. Use Build123d syntax with context managers: `with BuildPart() as part:`
2. Label ALL faces that will be used as boundary conditions with `.label = "name"`
3. Units are ALWAYS millimetres
4. Return ONLY a JSON object with a single key "code" containing the Python code string
5. The code must define a `make_part()` function that returns a Build123d Part object
6. Face labels must match the surface names in the project boundary conditions

EXAMPLE:
```json
{"code": "from build123d import *\\n\\ndef make_part(length=100, width=60, thickness=8):\\n    with BuildPart() as p:\\n        Box(length, width, thickness)\\n        faces().filter_by(Axis.Z).last.label = 'top_face'\\n        faces().filter_by(Axis.Z).first.label = 'bottom_face'\\n    return p.part\\n"}
```
"""


class Build123dAdapter(BaseAdapter):
    """Adapter for Build123d CAD geometry generation and validation.

    Supports two modes:
    - ``source='generate'``: Execute LLM-generated Build123d code
    - ``source='upload'``: Validate and copy an existing STEP file
    """

    def __init__(self, project: ProjectConfig, project_dir: Path) -> None:
        self.project = project
        self.project_dir = project_dir
        self.geometry_dir = project_dir / "geometry"
        self.output_path = self.geometry_dir / "part.step"
        self._code: str | None = None

    def set_code(self, code: str) -> None:
        """Set the LLM-generated Build123d code to execute."""
        self._code = code

    def validate_inputs(self) -> bool:
        """Check that inputs are ready for the current geometry source."""
        source = self.project.geometry.source.value
        if source == "generate":
            return self._code is not None
        if source in ("upload", "freecad_gui"):
            return (
                self.project.geometry.file is not None
                and Path(self.project.geometry.file).exists()
            )
        return False

    def check_installed(self) -> bool:
        """Check that build123d is importable."""
        try:
            import build123d  # noqa: F401
            return True
        except ImportError:
            return False

    def run(self) -> AdapterResult:
        """Execute build123d code and export STEP, or copy uploaded file."""
        self.geometry_dir.mkdir(parents=True, exist_ok=True)
        source = self.project.geometry.source.value

        if source == "generate":
            return self._run_generated()
        if source in ("upload", "freecad_gui"):
            return self._run_upload()
        return AdapterResult(success=False, error=f"Unknown geometry source: {source}")

    def _run_generated(self) -> AdapterResult:
        """Execute LLM-generated Build123d code with validation."""
        if not self._code:
            return AdapterResult(success=False, error="No Build123d code provided")

        try:
            namespace: dict[str, Any] = {}
            exec(self._code, namespace)  # noqa: S102

            if "make_part" not in namespace:
                return AdapterResult(
                    success=False,
                    error="Generated code must define a make_part() function",
                )

            # Call make_part with geometry parameters
            params = self.project.geometry.parameters
            part = namespace["make_part"](**params)

            # Validate result
            validation_error = self._validate_solid(part)
            if validation_error:
                return AdapterResult(success=False, error=validation_error, logs=self._code)

            # Export to STEP
            from build123d import export_step  # noqa: PLC0415
            export_step(part, str(self.output_path))

            # Verify exported file can be re-imported
            re_import_error = self._verify_step_export(self.output_path)
            if re_import_error:
                return AdapterResult(success=False, error=re_import_error, logs=self._code)

            summary = {"step_file": str(self.output_path), "source": "generated"}
            return AdapterResult(
                success=True, output_path=self.output_path, summary=summary, logs=self._code
            )

        except Exception:
            return AdapterResult(
                success=False,
                error=f"Build123d execution failed:\n{traceback.format_exc()}",
                logs=self._code or "",
            )
        finally:
            gc.collect()

    def _run_upload(self) -> AdapterResult:
        """Copy and validate an uploaded STEP/IGES/STL file."""
        source_file = Path(self.project.geometry.file)
        if not source_file.exists():
            return AdapterResult(success=False, error=f"Source file not found: {source_file}")

        suffix = source_file.suffix.lower()
        if suffix in (".step", ".stp", ".iges", ".igs"):
            shutil.copy2(source_file, self.output_path)
            return AdapterResult(
                success=True,
                output_path=self.output_path,
                summary={"step_file": str(self.output_path), "source": "upload"},
            )
        if suffix == ".stl":
            shutil.copy2(source_file, self.output_path.with_suffix(".stl"))
            return AdapterResult(
                success=True,
                output_path=self.output_path.with_suffix(".stl"),
                summary={
                    "source": "upload",
                    "warning": "STL is a mesh format; B-Rep preferred",
                },
            )
        return AdapterResult(success=False, error=f"Unsupported file format: {suffix}")

    @staticmethod
    def _validate_solid(part: Any) -> str | None:
        """Return an error string if the solid is invalid, else None."""
        try:
            if part is None:
                return "make_part() returned None"
            if hasattr(part, "solids"):
                solids = part.solids()
                if not solids:
                    return "make_part() produced no solid bodies"
            return None
        except Exception:
            return f"Solid validation error:\n{traceback.format_exc()}"

    @staticmethod
    def _verify_step_export(step_path: Path) -> str | None:
        """Re-import STEP file to catch OCC export bugs. Returns error string or None."""
        try:
            import gmsh  # noqa: PLC0415
            gmsh.initialize()
            gmsh.model.add("verify")
            gmsh.model.occ.importShapes(str(step_path))
            gmsh.model.occ.synchronize()
            vols = gmsh.model.getEntities(3)
            gmsh.finalize()
            if not vols:
                return "STEP export produced no 3D volumes when re-imported"
            return None
        except Exception:
            try:
                gmsh.finalize()
            except Exception:
                pass
            return f"STEP re-import verification failed:\n{traceback.format_exc()}"

    def parse_outputs(self) -> dict:
        """Return summary of the generated STEP file."""
        if self.output_path.exists():
            return {
                "step_file": str(self.output_path),
                "size_bytes": self.output_path.stat().st_size,
            }
        return {}
