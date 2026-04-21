"""Tests for ascot.graders._read_verdict_file."""

import json

from ascot.graders import _read_verdict_file
from ascot.models import Expectation


def _write_verdict(judge_ws, obj):
    (judge_ws / "verdict.json").write_text(json.dumps(obj))


class TestReadVerdictFile:
    def test_valid_verdict_all_passed(self, tmp_path):
        exps = [Expectation(desc="a", score=5), Expectation(desc="b", score=3)]
        _write_verdict(tmp_path, {
            "results": [
                {"index": 0, "passed": True, "reasoning": "ok a"},
                {"index": 1, "passed": True, "reasoning": "ok b"},
            ],
        })

        results = _read_verdict_file(tmp_path, exps)

        assert len(results) == 2
        assert results[0].desc == "a"
        assert results[0].score == 5
        assert results[0].earned == 5
        assert results[0].reasoning == "ok a"
        assert results[1].earned == 3
        assert results[1].reasoning == "ok b"

    def test_valid_verdict_mixed(self, tmp_path):
        exps = [Expectation(desc="a", score=2), Expectation(desc="b", score=4)]
        _write_verdict(tmp_path, {
            "results": [
                {"index": 0, "passed": True, "reasoning": "yes"},
                {"index": 1, "passed": False, "reasoning": "no"},
            ],
        })

        results = _read_verdict_file(tmp_path, exps)

        assert results[0].earned == 2
        assert results[1].earned == 0
        assert results[1].reasoning == "no"

    def test_file_missing(self, tmp_path):
        exps = [Expectation(desc="a", score=1), Expectation(desc="b", score=2)]

        results = _read_verdict_file(tmp_path, exps)

        assert len(results) == 2
        for r in results:
            assert r.earned == 0
            assert r.reasoning.startswith("Could not read verdict.json")

    def test_malformed_json(self, tmp_path):
        exps = [Expectation(desc="a", score=1)]
        (tmp_path / "verdict.json").write_text("not valid json{")

        results = _read_verdict_file(tmp_path, exps)

        assert len(results) == 1
        assert results[0].earned == 0
        assert results[0].reasoning.startswith("Could not read verdict.json")

    def test_missing_results_key(self, tmp_path):
        exps = [Expectation(desc="a", score=1)]
        _write_verdict(tmp_path, {"foo": []})

        results = _read_verdict_file(tmp_path, exps)

        assert results[0].earned == 0
        assert results[0].reasoning.startswith("Could not read verdict.json")

    def test_missing_index_entry(self, tmp_path):
        exps = [Expectation(desc="a", score=1), Expectation(desc="b", score=1)]
        _write_verdict(tmp_path, {
            "results": [
                {"index": 0, "passed": True, "reasoning": "ok"},
            ],
        })

        results = _read_verdict_file(tmp_path, exps)

        assert results[0].earned == 1
        assert results[1].earned == 0
        assert results[1].reasoning == "Missing from judge response"

    def test_extra_entries_ignored(self, tmp_path):
        exps = [Expectation(desc="a", score=1), Expectation(desc="b", score=1)]
        _write_verdict(tmp_path, {
            "results": [
                {"index": 0, "passed": True, "reasoning": "a ok"},
                {"index": 1, "passed": True, "reasoning": "b ok"},
                {"index": 2, "passed": False, "reasoning": "phantom"},
            ],
        })

        results = _read_verdict_file(tmp_path, exps)

        assert len(results) == 2
        assert results[0].earned == 1
        assert results[1].earned == 1

    def test_non_integer_index_ignored(self, tmp_path):
        exps = [Expectation(desc="a", score=1)]
        _write_verdict(tmp_path, {
            "results": [
                {"index": "0", "passed": True, "reasoning": "string index"},
            ],
        })

        results = _read_verdict_file(tmp_path, exps)

        assert results[0].earned == 0
        assert results[0].reasoning == "Missing from judge response"

    def test_results_not_a_list(self, tmp_path):
        exps = [Expectation(desc="a", score=1)]
        _write_verdict(tmp_path, {"results": "oops"})

        results = _read_verdict_file(tmp_path, exps)

        assert results[0].earned == 0
        assert results[0].reasoning.startswith("Could not read verdict.json")
