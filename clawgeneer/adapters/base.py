"""Base adapter contract for all ClawGeneer tool adapters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AdapterResult:
    """Standardised result from any adapter run."""

    success: bool
    output_path: Path | None = None
    summary: dict = field(default_factory=dict)
    logs: str = ""
    error: str | None = None


class BaseAdapter(ABC):
    """Abstract base class that every tool adapter must implement."""

    @abstractmethod
    def validate_inputs(self) -> bool:
        """Validate that all required inputs exist and are well-formed."""
        ...

    @abstractmethod
    def run(self) -> AdapterResult:
        """Execute the tool and return an AdapterResult."""
        ...

    @abstractmethod
    def parse_outputs(self) -> dict:
        """Parse tool outputs into a structured summary dict."""
        ...

    @abstractmethod
    def check_installed(self) -> bool:
        """Return True if the underlying tool is installed and runnable."""
        ...
