"""Tests for ToolManager and the tool registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clawgeneer.tools.tool_manager import ToolManager


class TestToolManagerRegistry:
    def test_registry_loads(self) -> None:
        """Registry file should load without errors."""
        manager = ToolManager()
        assert manager._registry is not None
        assert "tools" in manager._registry

    def test_registry_has_expected_tools(self) -> None:
        """All critical tools should be present in the registry."""
        manager = ToolManager()
        tools = manager._registry["tools"]
        expected = [
            "build123d",
            "cadquery",
            "gmsh",
            "meshio",
            "calculix",
            "openfoam",
            "freecad",
            "pyvista",
            "ccx2paraview",
            "ollama",
            "foamlib",
        ]
        for tool in expected:
            assert tool in tools, f"'{tool}' not found in registry"

    def test_tool_has_required_fields(self) -> None:
        """Each tool entry should have at least a description."""
        manager = ToolManager()
        for name, info in manager._registry["tools"].items():
            assert "description" in info, f"Tool '{name}' missing 'description' field"

    def test_custom_registry_path(self, tmp_path: Path) -> None:
        """ToolManager should accept a custom registry path."""
        custom_registry = tmp_path / "custom_registry.yaml"
        custom_registry.write_text(
            "tools:\n  my_tool:\n    check_python: os\n    description: test tool\n    install_cmd: pip install os\n"
        )
        manager = ToolManager(registry_path=custom_registry)
        assert "my_tool" in manager._registry["tools"]


class TestCheckInstalled:
    def test_python_module_installed(self) -> None:
        """A module that definitely exists (yaml) should return True."""
        manager = ToolManager()
        # Inject a fake entry for 'yaml' which is always available
        manager._registry["tools"]["_test_yaml"] = {
            "check_python": "yaml",
            "description": "test",
        }
        assert manager.check_installed("_test_yaml") is True

    def test_python_module_missing(self) -> None:
        """A module that does not exist should return False."""
        manager = ToolManager()
        manager._registry["tools"]["_test_missing"] = {
            "check_python": "nonexistent_module_xyz_abc_123",
            "description": "test",
        }
        assert manager.check_installed("_test_missing") is False

    def test_binary_installed_via_which(self) -> None:
        """A binary that exists on PATH (python3) should return True."""
        manager = ToolManager()
        manager._registry["tools"]["_test_python3"] = {
            "check_binary": "python3",
            "description": "test",
        }
        assert manager.check_installed("_test_python3") is True

    def test_binary_missing(self) -> None:
        """A binary that does not exist should return False."""
        manager = ToolManager()
        manager._registry["tools"]["_test_nope"] = {
            "check_binary": "nonexistent_binary_xyz_abc",
            "description": "test",
        }
        assert manager.check_installed("_test_nope") is False

    def test_unknown_tool_returns_false(self) -> None:
        manager = ToolManager()
        assert manager.check_installed("tool_that_does_not_exist") is False

    def test_tool_no_check_returns_false(self) -> None:
        """Tool with no check_python or check_binary should return False."""
        manager = ToolManager()
        manager._registry["tools"]["_test_no_check"] = {
            "description": "no check method",
        }
        assert manager.check_installed("_test_no_check") is False

    def test_check_path_existing(self, tmp_path: Path) -> None:
        """check_path should return True when the path exists."""
        existing_file = tmp_path / "mybinary"
        existing_file.touch()
        manager = ToolManager()
        manager._registry["tools"]["_test_path_exists"] = {
            "check_path": str(existing_file),
            "description": "test path check",
        }
        assert manager.check_installed("_test_path_exists") is True

    def test_check_path_missing(self, tmp_path: Path) -> None:
        """check_path should return False when the path does not exist."""
        manager = ToolManager()
        manager._registry["tools"]["_test_path_missing"] = {
            "check_path": str(tmp_path / "nonexistent_binary"),
            "description": "test path check missing",
        }
        assert manager.check_installed("_test_path_missing") is False

    def test_source_cmd_binary_found(self) -> None:
        """source_cmd + check_binary should return True when subprocess succeeds."""
        manager = ToolManager()
        manager._registry["tools"]["_test_sourced"] = {
            "check_binary": "python3",
            "source_cmd": "true",  # 'true' is always available; sources a no-op
            "description": "test sourced binary",
        }
        assert manager.check_installed("_test_sourced") is True

    def test_source_cmd_binary_missing(self) -> None:
        """source_cmd + check_binary should return False when binary is not found."""
        manager = ToolManager()
        manager._registry["tools"]["_test_sourced_missing"] = {
            "check_binary": "nonexistent_binary_xyz_abc",
            "source_cmd": "true",
            "description": "test sourced binary missing",
        }
        assert manager.check_installed("_test_sourced_missing") is False

    def test_source_cmd_timeout_returns_false(self) -> None:
        """source_cmd subprocess timeout should return False, not raise."""
        import subprocess
        manager = ToolManager()
        manager._registry["tools"]["_test_timeout"] = {
            "check_binary": "python3",
            "source_cmd": "true",
            "description": "test timeout",
        }
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="bash", timeout=10)):
            assert manager.check_installed("_test_timeout") is False


class TestCheckAll:
    def test_check_all_returns_dict(self) -> None:
        manager = ToolManager()
        result = manager.check_all()
        assert isinstance(result, dict)

    def test_check_all_has_expected_keys_per_tool(self) -> None:
        manager = ToolManager()
        result = manager.check_all()
        for name, info in result.items():
            assert "installed" in info, f"Tool '{name}' missing 'installed' key"
            assert "description" in info, f"Tool '{name}' missing 'description' key"
            assert "install_cmd" in info, f"Tool '{name}' missing 'install_cmd' key"
            assert isinstance(info["installed"], bool)

    def test_check_all_covers_registry_tools(self) -> None:
        manager = ToolManager()
        registry_tools = set(manager._registry["tools"].keys())
        result_tools = set(manager.check_all().keys())
        assert registry_tools == result_tools


class TestGetInstallCommand:
    def test_known_tool(self) -> None:
        manager = ToolManager()
        cmd = manager.get_install_command("gmsh")
        assert cmd is not None
        assert "gmsh" in cmd.lower()

    def test_unknown_tool_returns_none(self) -> None:
        manager = ToolManager()
        assert manager.get_install_command("does_not_exist") is None


class TestPrintStatus:
    def test_print_status_runs_without_error(self, capsys) -> None:
        """print_status should produce output without raising."""
        manager = ToolManager()
        manager.print_status()
        captured = capsys.readouterr()
        assert "Tool" in captured.out
        assert "Install command" in captured.out
