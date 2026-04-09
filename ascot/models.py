"""Data models for Ascot benchmark framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Expectation:
    """A single grading expectation with description and point value."""

    desc: str
    score: int = 1


@dataclass
class ExpectationResult:
    """Result of evaluating a single expectation."""

    desc: str
    score: int  # max points for this expectation
    earned: int  # actual points earned (0 or score)
    reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "desc": self.desc,
            "score": self.score,
            "earned": self.earned,
            "reasoning": self.reasoning,
        }


@dataclass
class TestCase:
    """A single benchmark test case."""

    id: str
    prompt: str
    expectations: list[Expectation] = field(default_factory=list)
    description: str = ""
    workspace_files_from: str | None = None
    timeout_s: float = 120.0
    model: str | None = None
    agent: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class TestSuite:
    """A collection of test cases for a suite benchmark."""

    name: str
    test_cases: list[TestCase] = field(default_factory=list)
    description: str = ""
    default_timeout_s: float = 120.0
    default_model: str | None = None


@dataclass
class CaseResult:
    """Aggregated result for one test case."""

    case_id: str
    score: int = 0
    max_score: int = 0
    expectation_results: list[ExpectationResult] = field(default_factory=list)
    final_text: str = ""
    exit_code: int | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    total_cost: float = 0.0
    turns: int = 0
    duration_s: float = 0.0
    error: str | None = None
    phases: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "case_id": self.case_id,
            "score": self.score,
            "max_score": self.max_score,
            "expectation_results": [er.to_dict() for er in self.expectation_results],
            "final_text": self.final_text,
            "exit_code": self.exit_code,
            "token_usage": self.token_usage,
            "total_cost": self.total_cost,
            "turns": self.turns,
            "duration_s": self.duration_s,
            "error": self.error,
        }
        if self.phases:
            d["phases"] = self.phases
        return d


@dataclass
class BenchmarkReport:
    """Aggregated benchmark results."""

    suite_name: str
    run_id: str
    timestamp: str
    results: list[CaseResult] = field(default_factory=list)
    total: int = 0
    total_score: int = 0
    max_score: int = 0
    total_turns: int = 0
    total_tokens: int = 0
    total_duration_s: float = 0.0
    total_cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "results": [r.to_dict() for r in self.results],
            "total": self.total,
            "total_score": self.total_score,
            "max_score": self.max_score,
            "total_turns": self.total_turns,
            "total_tokens": self.total_tokens,
            "total_duration_s": self.total_duration_s,
            "total_cost": self.total_cost,
        }
