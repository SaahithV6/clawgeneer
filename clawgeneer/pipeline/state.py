"""Pipeline state tracking — enables resumable pipeline runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class StageStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class PipelineState:
    """Track and persist the status of each pipeline stage.

    State is saved to ``pipeline_state.json`` in the project directory after
    every stage change, enabling the pipeline to resume from the last successful
    stage.
    """

    STAGES = ["cad", "mesh", "fea", "cfd", "results"]

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.state_file = project_dir / "pipeline_state.json"
        self._state: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        """Load state from disk, or create fresh state if file does not exist."""
        if self.state_file.exists():
            with open(self.state_file) as f:
                return json.load(f)
        return {
            "stages": {stage: StageStatus.pending.value for stage in self.STAGES},
            "timestamps": {},
            "errors": {},
            "summaries": {},
            "iteration": 0,
        }

    def _save(self) -> None:
        """Persist current state to disk."""
        with open(self.state_file, "w") as f:
            json.dump(self._state, f, indent=2)

    def set_stage(
        self,
        stage: str,
        status: StageStatus,
        summary: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update a stage status and persist to disk."""
        self._state["stages"][stage] = status.value
        self._state["timestamps"][f"{stage}_{status.value}"] = datetime.now(timezone.utc).isoformat()
        if summary:
            self._state["summaries"][stage] = summary
        if error:
            self._state["errors"][stage] = error
        self._save()

    def get_stage(self, stage: str) -> StageStatus:
        """Return the current status of a stage."""
        return StageStatus(self._state["stages"].get(stage, StageStatus.pending.value))

    def is_stage_complete(self, stage: str) -> bool:
        """Return True if the stage has completed successfully."""
        return self.get_stage(stage) == StageStatus.completed

    def reset_from(self, stage: str) -> None:
        """Reset all stages from a given stage onwards (for re-runs)."""
        reset = False
        for s in self.STAGES:
            if s == stage:
                reset = True
            if reset:
                self._state["stages"][s] = StageStatus.pending.value
                self._state["errors"].pop(s, None)
        self._save()

    def increment_iteration(self) -> int:
        """Increment the optimization iteration counter and return new value."""
        self._state["iteration"] += 1
        self._save()
        return self._state["iteration"]

    @property
    def iteration(self) -> int:
        """Current optimization iteration number."""
        return self._state["iteration"]

    def summary(self) -> dict:
        """Return a copy of the full state dict."""
        return dict(self._state)
