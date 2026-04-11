"""Tests for the Pydantic v2 project schema."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clawgeneer.schema.project import (
    BoundaryCondition,
    ComponentPlacement,
    Geometry,
    GeometrySource,
    GeometryTool,
    Job,
    JobType,
    Material,
    MeshSettings,
    Optimization,
    OptimizationObjective,
    ProjectConfig,
    ProjectMetadata,
    ProjectType,
)


class TestProjectMetadata:
    def test_defaults(self) -> None:
        meta = ProjectMetadata(name="test")
        assert meta.name == "test"
        assert meta.type == ProjectType.part
        assert meta.description == ""
        assert meta.version == "1"

    def test_assembly_type(self) -> None:
        meta = ProjectMetadata(name="asm", type=ProjectType.assembly)
        assert meta.type == ProjectType.assembly


class TestGeometry:
    def test_defaults(self) -> None:
        geo = Geometry()
        assert geo.source == GeometrySource.generate
        assert geo.tool == GeometryTool.build123d
        assert geo.parameters == {}
        assert geo.surface_map == {}
        assert geo.file is None

    def test_all_sources(self) -> None:
        for source in GeometrySource:
            geo = Geometry(source=source)
            assert geo.source == source

    def test_all_tools(self) -> None:
        for tool in GeometryTool:
            geo = Geometry(tool=tool)
            assert geo.tool == tool


class TestMaterial:
    def test_defaults(self) -> None:
        mat = Material()
        assert mat.name == "steel_mild"
        assert mat.youngs_modulus == 210000.0
        assert mat.poisson_ratio == 0.3
        assert mat.density == 7850.0
        assert mat.yield_strength == 250.0
        assert mat.thermal_conductivity is None
        assert mat.specific_heat is None

    def test_poisson_ratio_bounds(self) -> None:
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Material(poisson_ratio=0.5)  # lt=0.5, so 0.5 is invalid
        with pytest.raises(ValidationError):
            Material(poisson_ratio=-0.1)  # ge=0.0


class TestMeshSettings:
    def test_defaults(self) -> None:
        mesh = MeshSettings()
        assert mesh.tool == "gmsh"
        assert mesh.element_size == 3.0
        assert mesh.element_order == 1
        assert mesh.algorithm == 6

    def test_element_size_positive(self) -> None:
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            MeshSettings(element_size=0.0)
        with pytest.raises(ValidationError):
            MeshSettings(element_size=-1.0)

    def test_element_order_bounds(self) -> None:
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            MeshSettings(element_order=0)
        with pytest.raises(ValidationError):
            MeshSettings(element_order=3)


class TestJob:
    def test_fea_job(self) -> None:
        job = Job(type=JobType.fea, solver="calculix")
        assert job.type == JobType.fea
        assert job.solver == "calculix"
        assert job.boundary_conditions == []

    def test_cfd_job(self) -> None:
        job = Job(type=JobType.cfd, solver="openfoam")
        assert job.type == JobType.cfd

    def test_job_with_bcs(self) -> None:
        bc_fixed = BoundaryCondition(surface="bottom_face", type="fixed")
        bc_force = BoundaryCondition(
            surface="top_face",
            type="force",
            magnitude=5000.0,
            direction=[0.0, -1.0, 0.0],
        )
        job = Job(type=JobType.fea, solver="calculix", boundary_conditions=[bc_fixed, bc_force])
        assert len(job.boundary_conditions) == 2
        assert job.boundary_conditions[0].surface == "bottom_face"
        assert job.boundary_conditions[1].magnitude == 5000.0


class TestOptimization:
    def test_defaults(self) -> None:
        opt = Optimization()
        assert opt.enabled is False
        assert opt.objective == OptimizationObjective.minimize_mass
        assert opt.max_iterations == 20
        assert opt.sampler == "tpe"

    def test_all_objectives(self) -> None:
        for obj in OptimizationObjective:
            opt = Optimization(objective=obj)
            assert opt.objective == obj


class TestProjectConfig:
    def test_minimal_valid(self) -> None:
        config = ProjectConfig(project=ProjectMetadata(name="test_project"))
        assert config.project.name == "test_project"
        assert config.components == []
        assert config.jobs == []

    def test_components_default_empty(self) -> None:
        config = ProjectConfig(project=ProjectMetadata(name="p"))
        assert isinstance(config.components, list)
        assert len(config.components) == 0

    def test_model_dump_json(self) -> None:
        config = ProjectConfig(project=ProjectMetadata(name="dump_test"))
        data = config.model_dump(mode="json")
        assert data["project"]["name"] == "dump_test"
        assert "geometry" in data
        assert "material" in data
        assert "mesh" in data

    def test_from_yaml(self, tmp_path: Path) -> None:
        """Test loading a project config from a YAML file."""
        yaml_content = {
            "project": {"name": "yaml_test", "type": "part"},
            "geometry": {
                "source": "generate",
                "tool": "build123d",
                "parameters": {"length": 100, "width": 60, "thickness": 8},
            },
            "material": {
                "name": "steel_mild",
                "youngs_modulus": 210000,
                "poisson_ratio": 0.3,
                "density": 7850,
                "yield_strength": 250,
            },
            "mesh": {"tool": "gmsh", "element_size": 3.0, "element_order": 1, "algorithm": 6},
            "jobs": [
                {
                    "type": "fea",
                    "solver": "calculix",
                    "boundary_conditions": [
                        {"surface": "bottom_face", "type": "fixed"},
                        {
                            "surface": "top_face",
                            "type": "force",
                            "magnitude": 5000,
                            "direction": [0, -1, 0],
                        },
                    ],
                }
            ],
            "results": {},
            "optimization": {"enabled": False},
            "components": [],
        }

        yaml_path = tmp_path / "project.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump(yaml_content, f)

        config = ProjectConfig.from_yaml(yaml_path)
        assert config.project.name == "yaml_test"
        assert config.geometry.parameters["length"] == 100
        assert len(config.jobs) == 1
        assert config.jobs[0].type == JobType.fea
        assert len(config.jobs[0].boundary_conditions) == 2

    def test_to_yaml_roundtrip(self, tmp_path: Path) -> None:
        """Test that to_yaml -> from_yaml is a lossless roundtrip."""
        original = ProjectConfig(
            project=ProjectMetadata(name="roundtrip"),
            geometry=Geometry(parameters={"length": 50, "width": 30}),
            material=Material(youngs_modulus=70000.0),
        )

        yaml_path = tmp_path / "roundtrip.yaml"
        original.to_yaml(yaml_path)
        loaded = ProjectConfig.from_yaml(yaml_path)

        assert loaded.project.name == "roundtrip"
        assert loaded.geometry.parameters["length"] == 50
        assert loaded.material.youngs_modulus == 70000.0

    def test_template_yaml_valid(self) -> None:
        """Verify the shipped template project.yaml is valid."""
        template = (
            Path(__file__).parents[1]
            / "clawgeneer"
            / "schema"
            / "templates"
            / "project.yaml"
        )
        assert template.exists(), f"Template not found at {template}"
        config = ProjectConfig.from_yaml(template)
        assert config.project.name == "my_project"
        assert config.geometry.tool == GeometryTool.build123d
