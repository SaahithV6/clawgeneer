"""ClawGeneer CLI — the 'oc' command.

Commands:
  oc init <name>           Create a new project directory with template project.yaml
  oc run <name>            Run the full pipeline for a project
  oc status <name>         Show pipeline state
  oc check                 Verify all tools are installed
  oc setup-keys            Configure API keys for LLM features
  oc chat <name>           Interactive AI assistant (stub)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from getpass import getpass
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _projects_dir() -> Path:
    """Return the configured projects directory, expanding ~."""
    return Path(os.environ.get("CLAWGENEER_PROJECTS_DIR", "~/projects")).expanduser()


def _project_dir(name: str) -> Path:
    """Return the directory for a named project."""
    return _projects_dir() / name


def cmd_init(args: argparse.Namespace) -> int:
    """Create a new project directory with a template project.yaml."""
    project_dir = _project_dir(args.name)
    if project_dir.exists():
        logger.error("Project '%s' already exists at %s", args.name, project_dir)
        return 1

    project_dir.mkdir(parents=True)
    for subdir in ("geometry", "mesh", "fea", "cfd", "results"):
        (project_dir / subdir).mkdir()

    # Copy template project.yaml
    template = Path(__file__).parents[2] / "schema" / "templates" / "project.yaml"
    dest = project_dir / "project.yaml"
    if template.exists():
        shutil.copy2(template, dest)
        content = dest.read_text()
        content = content.replace("name: my_project", f"name: {args.name}")
        dest.write_text(content)
    else:
        dest.write_text(f"project:\n  name: {args.name}\n  type: part\n")

    print(f"✓ Project '{args.name}' created at {project_dir}")
    print(f"  Edit {dest} to configure geometry, material, and jobs.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run the full pipeline for a project."""
    project_dir = _project_dir(args.name)
    yaml_file = project_dir / "project.yaml"

    if not yaml_file.exists():
        logger.error("project.yaml not found at %s", yaml_file)
        logger.error("Run: oc init %s", args.name)
        return 1

    try:
        from clawgeneer.schema.project import ProjectConfig  # noqa: PLC0415
        from clawgeneer.pipeline.runner import PipelineRunner  # noqa: PLC0415
    except ImportError as e:
        logger.error("Import error: %s", e)
        logger.error("Make sure the clawgeneer package is on PYTHONPATH or run from repo root.")
        return 1

    project = ProjectConfig.from_yaml(yaml_file)
    runner = PipelineRunner(project, project_dir)
    summary = runner.run(resume=not args.fresh)

    print("\n=== Pipeline Summary ===")
    for stage, result in summary.get("stages", {}).items():
        status = "✓" if result["success"] else "✗"
        print(
            f"  {status} {stage.upper()}: {'OK' if result['success'] else result.get('error', 'failed')}"
        )
    return 0 if all(v["success"] for v in summary.get("stages", {}).values()) else 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show pipeline state for a project."""
    project_dir = _project_dir(args.name)
    state_file = project_dir / "pipeline_state.json"

    if not state_file.exists():
        print(f"No pipeline state found for project '{args.name}'")
        print(f"Run: oc run {args.name}")
        return 0

    with open(state_file) as f:
        state = json.load(f)

    print(f"\n=== Pipeline State: {args.name} ===")
    print(f"Iteration: {state.get('iteration', 0)}")
    print("\nStages:")
    for stage, status in state.get("stages", {}).items():
        icon = {
            "completed": "✓",
            "failed": "✗",
            "running": "►",
            "pending": "○",
        }.get(status, "?")
        print(f"  {icon} {stage}: {status}")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    """Check that all required tools are installed."""
    try:
        from clawgeneer.tools.tool_manager import ToolManager  # noqa: PLC0415
        manager = ToolManager()
        manager.print_status()
    except ImportError as e:
        logger.error("Import error: %s", e)
        return 1
    return 0


def cmd_setup_keys(_args: argparse.Namespace) -> int:
    """Interactive setup for API keys and environment variables."""
    bashrc = Path.home() / ".bashrc"

    print("\n=== ClawGeneer API Key Setup ===\n")

    # Check if already configured
    existing = bashrc.read_text() if bashrc.exists() else ""
    if "GITHUB_PAT" in existing:
        print("⚠  GITHUB_PAT already found in ~/.bashrc")
        overwrite = input("  Overwrite? [y/N]: ").strip().lower()
        if overwrite not in ("y", "yes"):
            print("  Skipping. Current config preserved.")
            return 0

    # Get PAT
    print("GitHub Personal Access Token (for GitHub Models LLM API)")
    print("  Get one at: https://github.com/settings/tokens")
    print("  No special scopes needed for GitHub Models.")
    pat = getpass("\n  Enter your GitHub PAT: ").strip()

    if not pat:
        print("  No token entered. Skipping.")
        return 0

    # Get model preference
    print("\n  Available models: gpt-4o (default), gpt-5, gpt-5-mini, gpt-5-nano")
    model = input("  LLM model [gpt-4o]: ").strip() or "gpt-4o"

    # Write to bashrc
    block = (
        "\n# ── ClawGeneer API Configuration ──\n"
        f'export GITHUB_PAT="{pat}"\n'
        f'export CLAWGENEER_LLM_MODEL="{model}"\n'
        'export CLAWGENEER_LLM_MODE="interactive"\n'
    )

    with open(bashrc, "a") as f:
        f.write(block)

    print(f"\n✓ API keys written to {bashrc}")
    print("  Run: source ~/.bashrc")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    """Interactive AI chat assistant (stub)."""
    print(f"oc chat {args.name} — interactive AI assistant")
    print("Coming soon. For now, use 'oc run' to execute the pipeline.")
    return 0


def main() -> None:
    """Main entry point for the 'oc' CLI."""
    parser = argparse.ArgumentParser(
        prog="oc",
        description="ClawGeneer engineering orchestration platform",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Create a new project")
    p_init.add_argument("name", help="Project name")
    p_init.set_defaults(func=cmd_init)

    # run
    p_run = subparsers.add_parser("run", help="Run the pipeline")
    p_run.add_argument("name", help="Project name")
    p_run.add_argument("--fresh", action="store_true", help="Ignore cached stage results")
    p_run.set_defaults(func=cmd_run)

    # status
    p_status = subparsers.add_parser("status", help="Show pipeline state")
    p_status.add_argument("name", help="Project name")
    p_status.set_defaults(func=cmd_status)

    # check
    p_check = subparsers.add_parser("check", help="Check tool installation status")
    p_check.set_defaults(func=cmd_check)

    # setup-keys
    p_keys = subparsers.add_parser("setup-keys", help="Configure API keys for LLM features")
    p_keys.set_defaults(func=cmd_setup_keys)

    # chat
    p_chat = subparsers.add_parser("chat", help="Interactive AI chat assistant")
    p_chat.add_argument("name", help="Project name")
    p_chat.set_defaults(func=cmd_chat)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
