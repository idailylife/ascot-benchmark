"""Tests for ascot.report."""

import json

from ascot.models import BenchmarkReport, CaseResult, ExpectationResult
from ascot.report import format_terminal, format_json


class TestFormatTerminal:
    def _make_report(self, **kwargs):
        defaults = dict(
            suite_name="test-suite",
            run_id="run-001",
            timestamp="2024-01-01",
            total=1,
            total_score=10,
            max_score=10,
            num_trials=1,
        )
        defaults.update(kwargs)
        return BenchmarkReport(**defaults)

    def test_contains_suite_name(self):
        r = self._make_report()
        text = format_terminal(r)
        assert "test-suite" in text

    def test_shows_score_percentage(self):
        r = self._make_report(total_score=7, max_score=10)
        text = format_terminal(r)
        assert "7/10" in text
        assert "70.0%" in text

    def test_shows_cost_when_enabled(self):
        r = self._make_report(total_cost=0.1234)
        text_no_cost = format_terminal(r, show_cost=False)
        text_cost = format_terminal(r, show_cost=True)
        assert "$" not in text_no_cost
        assert "$0.1234" in text_cost

    def test_shows_trial_count(self):
        r = self._make_report(num_trials=3)
        text = format_terminal(r)
        assert "3 trials" in text

    def test_shows_imperfect_details(self):
        er = ExpectationResult(desc="file exists", score=5, earned=0, reasoning="not found")
        cr = CaseResult(case_id="fail-case", score=0, max_score=5,
                        expectation_results=[er])
        r = self._make_report(results=[cr], total_score=0, max_score=5)
        text = format_terminal(r)
        assert "fail-case" in text
        assert "[FAIL]" in text
        assert "file exists" in text

    def test_shows_error(self):
        cr = CaseResult(case_id="err-case", score=0, max_score=5, error="timeout")
        r = self._make_report(results=[cr], total_score=0, max_score=5)
        text = format_terminal(r)
        assert "Error: timeout" in text

    def test_phase_breakdown(self):
        cr = CaseResult(
            case_id="c1", score=10, max_score=10,
            phases={"agent_run": {"duration_s": 5.0}, "grading": {"duration_s": 2.0}},
        )
        r = self._make_report(results=[cr])
        text = format_terminal(r)
        assert "Phase Breakdown" in text


class TestFormatJson:
    def test_valid_json(self):
        r = BenchmarkReport(suite_name="s", run_id="r", timestamp="t")
        output = format_json(r)
        data = json.loads(output)
        assert data["suite_name"] == "s"
