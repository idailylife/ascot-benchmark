"""Tests for grade_case's test_script + LLM-judge concat path."""

from pathlib import Path

import pytest
from types import SimpleNamespace

from ascot.graders import grade_case
from ascot.models import Expectation, ExpectationResult, TestCase


def _fake_run_result():
    """Minimal RunResult-like object for grade_case."""
    return SimpleNamespace(
        final_text="ok",
        events=[],
        exit_code=0,
        total_cost=0.0,
        turns=0,
        token_usage=SimpleNamespace(
            total=0, input=0, output=0, reasoning=0,
            cache_read=0, cache_write=0,
        ),
    )


def _make_case_dir_with_ws(tmp_path: Path) -> Path:
    case_dir = tmp_path / "case"
    (case_dir / "workspace").mkdir(parents=True)
    return case_dir


def _write(p: Path, content: str) -> Path:
    p.write_text(content)
    return p


class TestGradeCaseTestScript:
    async def test_test_script_only_skips_judge(self, tmp_path, monkeypatch):
        """When only test_script is set, llm_judge must not be called."""
        case_dir = _make_case_dir_with_ws(tmp_path)
        script = _write(tmp_path / "test_x.py",
                        "def test_a():\n    assert True\n"
                        "def test_b():\n    assert False\n")

        called = {"judge": False}

        async def fake_judge(*a, **kw):
            called["judge"] = True
            return [], {"tokens": {}, "cost": 0.0, "turns": 0}

        monkeypatch.setattr("ascot.graders.llm_judge", fake_judge)

        tc = TestCase(id="c1", prompt="x", expectations=[])
        cr, stats = await grade_case(
            tc, case_dir, _fake_run_result(), 1.0, client=None,
            test_script_path=script,
        )

        assert called["judge"] is False
        assert stats["cost"] == 0.0
        assert len(cr.expectation_results) == 2
        assert cr.score == 1  # test_a passed
        assert cr.max_score == 2

    async def test_expectations_only_behaves_as_before(self, tmp_path, monkeypatch):
        """No test_script → unchanged behavior: judge handles all expectations."""
        case_dir = _make_case_dir_with_ws(tmp_path)

        called = {"judge": 0}

        async def fake_judge(case_dir, test_case, client, grading_model=None):
            called["judge"] += 1
            results = [
                ExpectationResult(desc=e.desc, score=e.score, earned=e.score,
                                  reasoning="judged")
                for e in test_case.expectations
            ]
            return results, {"tokens": {}, "cost": 0.123, "turns": 1}

        monkeypatch.setattr("ascot.graders.llm_judge", fake_judge)

        tc = TestCase(id="c1", prompt="x",
                      expectations=[Expectation(desc="fuzzy", score=3)])
        cr, stats = await grade_case(
            tc, case_dir, _fake_run_result(), 1.0, client=None,
        )

        assert called["judge"] == 1
        assert stats["cost"] == 0.123
        assert cr.score == 3
        assert cr.max_score == 3
        assert cr.expectation_results[0].desc == "fuzzy"

    async def test_hybrid_concat_order(self, tmp_path, monkeypatch):
        """test_script results come first, then judge results."""
        case_dir = _make_case_dir_with_ws(tmp_path)
        script = _write(tmp_path / "test_x.py",
                        "def test_obj():\n    assert True\n")

        async def fake_judge(case_dir, test_case, client, grading_model=None):
            return [
                ExpectationResult(desc=e.desc, score=e.score, earned=0,
                                  reasoning="failed")
                for e in test_case.expectations
            ], {"tokens": {}, "cost": 0.5, "turns": 2}

        monkeypatch.setattr("ascot.graders.llm_judge", fake_judge)

        tc = TestCase(id="c1", prompt="x",
                      expectations=[Expectation(desc="fuzzy", score=5)])
        cr, stats = await grade_case(
            tc, case_dir, _fake_run_result(), 1.0, client=None,
            test_script_path=script,
        )

        assert [r.desc for r in cr.expectation_results] == ["test_obj", "fuzzy"]
        assert cr.expectation_results[0].earned == 1
        assert cr.expectation_results[1].earned == 0
        # 1 (pytest) + 0 (failed judge) = 1
        assert cr.score == 1
        # 1 (pytest) + 5 (judge max) = 6
        assert cr.max_score == 6
        assert stats["cost"] == 0.5

    async def test_no_expectations_no_test_script(self, tmp_path, monkeypatch):
        """Empty case: no judge, no verifier, zero score."""
        case_dir = _make_case_dir_with_ws(tmp_path)

        called = {"judge": False}

        async def fake_judge(*a, **kw):
            called["judge"] = True
            return [], {"tokens": {}, "cost": 0.0, "turns": 0}

        monkeypatch.setattr("ascot.graders.llm_judge", fake_judge)

        tc = TestCase(id="c1", prompt="x", expectations=[])
        cr, stats = await grade_case(
            tc, case_dir, _fake_run_result(), 1.0, client=None,
        )

        assert called["judge"] is False
        assert cr.score == 0
        assert cr.max_score == 0
        assert cr.expectation_results == []
