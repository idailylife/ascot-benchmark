"""Tests for ascot.runner helpers."""

import json
from pathlib import Path

import pytest
from opencode_wrapper import OpenCodeTimeoutError, RunResult

from ascot.models import TestCase, TestSuite
from ascot.runner import (
    CONTINUE_PROMPT,
    INSTRUCTION_REL,
    SENTINEL_REL,
    BenchmarkRunner,
    _merge_results,
    _preserve_workspace_best_effort,
    build_permission,
)


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

    def __init__(self, ws_dir, *, script, sentinel_on_turn=None, **_):
        self.ws = Path(ws_dir)
        self.script = script
        self.sentinel_on_turn = sentinel_on_turn
        self.prompts: list[str] = []

    async def __aenter__(self):
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
