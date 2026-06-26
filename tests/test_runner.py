"""Tests for ascot.runner helpers."""

import json
from pathlib import Path

import pytest
from opencode_wrapper import (
    OpenCodeError,
    OpenCodeProcessError,
    OpenCodeTimeoutError,
    RunResult,
)

from ascot.models import CaseResult, TestCase, TestSuite
from ascot.runner import (
    CONTINUE_PROMPT,
    INSTRUCTION_REL,
    SENTINEL_REL,
    BenchmarkRunner,
    _LOG_EXCLUDE_TYPES,
    _is_startup_failure,
    _merge_results,
    _preserve_workspace_best_effort,
    _strip_delta_lines,
    build_permission,
    build_report,
)


class TestStripDeltaLines:
    def test_drops_deltas_keeps_reasoning_and_snapshots(self, tmp_path):
        p = tmp_path / "events.jsonl"
        p.write_text("".join(json.dumps(e) + "\n" for e in [
            {"type": "message.part.delta", "properties": {"delta": "Hel"}},
            {"type": "reasoning", "text": "thinking"},
            {"type": "message.part.delta", "properties": {"delta": "lo"}},
            {"type": "message.part.updated",
             "properties": {"part": {"id": "p1", "type": "text", "text": "Hello"}}},
        ]))
        _strip_delta_lines(p)
        types = [json.loads(l)["type"] for l in p.read_text().splitlines() if l.strip()]
        assert types == ["reasoning", "message.part.updated"]

    def test_missing_file_is_noop(self, tmp_path):
        _strip_delta_lines(tmp_path / "nope.jsonl")  # must not raise

    def test_preserves_malformed_lines(self, tmp_path):
        p = tmp_path / "events.jsonl"
        p.write_text("not json\n" + json.dumps(
            {"type": "message.part.delta", "properties": {}}) + "\n")
        _strip_delta_lines(p)
        assert p.read_text() == "not json\n"


class TestLogExcludeTypes:
    def test_excludes_deltas_only(self):
        assert isinstance(_LOG_EXCLUDE_TYPES, frozenset)
        assert "message.part.delta" in _LOG_EXCLUDE_TYPES
        assert "message.part.updated" not in _LOG_EXCLUDE_TYPES
        assert "reasoning" not in _LOG_EXCLUDE_TYPES


def test_build_permission_reads_json_with_schema_url(tmp_path):
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    (suite_dir / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "permission": {"bash": "deny"},
    }))

    permission = build_permission(suite_dir)

    assert permission["*"] == "allow"
    assert permission["bash"] == "deny"


def test_build_permission_reads_jsonc_and_nested_permission(tmp_path):
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    (suite_dir / "opencode.jsonc").write_text(
        """
        {
          // allow reads from a fixture path outside the workspace
          "$schema": "https://opencode.ai/config.json",
          "permission": {
            "external_directory": {
              "/tmp/fixtures/**": "allow",
            },
          },
        }
        """
    )

    permission = build_permission(suite_dir)

    assert permission["question"] == "deny"
    assert permission["external_directory"] == {"/tmp/fixtures/**": "allow"}


def test_preserve_workspace_best_effort_returns_duration(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "out.txt").write_text("ok")
    dest = tmp_path / "dest"

    duration = _preserve_workspace_best_effort(ws, dest)

    assert duration is not None
    assert (dest / "out.txt").read_text() == "ok"


def test_preserve_workspace_best_effort_swallows_errors(monkeypatch, tmp_path):
    def boom(ws, dest):
        raise OSError("disk full")

    monkeypatch.setattr("ascot.runner.preserve_workspace", boom)

    duration = _preserve_workspace_best_effort(tmp_path / "ws", tmp_path / "dest")

    assert duration is None


def _result(final_text="", *, turns=1, cost=1.0, tokens=10):
    r = RunResult(final_text=final_text, turns=turns, total_cost=cost)
    r.token_usage.total = tokens
    r.token_usage.input = tokens
    return r


