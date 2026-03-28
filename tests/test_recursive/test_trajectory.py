"""Tests for RecursiveTrajectory."""
from __future__ import annotations

from depthfusion.recursive.trajectory import RecursiveTrajectory


def test_default_values():
    traj = RecursiveTrajectory(strategy="peek", query="test query")
    assert traj.strategy == "peek"
    assert traj.query == "test query"
    assert traj.sub_calls == 0
    assert traj.total_tokens == 0
    assert traj.estimated_cost == 0.0
    assert traj.quality_score is None
    assert traj.completed is False
    assert traj.error is None
    assert traj.steps == []


def test_log_step_appends_to_steps():
    traj = RecursiveTrajectory(strategy="grep", query="find something")
    traj.log_step("read", 500, 0.001, "Read 500 tokens of content")
    assert len(traj.steps) == 1
    step = traj.steps[0]
    assert step["step_type"] == "read"
    assert step["tokens"] == 500
    assert step["cost"] == 0.001
    assert step["result_summary"] == "Read 500 tokens of content"


def test_log_step_accumulates_tokens_and_cost():
    traj = RecursiveTrajectory(strategy="summarize", query="summarize this")
    traj.log_step("chunk_1", 1000, 0.002, "First chunk done")
    traj.log_step("chunk_2", 800, 0.0016, "Second chunk done")
    assert traj.total_tokens == 1800
    assert abs(traj.estimated_cost - 0.0036) < 1e-9
    assert traj.sub_calls == 2


def test_log_step_multiple_steps():
    traj = RecursiveTrajectory(strategy="partition_map", query="analyse content")
    for i in range(5):
        traj.log_step(f"step_{i}", 100, 0.0001, f"Step {i} result")
    assert len(traj.steps) == 5
    assert traj.sub_calls == 5
    assert traj.total_tokens == 500


def test_completed_and_error_fields():
    traj = RecursiveTrajectory(strategy="peek", query="q")
    traj.completed = True
    traj.error = "timeout exceeded"
    assert traj.completed is True
    assert traj.error == "timeout exceeded"


def test_quality_score_can_be_set():
    traj = RecursiveTrajectory(strategy="peek", query="q")
    traj.quality_score = 0.87
    assert traj.quality_score == 0.87


def test_steps_not_shared_between_instances():
    """Each instance must have its own steps list."""
    t1 = RecursiveTrajectory(strategy="peek", query="q1")
    t2 = RecursiveTrajectory(strategy="grep", query="q2")
    t1.log_step("read", 100, 0.0, "done")
    assert len(t2.steps) == 0
