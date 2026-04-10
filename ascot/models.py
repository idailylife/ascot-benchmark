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
    default_workspace_files_from: str | None = None


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
    trial_results: list["CaseResult"] = field(default_factory=list)
    num_trials: int = 1

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
            "num_trials": self.num_trials,
            "trial_results": [tr.to_dict() for tr in self.trial_results],
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
    num_trials: int = 1

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
            "num_trials": self.num_trials,
        }


def aggregate_trials(case_id: str, trials: list[CaseResult]) -> CaseResult:
    """Aggregate multiple trial results into a single CaseResult using averaging."""
    n = len(trials)
    max_score = trials[0].max_score

    avg_score = round(sum(t.score for t in trials) / n)

    # Per-expectation averaging
    num_expectations = len(trials[0].expectation_results)
    avg_exp_results = []
    for i in range(num_expectations):
        exp = trials[0].expectation_results[i]
        pass_count = sum(1 for t in trials if t.expectation_results[i].earned > 0)
        earned = round(exp.score * pass_count / n)
        avg_exp_results.append(ExpectationResult(
            desc=exp.desc,
            score=exp.score,
            earned=earned,
            reasoning=f"Passed {pass_count}/{n} trials",
        ))

    # Token usage: sum across trials
    all_keys = set()
    for t in trials:
        all_keys.update(t.token_usage.keys())
    agg_tokens = {k: sum(t.token_usage.get(k, 0) for t in trials) for k in all_keys}

    return CaseResult(
        case_id=case_id,
        score=avg_score,
        max_score=max_score,
        expectation_results=avg_exp_results,
        token_usage=agg_tokens,
        total_cost=sum(t.total_cost for t in trials),
        turns=round(sum(t.turns for t in trials) / n),
        duration_s=sum(t.duration_s for t in trials),
        trial_results=trials,
        num_trials=n,
    )
