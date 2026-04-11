"""Pydantic v2 schema for ClawGeneer project.yaml files."""

from __future__ import annotations

import yaml
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field, model_validator


class GeometrySource(str, Enum):
    generate = "generate"
    upload = "upload"
    freecad_gui = "freecad_gui"


class GeometryTool(str, Enum):
    build123d = "build123d"
    cadquery = "cadquery"
    freecad = "freecad"


class ProjectType(str, Enum):
    part = "part"
    assembly = "assembly"


class Geometry(BaseModel):
    source: GeometrySource = GeometrySource.generate
    tool: GeometryTool = GeometryTool.build123d
    parameters: dict[str, Any] = Field(default_factory=dict)
    surface_map: dict[str, int] = Field(default_factory=dict)  # name -> gmsh tag
    file: Optional[Path] = None  # for upload/freecad_gui source


class Material(BaseModel):
    name: str = "steel_mild"
    youngs_modulus: float = Field(210000.0, description="MPa")
    poisson_ratio: float = Field(0.3, ge=0.0, lt=0.5)
    density: float = Field(7850.0, description="kg/m^3")
    yield_strength: float = Field(250.0, description="MPa")
    thermal_conductivity: Optional[float] = None  # W/(m·K), for thermal jobs
    specific_heat: Optional[float] = None  # J/(kg·K)


class MeshSettings(BaseModel):
    tool: str = "gmsh"
    element_size: float = Field(3.0, gt=0.0, description="mm")
    element_order: int = Field(1, ge=1, le=2)
    algorithm: int = Field(6, description="Gmsh meshing algorithm (default: Frontal-Delaunay)")


class BoundaryCondition(BaseModel):
    surface: str  # references surface_map key
    type: str  # fixed, force, pressure, velocity, inlet, outlet, wall, symmetry
    magnitude: Optional[float] = None
    direction: Optional[list[float]] = None  # [x, y, z] unit vector
    value: Optional[Any] = None  # for CFD BCs (velocity vector, pressure value)


class JobType(str, Enum):
    fea = "fea"
    cfd = "cfd"


class Job(BaseModel):
    type: JobType
    solver: str  # "calculix" or "openfoam"
    name: Optional[str] = None
    boundary_conditions: list[BoundaryCondition] = Field(default_factory=list)
    solver_settings: dict[str, Any] = Field(default_factory=dict)


class OptimizationObjective(str, Enum):
    minimize_mass = "minimize_mass"
    minimize_max_stress = "minimize_max_stress"
    minimize_drag = "minimize_drag"
    maximize_safety_factor = "maximize_safety_factor"


class Optimization(BaseModel):
    enabled: bool = False
    objective: OptimizationObjective = OptimizationObjective.minimize_mass
    constraints: dict[str, float] = Field(default_factory=dict)
    parameter_bounds: dict[str, list[float]] = Field(default_factory=dict)
    max_iterations: int = 20
    sampler: str = "tpe"  # optuna sampler


class ComponentPlacement(BaseModel):
    """Assembly component placement for future Phase 11."""

    file: Path
    material: Material = Field(default_factory=Material)
    position: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    orientation: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0]
    )  # Euler angles degrees


class ProjectMetadata(BaseModel):
    name: str
    type: ProjectType = ProjectType.part
    description: str = ""
    version: str = "1"


class ProjectConfig(BaseModel):
    """Top-level project configuration. Loaded from project.yaml."""

    project: ProjectMetadata
    geometry: Geometry = Field(default_factory=Geometry)
    material: Material = Field(default_factory=Material)
    mesh: MeshSettings = Field(default_factory=MeshSettings)
    jobs: list[Job] = Field(default_factory=list)
    results: dict[str, Any] = Field(default_factory=dict)
    optimization: Optimization = Field(default_factory=Optimization)
    components: list[ComponentPlacement] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> "ProjectConfig":
        """Load and validate a project.yaml file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    def to_yaml(self, path: Path) -> None:
        """Serialise the config back to YAML."""
        with open(path, "w") as f:
            yaml.dump(
                self.model_dump(mode="json"),
                f,
                default_flow_style=False,
                sort_keys=False,
            )
