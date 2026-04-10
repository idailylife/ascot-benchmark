"""Tests for ascot.store."""

import json
from pathlib import Path

import pytest

from ascot.models import CaseResult, ExpectationResult, TestCase, BenchmarkReport
from ascot.store import RunStore


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path / "runs")


class TestNextRunDir:
    def test_first_run(self, store):
        run_id, run_dir = store.next_run_dir()
        assert run_id == "run-001"
        assert run_dir.exists()

    def test_increments(self, store):
        store.next_run_dir()
        run_id, _ = store.next_run_dir()
        assert run_id == "run-002"

    def test_finds_max(self, store):
        (store.base / "run-005").mkdir(parents=True)
        run_id, _ = store.next_run_dir()
        assert run_id == "run-006"


class TestSaveAndLoad:
    def test_save_meta(self, store):
        _, run_dir = store.next_run_dir()
        store.save_meta(run_dir, {"name": "test", "version": 1})
        data = json.loads((run_dir / "meta.json").read_text())
        assert data["name"] == "test"

    def test_save_eval(self, store):
        _, run_dir = store.next_run_dir()
        tc = TestCase(id="c1", prompt="do it", timeout_s=60, tags=["smoke"])
        store.save_eval(run_dir, "c1", tc)
        data = json.loads((run_dir / "c1" / "eval.json").read_text())
        assert data["id"] == "c1"
        assert data["prompt"] == "do it"
        assert data["tags"] == ["smoke"]

    def test_save_result(self, store):
        _, run_dir = store.next_run_dir()
        cr = CaseResult(case_id="c1", score=5, max_score=10)
        store.save_result(run_dir, "c1", cr)
        data = json.loads((run_dir / "c1" / "result.json").read_text())
        assert data["score"] == 5

    def test_save_events(self, store):
        _, run_dir = store.next_run_dir()
        events = [{"type": "start"}, {"type": "end"}]
        store.save_events(run_dir, "c1", events)
        lines = (run_dir / "c1" / "events.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["type"] == "start"

    def test_save_report(self, store):
        _, run_dir = store.next_run_dir()
        report = BenchmarkReport(suite_name="s", run_id="run-001", timestamp="t")
        store.save_report(run_dir, report)
        data = json.loads((run_dir / "report.json").read_text())
        assert data["suite_name"] == "s"

    def test_trial_dir_and_save(self, store):
        _, run_dir = store.next_run_dir()
        td = store.trial_dir(run_dir, "c1", 1)
        assert td.name == "trial-1"
        cr = CaseResult(case_id="c1", score=3, max_score=5)
        store.save_trial_result(run_dir, "c1", 1, cr)
        data = json.loads((td / "result.json").read_text())
        assert data["score"] == 3
