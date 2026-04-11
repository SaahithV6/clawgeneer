"""Constraint handler — interprets vague prompts into project.yaml parameters."""

from __future__ import annotations

import json
import re

from clawgeneer.ai.llm_client import LLMClient
from clawgeneer.schema.project import ProjectConfig

SYSTEM_PROMPT = """You are an engineering constraint interpreter for ClawGeneer.

Your job: convert a vague user description into structured engineering parameters.

RULES:
1. If critical values (loads, dimensions) are missing, return QUESTIONS not guesses
2. If material is unspecified, default to mild steel (youngs_modulus: 210000, yield_strength: 250)
3. NEVER invent load magnitudes — always ask
4. Return JSON in one of two formats:

Format A — parameters are clear:
{"status": "ready", "geometry_parameters": {...}, "material": {...}, "notes": "..."}

Format B — clarification needed:
{"status": "questions", "questions": ["What is the expected load in Newtons?", ...]}
"""


class ConstraintHandler:
    """Interpret vague engineering descriptions into structured project parameters."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def interpret(self, description: str, project: ProjectConfig) -> dict:
        """Interpret a description and return either parameters or clarifying questions.

        Args:
            description: Vague natural language description from user.
            project: Current project config (may be partially filled).

        Returns:
            Dict with 'status' key. If 'ready', also has 'geometry_parameters' and 'material'.
            If 'questions', also has 'questions' list.
        """
        user_message = f"""User description: "{description}"

Current project state:
{json.dumps(project.model_dump(mode="json"), indent=2)}

Interpret this description and return parameters or questions as JSON."""

        raw = self.client.complete(SYSTEM_PROMPT, user_message)

        try:
            raw_stripped = re.sub(r"```(?:json)?\n?", "", raw).strip().rstrip("```")
            return json.loads(raw_stripped)
        except json.JSONDecodeError:
            return {"status": "error", "raw": raw}

    def apply_parameters(self, params: dict, project: ProjectConfig) -> ProjectConfig:
        """Apply interpreted parameters to the project config."""
        if params.get("status") != "ready":
            return project

        if "geometry_parameters" in params:
            project.geometry.parameters.update(params["geometry_parameters"])

        if "material" in params:
            mat_data = params["material"]
            for key, value in mat_data.items():
                if hasattr(project.material, key):
                    setattr(project.material, key, value)

        return project
