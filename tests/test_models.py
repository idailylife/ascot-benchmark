"""Tests for ascot.models."""

from ascot.models import (
    CaseResult,
    ExpectationResult,
    BenchmarkReport,
    aggregate_trials,
)


class TestExpectationResultToDict:
    def test_basic(self):
        er = ExpectationResult(desc="file exists", score=5, earned=5, reasoning="ok")
        assert er.to_dict() == {
            "desc": "file exists",
            "score": 5,
            "earned": 5,
            "reasoning": "ok",
        }

    def test_empty_reasoning(self):
        er = ExpectationResult(desc="x", score=1, earned=0)
        assert er.to_dict()["reasoning"] == ""


class TestCaseResultToDict:
    def test_includes_all_fields(self):
        cr = CaseResult(case_id="c1", score=5, max_score=10)
        d = cr.to_dict()
        assert d["case_id"] == "c1"
        assert d["score"] == 5
        assert d["max_score"] == 10
        assert "phases" not in d  # omitted when empty

    def test_includes_phases_when_present(self):
        cr = CaseResult(case_id="c1", phases={"agent_run": {"duration_s": 1.5}})
        assert "phases" in cr.to_dict()

    def test_nested_expectation_results(self):
        cr = CaseResult(
            case_id="c1",
            expectation_results=[
                ExpectationResult(desc="a", score=1, earned=1),
            ],
        )
        d = cr.to_dict()
        assert len(d["expectation_results"]) == 1
        assert d["expectation_results"][0]["desc"] == "a"

    def test_nested_trial_results(self):
        tr = CaseResult(case_id="c1", score=3, max_score=5)
        cr = CaseResult(case_id="c1", trial_results=[tr])
        d = cr.to_dict()
        assert len(d["trial_results"]) == 1
        assert d["trial_results"][0]["case_id"] == "c1"


class TestBenchmarkReportToDict:
    def test_basic(self):
        r = BenchmarkReport(suite_name="s", run_id="run-001", timestamp="2024-01-01")
        d = r.to_dict()
        assert d["suite_name"] == "s"
        assert d["run_id"] == "run-001"
        assert d["results"] == []


class TestAggregateTrials:
    def _make_trial(self, score, max_score, expectations=None, **kwargs):
        return CaseResult(
            case_id="c1",
            score=score,
            max_score=max_score,
            expectation_results=expectations or [],
            **kwargs,
        )

    def test_single_trial(self):
        t = self._make_trial(
            10, 10,
            [ExpectationResult(desc="ok", score=10, earned=10)],
            duration_s=5.0, turns=3,
        )
        agg = aggregate_trials("c1", [t])
        assert agg.score == 10
        assert agg.max_score == 10
        assert agg.num_trials == 1
        assert len(agg.expectation_results) == 1
        assert agg.expectation_results[0].earned == 10

    def test_averaging(self):
        e1 = [ExpectationResult(desc="a", score=10, earned=10)]
        e2 = [ExpectationResult(desc="a", score=10, earned=0)]
        t1 = self._make_trial(10, 10, e1, duration_s=5.0, turns=2)
        t2 = self._make_trial(0, 10, e2, duration_s=3.0, turns=4)
        agg = aggregate_trials("c1", [t1, t2])
        assert agg.score == 5
        assert agg.max_score == 10
        assert agg.num_trials == 2
        assert agg.expectation_results[0].earned == 5
        assert agg.expectation_results[0].reasoning == "Passed 1/2 trials"
        assert agg.duration_s == 8.0
        assert agg.turns == 3

    def test_timed_out_trial(self):
        ok = self._make_trial(
            10, 10,
            [ExpectationResult(desc="a", score=5, earned=5),
             ExpectationResult(desc="b", score=5, earned=5)],
            duration_s=5.0,
        )
        timeout = self._make_trial(0, 0, [], duration_s=120.0, error="timeout")
        agg = aggregate_trials("c1", [ok, timeout])
        assert agg.max_score == 10
        assert len(agg.expectation_results) == 2
        assert agg.expectation_results[0].reasoning == "Passed 1/2 trials"

    def test_all_timed_out(self):
        t1 = self._make_trial(0, 0, [], error="timeout")
        t2 = self._make_trial(0, 0, [], error="timeout")
        agg = aggregate_trials("c1", [t1, t2])
        assert agg.score == 0
        assert agg.max_score == 0
        assert agg.expectation_results == []

    def test_token_usage_summed(self):
        t1 = self._make_trial(0, 0, token_usage={"input": 100, "output": 50})
        t2 = self._make_trial(0, 0, token_usage={"input": 200, "output": 30, "total": 230})
        agg = aggregate_trials("c1", [t1, t2])
        assert agg.token_usage["input"] == 300
        assert agg.token_usage["output"] == 80
        assert agg.token_usage["total"] == 230

    def test_cost_summed(self):
        t1 = self._make_trial(0, 0, total_cost=0.01)
        t2 = self._make_trial(0, 0, total_cost=0.02)
        agg = aggregate_trials("c1", [t1, t2])
        assert abs(agg.total_cost - 0.03) < 1e-9