class TestMergeResults:
    def test_empty(self):
        merged = _merge_results([])
        assert merged.turns == 0
        assert merged.total_cost == 0.0
        assert merged.final_text == ""

    def test_sums_stats(self):
        r1 = _result("a", turns=2, cost=1.5, tokens=10)
        r2 = _result("b", turns=3, cost=2.5, tokens=20)
        merged = _merge_results([r1, r2])
        assert merged.turns == 5
        assert merged.total_cost == 4.0
        assert merged.token_usage.total == 30
        assert merged.token_usage.input == 30

    def test_final_text_is_last_nonempty(self):
        r1 = _result("first")
        r2 = _result("last")
        r3 = _result("   ")  # blank-only: should be skipped
        merged = _merge_results([r1, r2, r3])
        assert merged.final_text == "last"

    def test_exit_code_from_last_session_from_first(self):
        r1 = _result("a")
        r1.exit_code, r1.session_id = 1, "sess-1"
        r2 = _result("b")
        r2.exit_code, r2.session_id = 0, "sess-2"
        merged = _merge_results([r1, r2])
        assert merged.exit_code == 0
        assert merged.session_id == "sess-1"


def _make_runner(tmp_path) -> BenchmarkRunner:
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    return BenchmarkRunner(
        suite_dir=suite_dir,
        test_suite=TestSuite(name="s"),
        output_dir=tmp_path / "out",
    )


class FakeSession:
    """Async-context-manager stand-in for OpenCodeSession.

    Records prompts; per-turn behavior driven by `script`: a list of callables
    `(session) -> RunResult` (raising to simulate a timeout). Optionally creates
    the completion sentinel before returning on a given 1-based turn.
    """

    def __init__(self, ws_dir, *, script, sentinel_on_turn=None, enter_error=None, **_):
        self.ws = Path(ws_dir)
        self.script = script
        self.sentinel_on_turn = sentinel_on_turn
        self.enter_error = enter_error
        self.prompts: list[str] = []

    async def __aenter__(self):
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, prompt, timeout_s=None):
        self.prompts.append(prompt)
        turn = len(self.prompts)
        if self.sentinel_on_turn == turn:
            sentinel = self.ws / SENTINEL_REL
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.touch()
        return self.script[turn - 1](self)


def _install_fake_session(monkeypatch, holder, **session_kwargs):
    def factory(client, ws_dir, **kwargs):
        sess = FakeSession(ws_dir, **session_kwargs)
        holder.append(sess)
        return sess

    monkeypatch.setattr("ascot.runner.OpenCodeSession", factory)


