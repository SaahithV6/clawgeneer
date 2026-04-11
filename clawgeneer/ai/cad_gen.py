"""CAD code generation — prompt to Build123d Python code via LLM."""

from __future__ import annotations

import json
import re

from clawgeneer.ai.llm_client import LLMClient
from clawgeneer.schema.project import ProjectConfig

SYSTEM_PROMPT = """You are a Python CAD code generator for the ClawGeneer engineering platform.
Generate valid Build123d Python code that creates 3D geometry.

STRICT RULES:
1. Use Build123d context-manager syntax: `with BuildPart() as part:`
2. Label ALL faces that will be used as boundary conditions:
   `faces().filter_by(Axis.Z).last.label = "top_face"`
3. All dimensions are in MILLIMETRES
4. Define a function: `make_part(**kwargs) -> Part` that accepts geometry parameters as kwargs
5. Return ONLY valid JSON: {"code": "<escaped python string>"}
6. No markdown, no explanation — JSON only
7. Imports must be inside the function or at module top level

EXAMPLE OUTPUT:
{"code": "from build123d import *\\n\\ndef make_part(length=100, width=60, thickness=8, **kwargs):\\n    with BuildPart() as p:\\n        Box(length, width, thickness)\\n        faces().filter_by(Axis.Z).last.label = 'top_face'\\n        faces().filter_by(Axis.Z).first.label = 'bottom_face'\\n    return p.part\\n"}
"""


def generate_cad_code(
    prompt: str,
    project: ProjectConfig,
    client: LLMClient,
    max_retries: int = 3,
) -> str:
    """Generate Build123d Python code from a natural language prompt.

    Args:
        prompt: Natural language description of the geometry.
        project: Current project config (provides parameter hints and BC surface names).
        client: Configured LLMClient instance.
        max_retries: Number of retry attempts if validation fails.

    Returns:
        Valid executable Build123d Python code string.

    Raises:
        RuntimeError: If code generation fails after max_retries attempts.
    """
    bc_surfaces = [bc.surface for job in project.jobs for bc in job.boundary_conditions]
    params = project.geometry.parameters

    user_message = f"""Generate Build123d code for:

{prompt}

Geometry parameters from project.yaml:
{json.dumps(params, indent=2)}

Boundary condition surfaces that MUST be labelled:
{json.dumps(bc_surfaces, indent=2)}

Material: {project.material.name} (yield strength {project.material.yield_strength} MPa)
"""

    last_error = ""
    for attempt in range(max_retries):
        if attempt > 0:
            user_message += (
                f"\n\nPrevious attempt failed with error:\n{last_error}\n"
                "Please fix and try again."
            )

        raw = client.complete(SYSTEM_PROMPT, user_message)

        # Parse JSON response
        try:
            raw_stripped = re.sub(r"```(?:json)?\n?", "", raw).strip().rstrip("```")
            data = json.loads(raw_stripped)
            code = data.get("code", "")
            if not code:
                last_error = "Response JSON had empty 'code' field"
                continue
        except json.JSONDecodeError as e:
            last_error = f"JSON parse error: {e}\nRaw response: {raw[:500]}"
            continue

        # Basic syntax check
        try:
            compile(code, "<llm_generated>", "exec")
        except SyntaxError as e:
            last_error = f"Syntax error: {e}"
            continue

        if "make_part" not in code:
            last_error = "Code missing required make_part() function"
            continue

        return code

    raise RuntimeError(
        f"CAD code generation failed after {max_retries} attempts. Last error: {last_error}"
    )
