"""Tests for CLI helpers."""

from ascot.cli import _case_needs_review
from ascot.models import CaseResult, ExpectationResult


def test_case_needs_review_when_trial_has_error():
    trials = [
        CaseResult(case_id="c1", score=10, max_score=10),
        CaseResult(
            case_id="c1",
            score=0,
            max_score=10,
            error="OpenCodeTimeoutError: OpenCode run exceeded timeout_s",
        ),
    ]

    assert _case_needs_review(trials)


def test_case_needs_review_when_expectation_failed():
    trials = [
        CaseResult(
            case_id="c1",
            score=0,
            max_score=1,
            expectation_results=[
                ExpectationResult(desc="output exists", score=1, earned=0),
            ],
        )
    ]

    assert _case_needs_review(trials)


def test_case_needs_review_when_score_lost_without_expectation_details():
    trials = [CaseResult(case_id="c1", score=3, max_score=5)]

    assert _case_needs_review(trials)


def test_case_needs_review_false_when_all_trials_passed():
    trials = [
        CaseResult(
            case_id="c1",
            score=1,
            max_score=1,
            expectation_results=[
                ExpectationResult(desc="output exists", score=1, earned=1),
            ],
        )
    ]

    assert not _case_needs_review(trials)
