"""Tests for ascot.graders._read_verdict_file."""

import json
from pathlib import Path
from types import SimpleNamespace

from ascot.graders import (
    _copy_events_for_judge,
    _dump_judge_debug,
    _extract_stats,
    _extract_text_from_result,
    _has_verdict_issue,
    _list_workspace_files,
    _map_results,
    _read_verdict_file,
    _setup_judge_workspace,
    error_result,
)
from ascot.models import Expectation, ExpectationResult, TestCase


def _write_verdict(judge_ws, obj):
    (judge_ws / "verdict.json").write_text(json.dumps(obj))


class TestCopyEventsForJudge:
    def _lines(self, *events):
        return "".join(json.dumps(e) + "\n" for e in events)

    def test_strips_both_run_and_session_reasoning(self, tmp_path):
        src = tmp_path / "events.jsonl"
        src.write_text(self._lines(
            {"type": "reasoning", "text": "run-mode thinking"},
            {"type": "tool_use", "part": {"tool": "bash"}},
            {"type": "message.part.updated",
             "properties": {"part": {"type": "reasoning", "text": "sse thinking"}}},
            {"type": "message.part.updated",
             "properties": {"part": {"type": "text", "text": "answer"}}},
            {"type": "message.updated", "properties": {"info": {"role": "assistant"}}},
        ))
        dest = tmp_path / "out.jsonl"
        _copy_events_for_judge(src, dest)

        kept = [json.loads(l) for l in dest.read_text().splitlines() if l.strip()]
        types = [e["type"] for e in kept]
        assert types == ["tool_use", "message.part.updated", "message.updated"]
        # the surviving message.part.updated is the text part, not reasoning
        assert kept[1]["properties"]["part"]["type"] == "text"

    def test_strips_streaming_deltas(self, tmp_path):
        src = tmp_path / "events.jsonl"
        src.write_text(self._lines(
            {"type": "message.part.delta",
             "properties": {"delta": "Hel", "partID": "p1", "field": "text"}},
            {"type": "message.part.delta",
             "properties": {"delta": "lo", "partID": "p1", "field": "text"}},
            {"type": "message.part.updated",
             "properties": {"part": {"id": "p1", "type": "text", "text": "Hello"}}},
        ))
        dest = tmp_path / "out.jsonl"
        _copy_events_for_judge(src, dest)

        kept = [json.loads(l) for l in dest.read_text().splitlines() if l.strip()]
        types = [e["type"] for e in kept]
        assert types == ["message.part.updated"]

    def test_preserves_malformed_lines(self, tmp_path):
        src = tmp_path / "events.jsonl"
        src.write_text("not json\n" + json.dumps({"type": "reasoning"}) + "\n")
        dest = tmp_path / "out.jsonl"
        _copy_events_for_judge(src, dest)
        assert dest.read_text() == "not json\n"


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


class TestSetupJudgeWorkspace:
    """Guards the regression where _setup_judge_workspace called a renamed
    helper (_copy_events_without_reasoning) that no longer existed, crashing
    the judge for any case with expectations."""

    def test_copies_output_and_strips_events(self, tmp_path):
        case_dir = tmp_path / "case"
        (case_dir / "workspace").mkdir(parents=True)
        (case_dir / "workspace" / "result.txt").write_text("answer")
        (case_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in [
            {"type": "reasoning", "text": "thinking"},
            {"type": "message.part.delta", "properties": {"delta": "x"}},
            {"type": "tool_use", "part": {"tool": "bash"}},
        ]))

        judge_ws = _setup_judge_workspace(case_dir)

        assert (judge_ws / "output" / "result.txt").read_text() == "answer"
        kept = [json.loads(l) for l in (judge_ws / "events.jsonl").read_text().splitlines() if l.strip()]
        assert [e["type"] for e in kept] == ["tool_use"]

    def test_handles_missing_workspace_and_events(self, tmp_path):
        case_dir = tmp_path / "case"
        case_dir.mkdir()
        judge_ws = _setup_judge_workspace(case_dir)
        assert judge_ws.is_dir()
        assert not (judge_ws / "output").exists()
        assert not (judge_ws / "events.jsonl").exists()


class TestListWorkspaceFiles:
    def test_lists_files_excluding_opencode(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / ".opencode").mkdir()
        (tmp_path / ".opencode" / "config.json").write_text("{}")

        listing = _list_workspace_files(tmp_path)

        assert "a.txt" in listing
        assert "config.json" not in listing

    def test_empty_dir(self, tmp_path):
        assert _list_workspace_files(tmp_path) == "  (no files)"

    def test_truncates_at_max_files(self, tmp_path):
        for i in range(5):
            (tmp_path / f"f{i}.txt").write_text("x")
        listing = _list_workspace_files(tmp_path, max_files=2)
        assert "truncated at 2" in listing


