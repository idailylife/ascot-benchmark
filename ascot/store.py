"""Run output directory management."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import BenchmarkReport, CaseResult, TestCase


class RunStore:
    """Manages a run-NNN output directory structure."""

    def __init__(self, base_dir: Path):
        self.base = base_dir
        self.base.mkdir(parents=True, exist_ok=True)

    def next_run_dir(self) -> tuple[str, Path]:
        """Find the next available run-NNN directory."""
        existing = sorted(self.base.glob("run-*"))
        next_num = 1
        for d in existing:
            try:
                num = int(d.name.split("-", 1)[1])
                next_num = max(next_num, num + 1)
            except (ValueError, IndexError):
                pass
        run_id = f"run-{next_num:03d}"
        run_dir = self.base / run_id
        run_dir.mkdir(parents=True)
        return run_id, run_dir

    def case_dir(self, run_dir: Path, case_id: str) -> Path:
        d = run_dir / case_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_meta(self, run_dir: Path, meta: dict[str, Any]) -> None:
        _write_json(run_dir / "meta.json", meta)

    def save_eval(self, run_dir: Path, case_id: str, test_case: TestCase) -> None:
        cd = self.case_dir(run_dir, case_id)
        data = {
            "id": test_case.id,
            "prompt": test_case.prompt,
            "expectations": [{"desc": e.desc, "score": e.score} for e in test_case.expectations],
            "timeout_s": test_case.timeout_s,
            "model": test_case.model,
            "tags": test_case.tags,
        }
        _write_json(cd / "eval.json", data)

    def save_result(self, run_dir: Path, case_id: str, result: CaseResult) -> None:
        cd = self.case_dir(run_dir, case_id)
        _write_json(cd / "result.json", result.to_dict())

    def save_events(self, run_dir: Path, case_id: str, events: list[dict]) -> None:
        cd = self.case_dir(run_dir, case_id)
        with open(cd / "events.jsonl", "w") as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def save_report(self, run_dir: Path, report: BenchmarkReport) -> None:
        _write_json(run_dir / "report.json", report.to_dict())


def _write_json(path: Path, data: Any) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