class TestRunSession:
    async def test_writes_instruction_file_into_system_prompt(self, tmp_path, monkeypatch):
        runner = _make_runner(tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        from opencode_wrapper import RunConfig

        captured = {}

        def factory(client, ws_dir, *, run_cfg=None, **kwargs):
            captured["cfg"] = run_cfg
            sess = FakeSession(ws_dir, script=[lambda s: _result("done")], sentinel_on_turn=1)
            return sess

        monkeypatch.setattr("ascot.runner.OpenCodeSession", factory)
        tc = TestCase(id="c", prompt="go", timeout_s=30, max_continues=2)
        await runner._run_session(tc, ws, RunConfig(), ws / "events.jsonl")

        instr_path = ws / INSTRUCTION_REL
        assert instr_path.exists()
        assert str(instr_path) in captured["cfg"].instructions

    async def test_stops_on_sentinel(self, tmp_path, monkeypatch):
        runner = _make_runner(tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        from opencode_wrapper import RunConfig

        holder: list[FakeSession] = []
        # 3 turns available, but sentinel created on turn 2 -> only 2 sends
        _install_fake_session(
            monkeypatch, holder,
            script=[lambda s: _result("t1"), lambda s: _result("t2"), lambda s: _result("t3")],
            sentinel_on_turn=2,
        )
        tc = TestCase(id="c", prompt="go", timeout_s=30, max_continues=5)
        merged = await runner._run_session(tc, ws, RunConfig(), ws / "events.jsonl")

        assert holder[0].prompts == ["go", CONTINUE_PROMPT]
        assert merged.turns == 2

    async def test_stops_at_max_continues(self, tmp_path, monkeypatch):
        runner = _make_runner(tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        from opencode_wrapper import RunConfig

        holder: list[FakeSession] = []
        _install_fake_session(
            monkeypatch, holder,
            script=[lambda s: _result(f"t{i}") for i in range(10)],
            sentinel_on_turn=None,  # never signals completion
        )
        tc = TestCase(id="c", prompt="go", timeout_s=30, max_continues=2)
        merged = await runner._run_session(tc, ws, RunConfig(), ws / "events.jsonl")

        # initial + 2 continues = 3 sends
        assert holder[0].prompts == ["go", CONTINUE_PROMPT, CONTINUE_PROMPT]
        assert merged.turns == 3

    async def test_stops_on_turn_timeout(self, tmp_path, monkeypatch):
        runner = _make_runner(tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        from opencode_wrapper import RunConfig

        def raise_timeout(s):
            raise OpenCodeTimeoutError("turn timed out")

        holder: list[FakeSession] = []
        _install_fake_session(
            monkeypatch, holder,
            script=[lambda s: _result("t1"), raise_timeout, lambda s: _result("t3")],
            sentinel_on_turn=None,
        )
        tc = TestCase(id="c", prompt="go", timeout_s=30, max_continues=5)
        merged = await runner._run_session(tc, ws, RunConfig(), ws / "events.jsonl")

        # turn 1 succeeded, turn 2 (a continue) raised -> loop breaks
        assert holder[0].prompts == ["go", CONTINUE_PROMPT]
        assert merged.turns == 1

    async def test_stops_on_budget_exhausted_before_any_send(self, tmp_path, monkeypatch):
        runner = _make_runner(tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        from opencode_wrapper import RunConfig

        holder: list[FakeSession] = []
        _install_fake_session(
            monkeypatch, holder,
            script=[lambda s: _result("t1")],
            sentinel_on_turn=None,
        )
        tc = TestCase(id="c", prompt="go", timeout_s=0, max_continues=5)
        merged = await runner._run_session(tc, ws, RunConfig(), ws / "events.jsonl")

        assert holder[0].prompts == []
        assert merged.turns == 0


class TestBuildReport:
    def test_sums_across_results(self):
        results = [
            CaseResult(case_id="a", score=3, max_score=5, turns=2,
                       token_usage={"total": 100}, total_cost=0.1, duration_s=1.0),
            CaseResult(case_id="b", score=5, max_score=5, turns=4,
                       token_usage={"total": 200}, total_cost=0.2, duration_s=2.0),
        ]
        report = build_report("suite", "run-001", results,
                              benchmark_model="m1", grading_model="m2")
        assert report.total == 2
        assert report.total_score == 8
        assert report.max_score == 10
        assert report.total_turns == 6
        assert report.total_tokens == 300
        assert abs(report.total_cost - 0.3) < 1e-9
        assert report.total_duration_s == 3.0
        assert report.benchmark_model == "m1"
        assert report.grading_model == "m2"

    def test_empty_results(self):
        report = build_report("suite", "run-001", [])
        assert report.total == 0
        assert report.total_score == 0
        assert report.max_score == 0


class TestResolveTestScript:
    def test_none_when_unset(self, tmp_path):
        runner = _make_runner(tmp_path)
        assert runner._resolve_test_script(TestCase(id="c", prompt="x")) is None

    def test_relative_resolves_against_testcases_dir(self, tmp_path):
        runner = _make_runner(tmp_path)
        runner.testcases_dir = tmp_path / "tc"
        tc = TestCase(id="c", prompt="x", test_script="verify.py")
        assert runner._resolve_test_script(tc) == (tmp_path / "tc" / "verify.py").resolve()

    def test_absolute_path_kept(self, tmp_path):
        runner = _make_runner(tmp_path)
        abs_script = (tmp_path / "abs" / "v.py").resolve()
        tc = TestCase(id="c", prompt="x", test_script=str(abs_script))
        assert runner._resolve_test_script(tc) == abs_script


class TestRunSingle:
    async def test_happy_path_grades_via_test_script(self, tmp_path, monkeypatch):
        runner = _make_runner(tmp_path)
        _, runner.run_dir = runner.store.next_run_dir()

        script = tmp_path / "test_verify.py"
        script.write_text("def test_ok():\n    assert True\n")

        _install_fake_session(
            monkeypatch, [],
            script=[lambda s: _result("done")], sentinel_on_turn=1,
        )

        tc = TestCase(id="c1", prompt="go", timeout_s=30, max_continues=0,
                      test_script=str(script))
        cr = await runner._run_single(tc, trial_num=1)

        assert cr.error is None
        assert cr.score == 1
        assert cr.max_score == 1
        assert cr.signaled_completion is True
        # result was persisted
        result_path = runner.store.trial_dir(runner.run_dir, "c1", 1) / "result.json"
        assert result_path.exists()

    async def test_error_path_returns_error_result(self, tmp_path, monkeypatch):
        runner = _make_runner(tmp_path)
        _, runner.run_dir = runner.store.next_run_dir()

        def boom(s):
            raise OpenCodeError("session blew up")

        _install_fake_session(
            monkeypatch, [], script=[boom], sentinel_on_turn=None,
        )

        tc = TestCase(id="c1", prompt="go", timeout_s=30, max_continues=0)
        cr = await runner._run_single(tc, trial_num=1)

        assert cr.score == 0
        assert "OpenCodeError" in cr.error
        result_path = runner.store.trial_dir(runner.run_dir, "c1", 1) / "result.json"
        assert result_path.exists()

    def _install_sequential_sessions(self, monkeypatch, sessions):
        """Hand out the queued FakeSessions in order across re-instantiations."""
        queue = list(sessions)
        created: list[FakeSession] = []

        def factory(client, ws_dir, **kwargs):
            sess = queue.pop(0)
            sess.ws = Path(ws_dir)
            created.append(sess)
            return sess

        monkeypatch.setattr("ascot.runner.OpenCodeSession", factory)
        return created

    async def test_retries_once_on_startup_failure(self, tmp_path, monkeypatch):
        runner = _make_runner(tmp_path)
        _, runner.run_dir = runner.store.next_run_dir()

        script = tmp_path / "test_verify.py"
        script.write_text("def test_ok():\n    assert True\n")

        startup_err = OpenCodeProcessError(
            exit_code=-1,
            stderr="opencode serve did not announce readiness in 15.0s\n",
        )
        failing = FakeSession(tmp_path, script=[], enter_error=startup_err)
        succeeding = FakeSession(
            tmp_path, script=[lambda s: _result("done")], sentinel_on_turn=1,
        )
        created = self._install_sequential_sessions(
            monkeypatch, [failing, succeeding],
        )

        tc = TestCase(id="c1", prompt="go", timeout_s=30, max_continues=0,
                      test_script=str(script))
        cr = await runner._run_single(tc, trial_num=1)

        assert len(created) == 2  # retried after the startup failure
        assert cr.error is None
        assert cr.score == 1

    async def test_no_retry_on_non_startup_error(self, tmp_path, monkeypatch):
        runner = _make_runner(tmp_path)
        _, runner.run_dir = runner.store.next_run_dir()

        generic = FakeSession(
            tmp_path, script=[], enter_error=OpenCodeError("boom"),
        )
        created = self._install_sequential_sessions(monkeypatch, [generic])

        tc = TestCase(id="c1", prompt="go", timeout_s=30, max_continues=0)
        cr = await runner._run_single(tc, trial_num=1)

        assert len(created) == 1  # no retry for a non-startup error
        assert cr.score == 0
        assert "OpenCodeError" in cr.error

    async def test_startup_failure_exhausts_retries(self, tmp_path, monkeypatch):
        runner = _make_runner(tmp_path)
        _, runner.run_dir = runner.store.next_run_dir()

        def _startup_err():
            return OpenCodeProcessError(
                exit_code=-1,
                stderr="opencode serve did not become healthy in 15.0s\n",
            )

        sessions = [
            FakeSession(tmp_path, script=[], enter_error=_startup_err()),
            FakeSession(tmp_path, script=[], enter_error=_startup_err()),
        ]
        created = self._install_sequential_sessions(monkeypatch, sessions)

        tc = TestCase(id="c1", prompt="go", timeout_s=30, max_continues=0)
        cr = await runner._run_single(tc, trial_num=1)

        assert len(created) == 2  # initial attempt + one retry, then gives up
        assert cr.score == 0
        assert "OpenCodeProcessError" in cr.error


class TestIsStartupFailure:
    def test_readiness_marker_is_retryable(self):
        e = OpenCodeProcessError(
            exit_code=-1,
            stderr="opencode serve did not announce readiness in 15.0s\n",
        )
        assert _is_startup_failure(e) is True

    def test_healthy_marker_is_retryable(self):
        e = OpenCodeProcessError(
            exit_code=-1,
            stderr="opencode serve did not become healthy in 15.0s\n",
        )
        assert _is_startup_failure(e) is True

    def test_other_process_error_not_retryable(self):
        e = OpenCodeProcessError(exit_code=1, stderr="some agent crash")
        assert _is_startup_failure(e) is False

    def test_non_process_error_not_retryable(self):
        assert _is_startup_failure(OpenCodeError("boom")) is False