class TestExtractStats:
    def test_with_token_usage(self):
        tu = SimpleNamespace(total=100, input=60, output=40,
                             reasoning=5, cache_read=10, cache_write=2)
        rr = SimpleNamespace(token_usage=tu, total_cost=0.5, turns=3)
        stats = _extract_stats(rr)
        assert stats["tokens"]["total"] == 100
        assert stats["tokens"]["cache_write"] == 2
        assert stats["cost"] == 0.5
        assert stats["turns"] == 3

    def test_without_token_usage(self):
        rr = SimpleNamespace(total_cost=0.0, turns=1)  # no token_usage attr
        stats = _extract_stats(rr)
        assert stats["tokens"] == {}
        assert stats["turns"] == 1


class TestExtractTextFromResult:
    def test_returns_final_text(self):
        rr = SimpleNamespace(final_text="the answer", events=[])
        assert _extract_text_from_result(rr) == "the answer"

    def test_falls_back_to_fuzzy_on_json_leak(self, monkeypatch):
        monkeypatch.setattr("opencode_wrapper.run_result_fuzzy_text",
                            lambda r: "clean fuzzy text")
        rr = SimpleNamespace(final_text='{"type": "tool"}', events=[])
        assert _extract_text_from_result(rr) == "clean fuzzy text"

    def test_falls_back_to_tool_outputs(self, monkeypatch):
        monkeypatch.setattr("opencode_wrapper.run_result_fuzzy_text", lambda r: "")
        rr = SimpleNamespace(
            final_text="",
            events=[
                {"type": "tool_use",
                 "part": {"state": {"output": "computed result"}}},
                {"type": "tool_use",
                 "part": {"state": {"output": "<html ignored>"}}},
            ],
        )
        assert _extract_text_from_result(rr) == "computed result"


class TestMapResults:
    def test_maps_by_index_and_marks_missing(self):
        exps = [Expectation(desc="a", score=5), Expectation(desc="b", score=3)]
        raw = [{"index": 0, "passed": True, "reasoning": "good"}]
        results = _map_results(raw, exps)
        assert results[0].earned == 5
        assert results[0].reasoning == "good"
        assert results[1].earned == 0
        assert results[1].reasoning == "Missing from judge response"


class TestAppendGradingPrompt:
    class _FakeClient:
        def __init__(self):
            self.last_prompt = None

        async def async_run(self, prompt, ws, run_cfg=None, timeout_s=None):
            self.last_prompt = prompt
            # Write a valid verdict so llm_judge succeeds.
            (Path(ws) / "verdict.json").write_text(json.dumps(
                {"results": [{"index": 0, "passed": True, "reasoning": "ok"}]}))
            return SimpleNamespace(
                final_text="verdict written", events=[],
                exit_code=0, total_cost=0.0, turns=1,
            )

    async def test_appended_text_in_judge_prompt(self, tmp_path):
        from ascot.graders import llm_judge

        (tmp_path / "workspace").mkdir()
        tc = TestCase(
            id="c", prompt="go",
            expectations=[Expectation(desc="a", score=1)],
            append_grading_prompt="IMPORTANT: read .foo files as JSON.",
        )
        client = self._FakeClient()
        await llm_judge(tmp_path, tc, client)
        assert "IMPORTANT: read .foo files as JSON." in client.last_prompt

    async def test_no_append_when_unset(self, tmp_path):
        from ascot.graders import llm_judge

        (tmp_path / "workspace").mkdir()
        tc = TestCase(
            id="c", prompt="go",
            expectations=[Expectation(desc="a", score=1)],
        )
        client = self._FakeClient()
        await llm_judge(tmp_path, tc, client)
        assert client.last_prompt.rstrip().endswith('"verdict written").')


class TestErrorResult:
    def test_with_test_case_sums_max_score(self):
        tc = TestCase(id="c", prompt="go", expectations=[
            Expectation(desc="a", score=5), Expectation(desc="b", score=3)])
        cr = error_result("c", ValueError("boom"), tc)
        assert cr.score == 0
        assert cr.max_score == 8
        assert cr.error == "ValueError: boom"

    def test_without_test_case(self):
        cr = error_result("c", RuntimeError("x"))
        assert cr.max_score == 0
        assert cr.error == "RuntimeError: x"
