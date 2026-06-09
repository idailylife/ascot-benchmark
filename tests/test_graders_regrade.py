"""End-to-end test for graders.regrade_run.

Uses a test_script-only case so the LLM judge is skipped entirely — no client
mocking needed. Exercises run-dir discovery, the trial-dir and legacy branches,
aggregation, and report writing.
"""

import json
from pathlib import Path

from ascot.graders import regrade_run


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


def _passing_script(tmp_path: Path) -> Path:
    script = tmp_path / "test_v.py"
    script.write_text("def test_ok():\n    assert True\n")
    return script


def _eval(case_id: str, script: Path) -> dict:
    return {
        "id": case_id,
        "prompt": "do x",
        "expectations": [],  # no expectations -> judge skipped
        "test_script": script.name,
        "test_script_path": str(script),
        "test_script_timeout_s": 60.0,
    }


def _result_json() -> dict:
    return {"exit_code": 0, "duration_s": 1.5, "turns": 2,
            "token_usage": {"total": 10}, "total_cost": 0.05}


async def test_regrade_run_trial_and_legacy_branches(tmp_path):
    script = _passing_script(tmp_path)
    run_dir = tmp_path / "run-001"

    _write_json(run_dir / "meta.json",
                {"suite_name": "s", "trials": 1, "model": "m1"})

    # Case with trial subdirectory (trial branch)
    c1 = run_dir / "c1"
    _write_json(c1 / "eval.json", _eval("c1", script))
    (c1 / "trial-1" / "workspace").mkdir(parents=True)
    _write_json(c1 / "trial-1" / "result.json", _result_json())

    # Legacy case: no trial-* dirs, workspace + result.json directly
    c2 = run_dir / "c2"
    _write_json(c2 / "eval.json", _eval("c2", script))
    (c2 / "workspace").mkdir(parents=True)
    _write_json(c2 / "result.json", _result_json())

    report = await regrade_run(run_dir, client=None, concurrency=2)

    assert report.total == 2
    # both cases pass their single pytest test (1 pt each)
    assert report.total_score == 2
    assert report.max_score == 2
    # original agent metrics preserved through regrade
    assert report.total_turns == 4
    assert report.total_tokens == 20

    # report + case-level result files written
    assert (run_dir / "report.json").exists()
    assert (c1 / "result.json").exists()
    assert (c2 / "result.json").exists()


async def test_regrade_run_skips_case_without_eval(tmp_path):
    script = _passing_script(tmp_path)
    run_dir = tmp_path / "run-002"
    _write_json(run_dir / "meta.json", {"suite_name": "s", "trials": 1})

    # A directory with no eval.json must be skipped silently
    (run_dir / "stray").mkdir()

    c1 = run_dir / "c1"
    _write_json(c1 / "eval.json", _eval("c1", script))
    (c1 / "trial-1" / "workspace").mkdir(parents=True)
    _write_json(c1 / "trial-1" / "result.json", _result_json())

    report = await regrade_run(run_dir, client=None)
    assert report.total == 1
