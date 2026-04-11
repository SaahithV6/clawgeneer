"""CadQuery CAD adapter — secondary/fallback CAD backend."""

from __future__ import annotations

import gc
import traceback
from pathlib import Path
from typing import Any

from clawgeneer.adapters.base import AdapterResult, BaseAdapter
from clawgeneer.schema.project import ProjectConfig


CADQUERY_SYSTEM_PROMPT = """You are a Python CAD code generator using CadQuery.

RULES:
1. Tag ALL faces used in boundary conditions: .faces(">Z").tag("top_face")
2. Units are ALWAYS millimetres
3. Return ONLY JSON: {"code": "<python code string>"}
4. Code must define make_part(**kwargs) returning a CadQuery Workplane

NOTE: CadQuery tags can be lost after Boolean ops. Prefer simple geometry. For complex
multi-body parts, use the build123d adapter instead.
"""


class CadQueryAdapter(BaseAdapter):
    """Fallback CadQuery adapter. Use Build123dAdapter as the primary CAD adapter."""

    def __init__(self, project: ProjectConfig, project_dir: Path) -> None:
        self.project = project
        self.project_dir = project_dir
        self.geometry_dir = project_dir / "geometry"
        self.output_path = self.geometry_dir / "part.step"
        self._code: str | None = None

    def set_code(self, code: str) -> None:
        """Set the LLM-generated CadQuery code to execute."""
        self._code = code

    def validate_inputs(self) -> bool:
        """Check that generated code has been provided."""
        return self._code is not None

    def check_installed(self) -> bool:
        """Check that cadquery is importable."""
        try:
            import cadquery  # noqa: F401
            return True
        except ImportError:
            return False

    def run(self) -> AdapterResult:
        """Execute CadQuery code and export STEP."""
        self.geometry_dir.mkdir(parents=True, exist_ok=True)
        if not self._code:
            return AdapterResult(success=False, error="No CadQuery code provided")

        try:
            namespace: dict[str, Any] = {}
            exec(self._code, namespace)  # noqa: S102

            if "make_part" not in namespace:
                return AdapterResult(success=False, error="Code must define make_part()")

            params = self.project.geometry.parameters
            result = namespace["make_part"](**params)

            # Validate solid
            if result.val().isNull():
                return AdapterResult(success=False, error="make_part() produced null solid")
            if not result.val().isValid():
                return AdapterResult(success=False, error="make_part() produced invalid solid")
            if not result.val().isSolid():
                return AdapterResult(success=False, error="make_part() result is not a solid")

            result.val().exportStep(str(self.output_path))
            return AdapterResult(
                success=True,
                output_path=self.output_path,
                summary={"step_file": str(self.output_path)},
                logs=self._code,
            )
        except Exception:
            return AdapterResult(
                success=False,
                error=f"CadQuery execution failed:\n{traceback.format_exc()}",
                logs=self._code or "",
            )
        finally:
            gc.collect()

    def parse_outputs(self) -> dict:
        """Return summary of the generated STEP file."""
        if self.output_path.exists():
            return {"step_file": str(self.output_path)}
        return {}
