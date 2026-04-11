"""Tool manager — check installation status and report from registry."""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import yaml


class ToolManager:
    """Load the tool registry and report installation status of all tools."""

    def __init__(self, registry_path: Path | None = None) -> None:
        if registry_path is None:
            registry_path = Path(__file__).parent / "registry.yaml"
        self.registry_path = registry_path
        self._registry: dict = self._load_registry()

    def _load_registry(self) -> dict:
        """Load the YAML registry file."""
        with open(self.registry_path) as f:
            return yaml.safe_load(f)

    def check_installed(self, tool_name: str) -> bool:
        """Return True if the named tool is installed."""
        tool = self._registry.get("tools", {}).get(tool_name)
        if not tool:
            return False

        if "check_python" in tool:
            try:
                importlib.import_module(tool["check_python"])
                return True
            except ImportError:
                return False

        if "check_binary" in tool:
            return shutil.which(tool["check_binary"]) is not None

        return False

    def check_all(self) -> dict[str, dict]:
        """Check installation status of all registered tools.

        Returns:
            Dict mapping tool name to {'installed': bool, 'description': str, 'install_cmd': str}
        """
        results: dict[str, dict] = {}
        for name, info in self._registry.get("tools", {}).items():
            results[name] = {
                "installed": self.check_installed(name),
                "description": info.get("description", ""),
                "install_cmd": info.get("install_cmd", ""),
            }
        return results

    def print_status(self) -> None:
        """Print a formatted tool status table to stdout."""
        statuses = self.check_all()
        max_name = max(len(n) for n in statuses) + 2
        print(f"\n{'Tool':<{max_name}} {'Status':<12} Install command")
        print("-" * 80)
        for name, info in statuses.items():
            status = "✓ installed" if info["installed"] else "✗ missing"
            cmd = info["install_cmd"] if not info["installed"] else ""
            print(f"{name:<{max_name}} {status:<12} {cmd}")
        print()

    def get_install_command(self, tool_name: str) -> str | None:
        """Return the install command for a tool, or None if not found."""
        tool = self._registry.get("tools", {}).get(tool_name)
        return tool.get("install_cmd") if tool else None
