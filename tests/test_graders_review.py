"""Tests for the review-agent helpers in ascot.graders."""

from ascot.graders import _build_review_prompt, _setup_review_workspace
from ascot.models import CaseResult, Expectation, ExpectationResult, TestCase


def _tc():
    return TestCase(
        id="c1",
        prompt="do the thing",
        expectations=[Expectation(desc="a", score=5), Expectation(desc="b", score=5)],
    )


def _trial(score, max_score, *, error=None, ers=None):
    return CaseResult(
        case_id="c1", score=score, max_score=max_score,
        error=error, expectation_results=ers or [],
    )


class TestBuildReviewPrompt:
    def test_includes_task_and_expectations(self):
        prompt = _build_review_prompt(_tc(), [_trial(10, 10)])
        assert "do the thing" in prompt
        assert "1. a (5 pts)" in prompt
        assert "2. b (5 pts)" in prompt

    def test_renders_pass_fail_tags_and_errors(self):
        trials = [
            _trial(10, 10, ers=[ExpectationResult(desc="a", score=5, earned=5, reasoning="ok")]),
            _trial(0, 10, error="OpenCodeTimeoutError: slow",
                   ers=[ExpectationResult(desc="a", score=5, earned=0, reasoning="bad")]),
        ]
        prompt = _build_review_prompt(_tc(), trials)
        assert "[PASS] 10/10" in prompt
        assert "[FAIL] 0/10" in prompt
        assert "Error: OpenCodeTimeoutError: slow" in prompt

    def test_mixed_trials_emit_comparison_instruction(self):
        trials = [_trial(10, 10), _trial(0, 10)]
        assert "Some trials passed and some failed" in _build_review_prompt(_tc(), trials)

    def test_all_failed_omits_comparison_instruction(self):
        trials = [_trial(0, 10), _trial(0, 10)]
        assert "Some trials passed and some failed" not in _build_review_prompt(_tc(), trials)

    def test_reasoning_truncated_to_300_chars(self):
        long_reason = "x" * 500
        trials = [_trial(0, 10, ers=[
            ExpectationResult(desc="a", score=5, earned=0, reasoning=long_reason)])]
        prompt = _build_review_prompt(_tc(), trials)
        assert "x" * 300 in prompt
        assert "x" * 301 not in prompt


class TestSetupReviewWorkspace:
    def test_copies_events_and_output_per_trial(self, tmp_path):
        case_dir = tmp_path / "case"
        for n in (1, 2):
            td = case_dir / f"trial-{n}"
            td.mkdir(parents=True)
            (td / "events.jsonl").write_text(f"trial{n}\n")
            (td / "workspace").mkdir()
            (td / "workspace" / "out.txt").write_text(f"out{n}")

        review_ws = _setup_review_workspace(case_dir, [])

        assert (review_ws / "trial-1" / "events.jsonl").read_text() == "trial1\n"
        assert (review_ws / "trial-2" / "output" / "out.txt").read_text() == "out2"

    def test_handles_trial_without_artifacts(self, tmp_path):
        case_dir = tmp_path / "case"
        (case_dir / "trial-1").mkdir(parents=True)  # no events, no workspace
        review_ws = _setup_review_workspace(case_dir, [])
        assert review_ws.is_dir()
        assert not (review_ws / "trial-1" / "events.jsonl").exists()
