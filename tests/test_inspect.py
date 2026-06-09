"""Tests for ascot.inspect event parsing and formatting."""

import json

import pytest

from ascot.inspect import (
    CaseTrace,
    _fmt_ms,
    _parse_session_events,
    _tool_detail,
    format_trace_json,
    parse_events,
)


class TestToolDetail:
    def test_read_write_edit_return_basename(self):
        assert _tool_detail("read", {"input": {"filePath": "/a/b/c.py"}}) == "c.py"
        assert _tool_detail("write", {"input": {"file_path": "/x/y.txt"}}) == "y.txt"
        assert _tool_detail("edit", {"input": {"filePath": "/d/e.go"}}) == "e.go"

    def test_glob_grep_return_pattern(self):
        assert _tool_detail("glob", {"input": {"pattern": "**/*.py"}}) == "**/*.py"
        assert _tool_detail("grep", {"input": {"pattern": "TODO"}}) == "TODO"

    def test_bash_truncates_at_60_chars(self):
        cmd = "echo " + "x" * 100
        detail = _tool_detail("bash", {"input": {"command": cmd}})
        assert detail.endswith("...")
        assert len(detail) == 63  # 60 chars + "..."

    def test_skill_returns_name(self):
        assert _tool_detail("skill", {"input": {"skill": "review"}}) == "review"

    def test_unknown_tool_and_none(self):
        assert _tool_detail("unknown", {"input": {}}) is None
        assert _tool_detail(None, {}) is None
        assert _tool_detail("read", {"input": "not-a-dict"}) is None


class TestFmtMs:
    def test_sub_second(self):
        assert _fmt_ms(500) == "500ms"

    def test_seconds(self):
        assert _fmt_ms(1500) == "1.5s"


def _write_events(case_dir, events):
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events))


class TestParseEventsRunMode:
    def test_parses_steps_and_aggregates(self, tmp_path):
        case_dir = tmp_path / "my_case"
        _write_events(case_dir, [
            {"type": "step_start", "timestamp": 1000},
            {"type": "tool_use", "part": {
                "tool": "bash", "callID": "t1",
                "state": {"status": "completed", "input": {"command": "ls"},
                          "time": {"start": 1100, "end": 1300}}}},
            {"type": "step_finish", "timestamp": 1400, "part": {
                "tokens": {"input": 50, "output": 20}, "cost": 0.01, "reason": "stop"}},
        ])

        trace = parse_events(case_dir)

        assert trace.case_id == "my_case"
        assert len(trace.steps) == 1
        step = trace.steps[0]
        assert step.tool_name == "bash"
        assert step.tool_time_ms == 200  # 1300 - 1100
        assert step.reasoning_ms == 100  # 1100 - 1000
        assert trace.total_duration_ms == 400  # 1400 - 1000
        assert trace.total_tokens == {"input": 50, "output": 20}
        assert trace.total_cost == 0.01

    def test_missing_events_file_raises(self, tmp_path):
        case_dir = tmp_path / "empty"
        case_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            parse_events(case_dir)


class TestParseSessionEvents:
    def test_dedupes_by_part_id_and_sums_tokens(self):
        events = [
            {"type": "message.part.updated", "properties": {"part": {
                "id": "p1", "type": "tool", "tool": "bash", "callID": "c1",
                "state": {"status": "running", "time": {"start": 100}}}}},
            # same part id replaced with final snapshot -> one step, not two
            {"type": "message.part.updated", "properties": {"part": {
                "id": "p1", "type": "tool", "tool": "bash", "callID": "c1",
                "state": {"status": "completed", "time": {"start": 100, "end": 300}}}}},
            {"type": "message.updated", "properties": {"info": {
                "role": "assistant", "id": "m1",
                "tokens": {"input": 10, "output": 5}, "cost": 0.02}}},
        ]
        trace = _parse_session_events("c1", events)
        assert len(trace.steps) == 1
        assert trace.steps[0].tool_time_ms == 200
        assert trace.total_tokens == {"input": 10, "output": 5}
        assert trace.total_cost == 0.02

    def test_parse_events_dispatches_to_session_parser(self, tmp_path):
        case_dir = tmp_path / "sess_case"
        _write_events(case_dir, [
            {"type": "message.part.updated", "properties": {"part": {
                "id": "p1", "type": "tool", "tool": "read", "callID": "c1",
                "state": {"status": "completed", "input": {"filePath": "/a.py"},
                          "time": {"start": 0, "end": 50}}}}},
        ])
        trace = parse_events(case_dir)
        assert len(trace.steps) == 1
        assert trace.steps[0].tool_detail == "a.py"


class TestFormatTraceJson:
    def test_roundtrips_structure(self):
        trace = CaseTrace(case_id="c1", total_cost=0.5)
        data = json.loads(format_trace_json(trace))
        assert data["case_id"] == "c1"
        assert data["total_cost"] == 0.5
        assert data["steps"] == []
