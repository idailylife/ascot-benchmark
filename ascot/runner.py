"""Benchmark runner: orchestrates OpenCode execution and grading."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from opencode_wrapper import (
    AsyncOpenCodeClient,
    OpenCodeError,
    RunConfig,
    RunResult,
)

from .graders import error_result, grade_case
from .models import BenchmarkReport, CaseResult, TestCase, TestSuite
from .store import RunStore
from .workspace import cleanup_workspace, preserve_workspace, setup_workspace

log = logging.getLogger(__name__)

# Default permissions: allow all tools, deny non-interactive / safety risks.
# Users can override individual keys in their suite's opencode.json.
DEFAULT_PERMISSION: dict[str, str] = {
    "*": "allow",
    "question": "deny",
    "external_directory": "deny",
    "doom_loop": "deny",
}


def _load_suite_permission(suite_dir: Path) -> dict[str, str]:
    """Read user permission overrides from the suite's opencode.json."""
    for name in ("opencode.json", "opencode.jsonc"):
        cfg_path = suite_dir / name
        if cfg_path.exists():
            import json
            try:
                with open(cfg_path) as f:
                    data = json.loads(
                        # Strip JSONC comments (// and /* */) for .jsonc
                        _strip_jsonc_comments(f.read())
                    )
                return data.get("permission", {})
            except (json.JSONDecodeError, OSError):
                pass
    return {}


def _strip_jsonc_comments(text: str) -> str:
    """Minimal JSONC comment stripper for // and /* */ comments."""
    import re
    # Remove single-line comments
    text = re.sub(r'//.*?$', '', text, flags=re.MULTILINE)
    # Remove multi-line comments
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    return text


def build_permission(suite_dir: Path) -> dict[str, str]:
    """Merge default permissions with user overrides from suite config.

    Default provides a safe base; user's suite opencode.json can override
    individual keys (e.g. deny bash for a read-only suite).
    """
    merged = dict(DEFAULT_PERMISSION)
    user_overrides = _load_suite_permission(suite_dir)
    merged.update(user_overrides)
    return merged


def build_report(
    suite_name: str, run_id: str, results: list[CaseResult]
) -> BenchmarkReport:
    total = len(results)
    total_score = sum(r.score for r in results)
    max_score = sum(r.max_score for r in results)
    total_turns = sum(r.turns for r in results)
    total_tokens = sum(r.token_usage.get("total", 0) for r in results)
    total_duration = sum(r.duration_s for r in results)
    total_cost = sum(r.total_cost for r in results)
    return BenchmarkReport(
        suite_name=suite_name,
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        results=results,
        total=total,
        total_score=total_score,
        max_score=max_score,
        total_turns=total_turns,
        total_tokens=total_tokens,
        total_duration_s=total_duration,
        total_cost=total_cost,
    )


class BenchmarkRunner:
    """Runs a test suite against a suite configuration via OpenCode."""

    def __init__(
        self,
        suite_dir: Path,
        test_suite: TestSuite,
        output_dir: Path,
        *,
        concurrency: int = 2,
        model: str | None = None,
        binary: str = "opencode",
        testcases_dir: Path | None = None,
        venv: Path | None = None,
    ):
        self.suite_dir = suite_dir
        self.test_suite = test_suite
        self.output_dir = output_dir
        self.model = model or test_suite.default_model
        self.testcases_dir = testcases_dir
        self.concurrency = concurrency
        self.permission = build_permission(suite_dir)
        self.venv = venv

        self.client = AsyncOpenCodeClient(
            binary=binary,
            startup_concurrency=1,
            startup_delay_s=0.3,
            isolate_db=True,
        )
        self.sem = asyncio.Semaphore(concurrency)
        self.store = RunStore(output_dir)

    async def run_all(self) -> BenchmarkReport:
        """Run all test cases and return a BenchmarkReport."""
        run_id, run_dir = self.store.next_run_dir()
        self.run_dir = run_dir

        self.store.save_meta(run_dir, {
            "suite_name": self.test_suite.name,
            "model": self.model,
            "concurrency": self.concurrency,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_cases": len(self.test_suite.test_cases),
        })

        tasks = [
            asyncio.create_task(self._run_guarded(tc))
            for tc in self.test_suite.test_cases
        ]
        results = await asyncio.gather(*tasks)

        report = build_report(self.test_suite.name, run_id, results)
        self.store.save_report(run_dir, report)
        return report

    async def _run_guarded(self, tc: TestCase) -> CaseResult:
        async with self.sem:
            return await self._run_single(tc)

    async def _run_single(self, tc: TestCase) -> CaseResult:
        log.info("Running case: %s", tc.id)
        self.store.save_eval(self.run_dir, tc.id, tc)
        phases: dict[str, dict] = {}

        t_ws = time.monotonic()
        ws = setup_workspace(self.suite_dir, tc, self.testcases_dir)
        phases["workspace_setup"] = {"duration_s": round(time.monotonic() - t_ws, 3)}

        try:
            # If user provided a venv, inject it into PATH
            extra_env = None
            if self.venv:
                import os
                venv_bin = self.venv / "bin"
                if venv_bin.is_dir():
                    extra_env = {
                        "PATH": f"{venv_bin}:{os.environ.get('PATH', '')}",
                        "VIRTUAL_ENV": str(self.venv),
                    }

            cfg = RunConfig(
                model=tc.model or self.model,
                agent=tc.agent,
                permission=self.permission,
                extra_env=extra_env,
            )
            t0 = time.monotonic()
            result = await self.client.async_run(
                tc.prompt, str(ws), run_cfg=cfg, timeout_s=tc.timeout_s
            )
            duration = time.monotonic() - t0

            agent_stats = {
                "duration_s": round(duration, 3),
                "turns": result.turns,
                "cost": result.total_cost,
            }
            if hasattr(result, "token_usage"):
                tu = result.token_usage
                agent_stats["tokens"] = {
                    "total": tu.total, "input": tu.input,
                    "output": tu.output, "reasoning": tu.reasoning,
                    "cache_read": tu.cache_read, "cache_write": tu.cache_write,
                }
            phases["agent_run"] = agent_stats

            # Save raw events
            self.store.save_events(self.run_dir, tc.id, result.events)

            # Preserve workspace output
            t_pres = time.monotonic()
            ws_dest = self.store.case_dir(self.run_dir, tc.id) / "workspace"
            preserve_workspace(ws, ws_dest)
            phases["workspace_preserve"] = {"duration_s": round(time.monotonic() - t_pres, 3)}

            # Grade
            t_grade = time.monotonic()
            case_dir = self.store.case_dir(self.run_dir, tc.id)
            case_result, grading_stats = await grade_case(tc, case_dir, result, duration, self.client)
            grading_stats["duration_s"] = round(time.monotonic() - t_grade, 3)
            phases["grading"] = grading_stats

            case_result.phases = phases
            self.store.save_result(self.run_dir, tc.id, case_result)

            log.info("Case %s: %d/%d (turns=%d, tokens=%d, %.1fs)",
                     tc.id, case_result.score, case_result.max_score,
                     case_result.turns,
                     case_result.token_usage.get("total", 0), duration)
            return case_result

        except OpenCodeError as e:
            log.error("Case %s error: %s", tc.id, e)
            cr = error_result(tc.id, e, tc)
            cr.phases = phases
            self.store.save_result(self.run_dir, tc.id, cr)
            return cr
        finally:
            cleanup_workspace(ws)
