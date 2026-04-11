"""LLM client — GitHub Models API and Ollama dual backend."""

from __future__ import annotations

import os
from typing import Any


class LLMClient:
    """Unified LLM client supporting GitHub Models (interactive) and Ollama (optimization).

    Backend is selected by the ``mode`` parameter:
    - ``interactive``: GitHub Models API (requires GITHUB_PAT env var)
    - ``optimization``: Ollama local server (no API key needed)

    Model names are read from environment variables so they can be updated
    without code changes as new models become available.
    """

    def __init__(self, mode: str = "interactive") -> None:
        self.mode = mode
        self._client: Any = None
        self._setup_client()

    def _setup_client(self) -> None:
        """Initialise the appropriate OpenAI-compatible client."""
        try:
            from openai import OpenAI  # noqa: PLC0415

            if self.mode == "interactive":
                pat = os.environ.get("GITHUB_PAT")
                if not pat:
                    raise EnvironmentError("GITHUB_PAT environment variable not set")
                self._client = OpenAI(
                    base_url="https://models.inference.ai.azure.com",
                    api_key=pat,
                )
                self.model = os.environ.get("CLAWGENEER_LLM_MODEL", "gpt-4o")

            elif self.mode == "optimization":
                self._client = OpenAI(
                    base_url="http://localhost:11434/v1",
                    api_key="ollama",
                )
                self.model = os.environ.get("CLAWGENEER_OLLAMA_MODEL", "qwen2.5-coder:7b")

            else:
                raise ValueError(
                    f"Unknown LLM mode: {self.mode!r}. Use 'interactive' or 'optimization'."
                )

        except ImportError as e:
            raise ImportError(
                "openai package not installed. Run: pip install openai"
            ) from e

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        """Send a completion request and return the assistant message content."""
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content
