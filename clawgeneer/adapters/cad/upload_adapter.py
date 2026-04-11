"""Upload adapter — intake for STEP/IGES/STL/FCStd files."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from clawgeneer.adapters.base import AdapterResult, BaseAdapter
from clawgeneer.schema.project import ProjectConfig

SUPPORTED_FORMATS = {".step", ".stp", ".iges", ".igs", ".stl", ".fcstd"}


class UploadAdapter(BaseAdapter):
    """Accept uploaded geometry files and normalise to STEP format.

    For .fcstd files, FreeCAD CLI is invoked to export to STEP.
    For .stl files, a warning is issued (mesh not B-Rep).
    """

    def __init__(
        self, project: ProjectConfig, project_dir: Path, source_file: Path
    ) -> None:
        self.project = project
        self.project_dir = project_dir
        self.source_file = source_file
        self.geometry_dir = project_dir / "geometry"
        self.output_path = self.geometry_dir / "part.step"

    def validate_inputs(self) -> bool:
        """Check the source file exists and is a supported format."""
        return (
            self.source_file.exists()
            and self.source_file.suffix.lower() in SUPPORTED_FORMATS
        )

    def check_installed(self) -> bool:
        """No extra tools needed unless converting .fcstd."""
        return True

    def run(self) -> AdapterResult:
        """Copy or convert geometry file to project geometry directory."""
        self.geometry_dir.mkdir(parents=True, exist_ok=True)
        suffix = self.source_file.suffix.lower()

        if suffix in (".step", ".stp", ".iges", ".igs"):
            shutil.copy2(self.source_file, self.output_path)
            return AdapterResult(
                success=True,
                output_path=self.output_path,
                summary={"source": str(self.source_file), "format": suffix},
            )

        if suffix == ".stl":
            stl_dest = self.geometry_dir / "part.stl"
            shutil.copy2(self.source_file, stl_dest)
            return AdapterResult(
                success=True,
                output_path=stl_dest,
                summary={
                    "source": str(self.source_file),
                    "format": ".stl",
                    "warning": "STL is a mesh format; B-Rep (STEP) is preferred for FEA/CFD",
                },
            )

        if suffix == ".fcstd":
            return self._convert_freecad()

        return AdapterResult(success=False, error=f"Unsupported format: {suffix}")

    def _convert_freecad(self) -> AdapterResult:
        """Convert FreeCAD .FCStd to STEP using freecadcmd."""
        script = (
            f"import FreeCAD, Import\n"
            f"FreeCAD.open('{self.source_file}')\n"
            f"Import.export(FreeCAD.ActiveDocument.Objects, '{self.output_path}')\n"
        )
        result = subprocess.run(
            ["freecadcmd", "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0 or not self.output_path.exists():
            return AdapterResult(
                success=False,
                error="FreeCAD conversion failed",
                logs=result.stderr,
            )
        return AdapterResult(
            success=True,
            output_path=self.output_path,
            summary={"source": str(self.source_file), "converted_from": ".fcstd"},
            logs=result.stdout,
        )

    def parse_outputs(self) -> dict:
        """Return summary of the output file."""
        if self.output_path.exists():
            return {"step_file": str(self.output_path)}
        return {}
