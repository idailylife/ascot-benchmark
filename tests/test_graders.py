"""Tests for ascot.graders._read_verdict_file."""

import json
from types import SimpleNamespace

from ascot.graders import (
    _dump_judge_debug,
    _has_verdict_issue,
    _read_verdict_file,
)
from ascot.models import Expectation, ExpectationResult


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


def _fake_run_result(final_text: str):
    """Minimal RunResult-like object for _extract_text_from_result."""
    return SimpleNamespace(final_text=final_text, events=[])


class TestHasVerdictIssue:
    def test_clean_results(self):
        ers = [
            ExpectationResult(desc="a", score=1, earned=1, reasoning="ok"),
            ExpectationResult(desc="b", score=1, earned=0, reasoning="nope"),
        ]
        assert _has_verdict_issue(ers) is False

    def test_missing_from_response(self):
        ers = [
            ExpectationResult(desc="a", score=1, earned=0,
                              reasoning="Missing from judge response"),
        ]
        assert _has_verdict_issue(ers) is True

    def test_could_not_read(self):
        ers = [
            ExpectationResult(desc="a", score=1, earned=0,
                              reasoning="Could not read verdict.json: bad"),
        ]
        assert _has_verdict_issue(ers) is True


class TestDumpJudgeDebug:
    def test_dumps_verdict_and_text(self, tmp_path):
        judge_ws = tmp_path / "judge_ws"
        judge_ws.mkdir()
        (judge_ws / "verdict.json").write_text("not valid json{")

        dump_dir = tmp_path / "case"
        _dump_judge_debug(
            dump_dir, "", judge_ws,
            _fake_run_result("verdict written"),
            case_id="my_case",
        )

        assert (dump_dir / "verdict.bad.json").read_text() == "not valid json{"
        assert (dump_dir / "judge_response.bad.txt").read_text() == "verdict written"

    def test_dumps_with_retry_suffix(self, tmp_path):
        judge_ws = tmp_path / "judge_ws"
        judge_ws.mkdir()
        (judge_ws / "verdict.json").write_text("{}")

        dump_dir = tmp_path / "case"
        _dump_judge_debug(
            dump_dir, ".retry", judge_ws,
            _fake_run_result("retry text"),
            case_id="my_case",
        )

        assert (dump_dir / "verdict.bad.retry.json").read_text() == "{}"
        assert (dump_dir / "judge_response.bad.retry.txt").read_text() == "retry text"

    def test_no_verdict_file_still_dumps_text(self, tmp_path):
        judge_ws = tmp_path / "judge_ws"
        judge_ws.mkdir()
        # No verdict.json present

        dump_dir = tmp_path / "case"
        _dump_judge_debug(
            dump_dir, "", judge_ws,
            _fake_run_result("some text"),
            case_id="my_case",
        )

        assert not (dump_dir / "verdict.bad.json").exists()
        assert (dump_dir / "judge_response.bad.txt").read_text() == "some text"

    def test_empty_final_text_writes_placeholder(self, tmp_path, monkeypatch):
        judge_ws = tmp_path / "judge_ws"
        judge_ws.mkdir()

        # When final_text is empty, _extract_text_from_result falls through
        # to run_result_fuzzy_text; stub it so we don't need a real RunResult.
        monkeypatch.setattr(
            "opencode_wrapper.run_result_fuzzy_text", lambda r: "",
        )

        dump_dir = tmp_path / "case"
        _dump_judge_debug(
            dump_dir, "", judge_ws,
            _fake_run_result(""),
            case_id="my_case",
        )

        assert (dump_dir / "judge_response.bad.txt").read_text() == "(empty)"

    def test_creates_dump_dir_if_missing(self, tmp_path):
        judge_ws = tmp_path / "judge_ws"
        judge_ws.mkdir()

        dump_dir = tmp_path / "nested" / "case"
        assert not dump_dir.exists()

        _dump_judge_debug(
            dump_dir, "", judge_ws,
            _fake_run_result("text"),
            case_id="my_case",
        )

        assert dump_dir.is_dir()
        assert (dump_dir / "judge_response.bad.txt").exists()

    def test_swallows_errors(self, tmp_path, monkeypatch):
        """Debug dump failures must not propagate — they'd mask the real issue."""
        judge_ws = tmp_path / "judge_ws"
        judge_ws.mkdir()

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr("ascot.graders.shutil.copy2", boom)
        (judge_ws / "verdict.json").write_text("bad")

        # Should not raise
        _dump_judge_debug(
            tmp_path / "case", "", judge_ws,
            _fake_run_result("text"),
            case_id="my_case",
        )
