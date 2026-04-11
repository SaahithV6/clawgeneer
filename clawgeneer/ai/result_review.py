"""Result review — AI interprets solver outputs and suggests changes."""

from __future__ import annotations

import json
import re
from pathlib import Path

from clawgeneer.ai.llm_client import LLMClient
from clawgeneer.schema.project import ProjectConfig

SYSTEM_PROMPT = """You are an engineering result analyst for ClawGeneer.

Analyse solver results and provide:
1. Plain-English interpretation of what the results mean
2. Specific parameter changes to improve the design (exact values, not vague suggestions)
3. Whether the design meets the optimization constraints

Return JSON:
{
  "interpretation": "Plain English summary...",
  "meets_constraints": true/false,
  "suggested_changes": {"parameter_name": new_value, ...},
  "reasoning": "Why these changes...",
  "next_action": "iterate|accept|manual_review"
}
"""


class ResultReviewer:
    """AI-powered result review and parameter suggestion engine."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def review(self, results_dir: Path, project: ProjectConfig) -> dict:
        """Review solver results and return interpretation + suggestions.

        Args:
            results_dir: Path to the project results/ directory.
            project: Current project configuration (includes optimization constraints).

        Returns:
            Dict with interpretation, constraint check, and suggested parameter changes.
        """
        results: dict = {}
        fea_summary = results_dir / "fea_summary.json"
        cfd_summary = results_dir / "cfd_summary.json"

        if fea_summary.exists():
            with open(fea_summary) as f:
                results["fea"] = json.load(f)

        if cfd_summary.exists():
            with open(cfd_summary) as f:
                results["cfd"] = json.load(f)

        if not results:
            return {"error": "No result files found in results directory"}

        user_message = f"""Results:
{json.dumps(results, indent=2)}

Project configuration:
{json.dumps(project.model_dump(mode="json"), indent=2)}

Review these results against the optimization constraints and suggest parameter changes."""

        raw = self.client.complete(SYSTEM_PROMPT, user_message)

        try:
            raw_stripped = re.sub(r"```(?:json)?\n?", "", raw).strip().rstrip("```")
            return json.loads(raw_stripped)
        except json.JSONDecodeError:
            return {"interpretation": raw, "error": "JSON parse failed"}

    def apply_suggestions(self, review: dict, project: ProjectConfig) -> ProjectConfig:
        """Apply suggested parameter changes to the project config for next iteration."""
        changes = review.get("suggested_changes", {})
        for param, value in changes.items():
            if param in project.geometry.parameters:
                project.geometry.parameters[param] = value
        return project
