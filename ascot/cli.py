"""CLI entry point for Ascot benchmark framework."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ascot",
        description="Benchmark framework for evaluating OpenCode suites",
    )
    parser.add_argument("--version", action="version", version=f"ascot {__version__}")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- run ---
    run_p = subparsers.add_parser("run", help="Run benchmark suite")
    run_p.add_argument("suite_dir", help="Path to suite directory")
    run_p.add_argument("testcases", help="Path to test cases YAML file or directory")
    run_p.add_argument("--output", "-o", default="./benchmark", help="Output directory")
    run_p.add_argument("--model", "-m", help="Override model for all cases")
    run_p.add_argument("--concurrency", "-c", type=int, default=2, help="Parallel case limit")
    run_p.add_argument("--timeout", "-t", type=float, help="Override per-case timeout (seconds)")
    run_p.add_argument("--binary", default="opencode", help="OpenCode binary path")
    run_p.add_argument("--tag", action="append", help="Only run cases with this tag")
    run_p.add_argument("--show-cost", action="store_true", help="Show cost in report")
    run_p.add_argument("--venv", help="Path to pre-configured virtual environment")
    run_p.add_argument("--format", "-f", choices=["terminal", "json"], default="terminal")

    # --- grade ---
    grade_p = subparsers.add_parser("grade", help="Re-grade an existing run")
    grade_p.add_argument("run_dir", help="Path to run output directory")
    grade_p.add_argument("--binary", default="opencode", help="OpenCode binary path")

    # --- report ---
    report_p = subparsers.add_parser("report", help="Generate report from existing run")
    report_p.add_argument("run_dir", help="Path to run output directory")
    report_p.add_argument("--format", "-f", choices=["terminal", "json"], default="terminal")
    report_p.add_argument("--show-cost", action="store_true", help="Show cost in report")

    # --- inspect ---
    inspect_p = subparsers.add_parser("inspect", help="Analyze case execution events")
    inspect_p.add_argument("case_dir", help="Path to case output directory")
    inspect_p.add_argument("--format", "-f", choices=["terminal", "json"], default="terminal")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "run":
        asyncio.run(_cmd_run(args))
    elif args.command == "grade":
        asyncio.run(_cmd_grade(args))
    elif args.command == "report":
        _cmd_report(args)
    elif args.command == "inspect":
        _cmd_inspect(args)


async def _cmd_run(args: argparse.Namespace) -> None:
    from .report import format_json, format_terminal
    from .runner import BenchmarkRunner
    from .suite import load_test_suite, resolve_suite

    suite_dir = resolve_suite(args.suite_dir)
    test_suite = load_test_suite(args.testcases)

    # Apply CLI overrides
    if args.timeout:
        for tc in test_suite.test_cases:
            tc.timeout_s = args.timeout

    # Filter by tags
    if args.tag:
        tag_set = set(args.tag)
        test_suite.test_cases = [
            tc for tc in test_suite.test_cases
            if tag_set.intersection(tc.tags)
        ]
        if not test_suite.test_cases:
            print(f"No test cases match tags: {args.tag}", file=sys.stderr)
            sys.exit(1)

    testcases_dir = Path(args.testcases).resolve()
    if testcases_dir.is_file():
        testcases_dir = testcases_dir.parent

    venv_path = Path(args.venv).resolve() if args.venv else None
    if venv_path and not (venv_path / "bin").is_dir():
        print(f"Invalid venv path (no bin/ dir): {venv_path}", file=sys.stderr)
        sys.exit(1)

    runner = BenchmarkRunner(
        suite_dir=suite_dir,
        test_suite=test_suite,
        output_dir=Path(args.output),
        concurrency=args.concurrency,
        model=args.model,
        binary=args.binary,
        testcases_dir=testcases_dir,
        venv=venv_path,
    )

    print(f"Running {len(test_suite.test_cases)} test case(s) from suite '{test_suite.name}'...")
    report = await runner.run_all()

    if args.format == "json":
        print(format_json(report))
    else:
        print(format_terminal(report, show_cost=args.show_cost))


async def _cmd_grade(args: argparse.Namespace) -> None:
    """Re-grade an existing run using LLM judge."""
    from opencode_wrapper import AsyncOpenCodeClient

    from .graders import grade_case
    from .models import CaseResult, Expectation
    from .report import format_terminal
    from .runner import build_report
    from .store import RunStore, _write_json

    run_dir = Path(args.run_dir).resolve()
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        print(f"No meta.json found in {run_dir}", file=sys.stderr)
        sys.exit(1)

    with open(meta_path) as f:
        meta = json.load(f)

    client = AsyncOpenCodeClient(
        binary=args.binary, isolate_db=True,
        startup_concurrency=1, startup_delay_s=0.3,
    )

    # Re-grade each case that has a workspace and eval.json
    results: list[CaseResult] = []
    for case_dir in sorted(run_dir.iterdir()):
        eval_path = case_dir / "eval.json"
        ws_path = case_dir / "workspace"
        result_path = case_dir / "result.json"
        if not eval_path.exists() or not ws_path.exists():
            continue

        with open(eval_path) as f:
            eval_data = json.load(f)
        with open(result_path) as f:
            result_data = json.load(f)

        from .models import TestCase
        expectations = [
            Expectation(desc=e["desc"], score=e.get("score", 1))
            for e in eval_data.get("expectations", [])
        ]
        tc = TestCase(
            id=eval_data["id"],
            prompt=eval_data["prompt"],
            expectations=expectations,
        )

        # Create a mock RunResult-like object for grading
        from opencode_wrapper import RunResult
        mock_result = RunResult()
        mock_result.exit_code = result_data.get("exit_code")

        cr, _grading_stats = await grade_case(tc, case_dir, mock_result,
                              result_data.get("duration_s", 0.0), client)
        # Preserve original metrics
        cr.turns = result_data.get("turns", 0)
        cr.token_usage = result_data.get("token_usage", {})
        cr.total_cost = result_data.get("total_cost", 0.0)
        cr.duration_s = result_data.get("duration_s", 0.0)
        results.append(cr)

        # Overwrite result.json
        _write_json(result_path, cr.to_dict())

    run_id = run_dir.name
    report = build_report(meta.get("suite_name", "unknown"), run_id, results)

    _write_json(run_dir / "report.json", report.to_dict())

    print(format_terminal(report))


def _cmd_report(args: argparse.Namespace) -> None:
    """Display report from an existing run."""
    from .models import BenchmarkReport, CaseResult, ExpectationResult

    run_dir = Path(args.run_dir).resolve()
    report_path = run_dir / "report.json"
    if not report_path.exists():
        print(f"No report.json found in {run_dir}", file=sys.stderr)
        sys.exit(1)

    with open(report_path) as f:
        data = json.load(f)

    # Reconstruct report from JSON
    results = []
    for r in data.get("results", []):
        expectation_results = [
            ExpectationResult(
                desc=er["desc"],
                score=er["score"],
                earned=er["earned"],
                reasoning=er.get("reasoning", ""),
            )
            for er in r.get("expectation_results", [])
        ]
        results.append(CaseResult(
            case_id=r["case_id"],
            score=r.get("score", 0),
            max_score=r.get("max_score", 0),
            expectation_results=expectation_results,
            token_usage=r.get("token_usage", {}),
            total_cost=r.get("total_cost", 0.0),
            turns=r.get("turns", 0),
            duration_s=r.get("duration_s", 0.0),
            error=r.get("error"),
        ))

    report = BenchmarkReport(
        suite_name=data["suite_name"],
        run_id=data["run_id"],
        timestamp=data["timestamp"],
        results=results,
        total=data["total"],
        total_score=data.get("total_score", 0),
        max_score=data.get("max_score", 0),
        total_turns=data.get("total_turns", 0),
        total_tokens=data.get("total_tokens", 0),
        total_duration_s=data.get("total_duration_s", 0.0),
        total_cost=data.get("total_cost", 0.0),
    )

    from .report import format_json, format_terminal

    show_cost = getattr(args, "show_cost", False)
    if args.format == "json":
        print(format_json(report))
    else:
        print(format_terminal(report, show_cost=show_cost))


def _cmd_inspect(args: argparse.Namespace) -> None:
    """Analyze case execution events for performance debugging."""
    from .inspect import format_trace_json, format_trace_terminal, parse_events

    case_dir = Path(args.case_dir).resolve()
    if not (case_dir / "events.jsonl").exists():
        print(f"No events.jsonl found in {case_dir}", file=sys.stderr)
        sys.exit(1)

    trace = parse_events(case_dir)

    if args.format == "json":
        print(format_trace_json(trace))
    else:
        print(format_trace_terminal(trace))
