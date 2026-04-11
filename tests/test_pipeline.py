"""Tests for PipelineState — resumable state tracking."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clawgeneer.pipeline.state import PipelineState, StageStatus


class TestPipelineStateInit:
    def test_creates_fresh_state(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        assert state.state_file == tmp_path / "pipeline_state.json"
        for stage in PipelineState.STAGES:
            assert state.get_stage(stage) == StageStatus.pending

    def test_state_file_created_on_first_set(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        assert not state.state_file.exists()
        state.set_stage("cad", StageStatus.completed)
        assert state.state_file.exists()

    def test_loads_existing_state(self, tmp_path: Path) -> None:
        """PipelineState should load from an existing JSON file."""
        state1 = PipelineState(tmp_path)
        state1.set_stage("cad", StageStatus.completed)
        state1.set_stage("mesh", StageStatus.failed, error="meshing error")

        state2 = PipelineState(tmp_path)
        assert state2.get_stage("cad") == StageStatus.completed
        assert state2.get_stage("mesh") == StageStatus.failed


class TestSetStage:
    def test_set_completed(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.set_stage("cad", StageStatus.completed)
        assert state.get_stage("cad") == StageStatus.completed

    def test_set_failed_with_error(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.set_stage("mesh", StageStatus.failed, error="out of memory")
        assert state.get_stage("mesh") == StageStatus.failed
        data = json.loads(state.state_file.read_text())
        assert data["errors"]["mesh"] == "out of memory"

    def test_set_with_summary(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        summary = {"step_file": "/path/to/part.step", "size_bytes": 12345}
        state.set_stage("cad", StageStatus.completed, summary=summary)
        data = json.loads(state.state_file.read_text())
        assert data["summaries"]["cad"]["step_file"] == "/path/to/part.step"

    def test_timestamps_recorded(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.set_stage("fea", StageStatus.running)
        data = json.loads(state.state_file.read_text())
        assert "fea_running" in data["timestamps"]

    def test_all_stage_statuses(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        for status in StageStatus:
            state.set_stage("cad", status)
            assert state.get_stage("cad") == status


class TestIsStageComplete:
    def test_pending_is_not_complete(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        assert state.is_stage_complete("cad") is False

    def test_failed_is_not_complete(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.set_stage("cad", StageStatus.failed)
        assert state.is_stage_complete("cad") is False

    def test_completed_is_complete(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.set_stage("cad", StageStatus.completed)
        assert state.is_stage_complete("cad") is True


class TestResetFrom:
    def test_resets_target_and_downstream(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        for stage in PipelineState.STAGES:
            state.set_stage(stage, StageStatus.completed)

        state.reset_from("mesh")

        # CAD should still be completed (upstream of reset point)
        assert state.get_stage("cad") == StageStatus.completed
        # Mesh and everything after should be pending
        for stage in ["mesh", "fea", "cfd", "results"]:
            assert state.get_stage(stage) == StageStatus.pending

    def test_reset_clears_errors(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.set_stage("fea", StageStatus.failed, error="FEA crashed")
        state.reset_from("fea")
        data = json.loads(state.state_file.read_text())
        assert "fea" not in data.get("errors", {})

    def test_reset_from_first_stage(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        for stage in PipelineState.STAGES:
            state.set_stage(stage, StageStatus.completed)
        state.reset_from("cad")
        for stage in PipelineState.STAGES:
            assert state.get_stage(stage) == StageStatus.pending


class TestIncrementIteration:
    def test_starts_at_zero(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        assert state.iteration == 0

    def test_increment_returns_new_value(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        assert state.increment_iteration() == 1
        assert state.increment_iteration() == 2
        assert state.increment_iteration() == 3

    def test_iteration_persisted(self, tmp_path: Path) -> None:
        state1 = PipelineState(tmp_path)
        state1.increment_iteration()
        state1.increment_iteration()

        state2 = PipelineState(tmp_path)
        assert state2.iteration == 2


class TestSummary:
    def test_summary_returns_dict(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        s = state.summary()
        assert isinstance(s, dict)
        assert "stages" in s
        assert "iteration" in s

    def test_summary_reflects_current_state(self, tmp_path: Path) -> None:
        state = PipelineState(tmp_path)
        state.set_stage("cad", StageStatus.completed)
        state.increment_iteration()
        s = state.summary()
        assert s["stages"]["cad"] == "completed"
        assert s["iteration"] == 1
