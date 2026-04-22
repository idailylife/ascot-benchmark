"""Grading: LLM judge evaluates expectations and assigns scores."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

log = logging.getLogger(__name__)

from .models import CaseResult, Expectation, ExpectationResult, TestCase

if TYPE_CHECKING:
    from opencode_wrapper import AsyncOpenCodeClient, RunResult


# ---------------------------------------------------------------------------
# LLM Judge
# ---------------------------------------------------------------------------

# Judge gets full permissions inside its own workspace so it can write
# and run scripts to inspect binary files (xlsx, docx, images, etc.).
JUDGE_PERMISSION: dict[str, str] = {
    "*": "allow",
    "question": "deny",
    "external_directory": "deny",
    "doom_loop": "deny",
}


def _setup_judge_workspace(case_dir: Path) -> Path:
    """Create an isolated workspace for the judge with case artifacts."""
    judge_ws = Path(tempfile.mkdtemp(prefix="ascot_judge_"))

    # Copy output files
    ws_src = case_dir / "workspace"
    if ws_src.is_dir():
        shutil.copytree(ws_src, judge_ws / "output")

    # Copy events log
    events_src = case_dir / "events.jsonl"
    if events_src.exists():
        shutil.copy2(events_src, judge_ws / "events.jsonl")

    return judge_ws


def _list_workspace_files(ws: Path, max_files: int = 50) -> str:
    """List files in workspace (excluding .opencode/) for the judge prompt."""
    files: list[str] = []
    for item in sorted(ws.rglob("*")):
        if ".opencode" in item.parts:
            continue
        if item.is_file():
            rel = item.relative_to(ws)
            size = item.stat().st_size
            files.append(f"  {rel}  ({size} bytes)")
            if len(files) >= max_files:
                files.append(f"  ... and more files (truncated at {max_files})")
                break
    return "\n".join(files) if files else "  (no files)"


async def llm_judge(
    case_dir: Path,
    test_case: TestCase,
    client: "AsyncOpenCodeClient",
    grading_model: str | None = None,
) -> tuple[list[ExpectationResult], dict]:
    """Run an OpenCode session to judge output against all expectations.

    Reads events.jsonl and workspace/ from case_dir to provide the judge
    with the agent's full execution trace and output files.

    Returns (expectation_results, judge_stats) where judge_stats contains
    token usage, cost, and turns from the judge LLM session.
    """
    from opencode_wrapper import RunConfig

    empty_stats: dict = {"tokens": {}, "cost": 0.0, "turns": 0}

    judge_ws = _setup_judge_workspace(case_dir)
    try:
        file_listing = _list_workspace_files(judge_ws / "output") if (judge_ws / "output").is_dir() else "(no files)"

        # Build numbered expectations list
        exp_lines = []
        for i, exp in enumerate(test_case.expectations):
            exp_lines.append(f"{i + 1}. {exp.desc} ({exp.score} pts)")
        exp_list = "\n".join(exp_lines)

        sections = [
            "You are a grading judge. Evaluate whether the agent's output "
            "meets EACH of the following expectations.\n",
            f"## Expectations\n{exp_list}\n",
            "## Available Evidence\n"
            "- `events.jsonl`: The agent's complete execution log (JSON lines). "
            "Each line is an event showing the agent's reasoning, tool calls, "
            "and outputs. Read this file to understand what the agent did.\n"
            "- `output/`: Directory containing all files the agent produced. "
            "Inspect these files to verify the agent's work.\n",
        ]

        if file_listing.strip() and file_listing.strip() != "(no files)":
            sections.append(
                f"## Output Files (in output/ directory)\n{file_listing}\n"
            )

        n_expectations = len(test_case.expectations)
        sections.append(
            "Evaluate based on ALL available evidence: read events.jsonl to "
            "understand the agent's execution, and inspect files in output/ to "
            "verify results. For binary files (xlsx, docx, etc.), write and run "
            "Python scripts to parse and verify their contents.\n\n"
            "Write your verdict to a file named `verdict.json` in the current "
            "directory, with exactly this structure:\n"
            "```json\n"
            "{\n"
            '  "results": [\n'
            '    {"index": 0, "passed": true, "reasoning": "brief explanation"},\n'
            '    {"index": 1, "passed": false, "reasoning": "brief explanation"}\n'
            "  ]\n"
            "}\n"
            "```\n"
            f"You MUST include exactly {n_expectations} entries (one per "
            "expectation), in order, using 0-based index. After writing, read "
            "the file back and verify:\n"
            "  1. It is valid JSON (e.g. "
            "`python -c \"import json; json.load(open('verdict.json'))\"`)\n"
            f"  2. `results` has exactly {n_expectations} entries\n"
            "  3. Every entry has `index`, `passed`, and `reasoning` fields\n"
            "If any check fails, rewrite the file. Then reply with a one-line "
            "confirmation (e.g. \"verdict written\")."
        )

        prompt = "\n".join(sections)
        cfg = RunConfig(model=grading_model, permission=JUDGE_PERMISSION)
        result = await client.async_run(
            prompt, str(judge_ws), run_cfg=cfg, timeout_s=300,
        )
        judge_stats = _extract_stats(result)
        exp_results = _read_verdict_file(judge_ws, test_case.expectations)

        # Retry once if verdict file had issues
        should_retry = _has_verdict_issue(exp_results)
        if should_retry:
            _dump_judge_debug(case_dir, "", judge_ws, result, test_case.id)
            reasons = [er.reasoning[:100] for er in exp_results if er.earned == 0]
            log.warning("Judge verdict issue for case %s: %s — retrying",
                        test_case.id, reasons)
            # Clear stale verdict before retry
            (judge_ws / "verdict.json").unlink(missing_ok=True)
            result = await client.async_run(
                prompt, str(judge_ws), run_cfg=cfg, timeout_s=300,
            )
            retry_stats = _extract_stats(result)
            exp_results = _read_verdict_file(judge_ws, test_case.expectations)
            if _has_verdict_issue(exp_results):
                _dump_judge_debug(case_dir, ".retry", judge_ws, result, test_case.id)
            # Merge stats: sum cost/turns, keep retry tokens
            judge_stats["cost"] = judge_stats.get("cost", 0.0) + retry_stats.get("cost", 0.0)
            judge_stats["turns"] = judge_stats.get("turns", 0) + retry_stats.get("turns", 0)
            judge_stats["tokens"] = retry_stats.get("tokens", judge_stats.get("tokens", {}))

        return exp_results, judge_stats
    except Exception as e:
        # On error, all expectations score 0
        return [
            ExpectationResult(
                desc=exp.desc, score=exp.score, earned=0,
                reasoning=f"Judge error: {e}",
            )
            for exp in test_case.expectations
        ], empty_stats
    finally:
        shutil.rmtree(judge_ws, ignore_errors=True)


def _has_verdict_issue(exp_results: list[ExpectationResult]) -> bool:
    """True if any expectation reasoning indicates a parse/shape problem."""
    return any(
        er.reasoning == "Missing from judge response"
        or er.reasoning.startswith("Could not read verdict.json")
        for er in exp_results
    )


def _dump_judge_debug(
    dump_dir: Path,
    suffix: str,
    judge_ws: Path,
    run_result: "RunResult",
    case_id: str,
) -> None:
    """Copy the malformed verdict.json and judge final_text into dump_dir.

    Writes `verdict.bad{suffix}.json` (if the file exists) and
    `judge_response.bad{suffix}.txt` so the raw judge output can be inspected
    after the judge workspace tempdir is cleaned up.
    """
    try:
        dump_dir.mkdir(parents=True, exist_ok=True)
        verdict_src = judge_ws / "verdict.json"
        if verdict_src.exists():
            verdict_dest = dump_dir / f"verdict.bad{suffix}.json"
            shutil.copy2(verdict_src, verdict_dest)
            log.warning("Saved malformed verdict for case %s to %s",
                        case_id, verdict_dest)
        final_text = _extract_text_from_result(run_result)
        text_dest = dump_dir / f"judge_response.bad{suffix}.txt"
        text_dest.write_text(final_text if final_text else "(empty)")
        log.warning("Saved judge response text for case %s to %s",
                    case_id, text_dest)
    except Exception as e:
        log.warning("Failed to dump judge debug artifacts for case %s: %s",
                    case_id, e)


def _read_verdict_file(
    judge_ws: Path, expectations: list[Expectation],
) -> list[ExpectationResult]:
    """Read judge verdict from `verdict.json` in the judge workspace.

    On missing file, invalid JSON, or missing `results` key, returns a list
    where every expectation earns 0 with a reasoning starting with
    "Could not read verdict.json" (the retry trigger).
    """
    verdict_path = judge_ws / "verdict.json"
    try:
        with open(verdict_path) as f:
            obj = json.load(f)
        if not isinstance(obj, dict) or "results" not in obj:
            raise ValueError("missing 'results' key")
        if not isinstance(obj["results"], list):
            raise ValueError("'results' is not a list")
        return _map_results(obj["results"], expectations)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        return [
            ExpectationResult(
                desc=exp.desc, score=exp.score, earned=0,
                reasoning=f"Could not read verdict.json: {e}",
            )
            for exp in expectations
        ]


def _map_results(
    raw_results: list[dict], expectations: list[Expectation],
) -> list[ExpectationResult]:
    """Map parsed JSON results to ExpectationResult objects by index."""
    # Build lookup by index
    by_index: dict[int, dict] = {}
    for item in raw_results:
        idx = item.get("index")
        if isinstance(idx, int):
            by_index[idx] = item

    results = []
    for i, exp in enumerate(expectations):
        item = by_index.get(i)
        if item is not None:
            passed = bool(item.get("passed", False))
            reasoning = item.get("reasoning", "")
            results.append(ExpectationResult(
                desc=exp.desc,
                score=exp.score,
                earned=exp.score if passed else 0,
                reasoning=reasoning,
            ))
        else:
            results.append(ExpectationResult(
                desc=exp.desc, score=exp.score, earned=0,
                reasoning="Missing from judge response",
            ))
    return results


def _extract_text_from_result(run_result: "RunResult") -> str:
    """Extract human-readable text from a RunResult."""
    from opencode_wrapper import run_result_fuzzy_text

    text = run_result.final_text.strip() if run_result.final_text else ""
    if text and not text.startswith('{"type":'):
        return text

    fuzzy = run_result_fuzzy_text(run_result).strip()
    if fuzzy and not fuzzy.startswith('{"type":'):
        return fuzzy

    pieces = []
    for ev in run_result.events:
        if ev.get("type") == "tool_use":
            part = ev.get("part", {})
            state = part.get("state", {})
            output = state.get("output", "")
            if isinstance(output, str) and output.strip():
                if not output.startswith("<") and len(output) < 2000:
                    pieces.append(output.strip())

    return "\n".join(pieces) if pieces else ""


def _extract_stats(run_result: "RunResult") -> dict:
    """Extract token usage, cost, and turns from a RunResult."""
    stats: dict = {"tokens": {}, "cost": 0.0, "turns": 0}
    if hasattr(run_result, "token_usage"):
        tu = run_result.token_usage
        stats["tokens"] = {
            "total": tu.total,
            "input": tu.input,
            "output": tu.output,
            "reasoning": tu.reasoning,
            "cache_read": tu.cache_read,
            "cache_write": tu.cache_write,
        }
    stats["cost"] = run_result.total_cost
    stats["turns"] = run_result.turns
    return stats


# ---------------------------------------------------------------------------
# Combined grading
# ---------------------------------------------------------------------------


async def grade_case(
    test_case: TestCase,
    case_dir: Path,
    run_result: "RunResult",
    duration: float,
    client: "AsyncOpenCodeClient",
    grading_model: str | None = None,
) -> tuple[CaseResult, dict]:
    """Grade a test case by running LLM judge on all expectations.

    Args:
        case_dir: Path to the case output directory containing events.jsonl
                  and workspace/.
        run_result: Used only for extracting agent token/cost stats.

    Returns (case_result, grading_stats) where grading_stats contains
    token usage, cost, and turns from the judge session.
    """
    expectation_results: list[ExpectationResult] = []
    score = 0
    max_score = 0
    grading_stats: dict = {"tokens": {}, "cost": 0.0, "turns": 0}

    if test_case.expectations:
        expectation_results, grading_stats = await llm_judge(
            case_dir, test_case, client, grading_model=grading_model,
        )
        score = sum(er.earned for er in expectation_results)
        max_score = sum(er.score for er in expectation_results)

    token_dict = {}
    if hasattr(run_result, "token_usage"):
        tu = run_result.token_usage
        token_dict = {
            "total": tu.total,
            "input": tu.input,
            "output": tu.output,
            "reasoning": tu.reasoning,
            "cache_read": tu.cache_read,
            "cache_write": tu.cache_write,
        }

    return CaseResult(
        case_id=test_case.id,
        score=score,
        max_score=max_score,
        expectation_results=expectation_results,
        final_text=_extract_text_from_result(run_result),
        exit_code=run_result.exit_code,
        token_usage=token_dict,
        total_cost=run_result.total_cost,
        turns=run_result.turns,
        duration_s=duration,
    ), grading_stats


async def regrade_run(
    run_dir: Path,
    client: "AsyncOpenCodeClient",
    concurrency: int = 4,
    grading_model: str | None = None,
) -> "BenchmarkReport":
    """Re-grade all cases in an existing run with concurrency.

    Discovers cases/trials from the run directory, grades them in parallel
    using a semaphore, aggregates trial results, and returns a BenchmarkReport.
    """
    import asyncio
    from collections import defaultdict

    from opencode_wrapper import RunResult

    from .models import BenchmarkReport, Expectation, aggregate_trials
    from .runner import build_report
    from .store import _write_json

    meta_path = run_dir / "meta.json"
    with open(meta_path) as f:
        meta = json.load(f)

    num_trials = meta.get("trials", 1)
    sem = asyncio.Semaphore(concurrency)

    # Discover all (TestCase, trial_dir, result_data) tuples
    grading_tasks: list[tuple[TestCase, Path, dict]] = []
    case_order: list[str] = []

    for case_dir in sorted(run_dir.iterdir()):
        eval_path = case_dir / "eval.json"
        if not eval_path.exists():
            continue

        with open(eval_path) as f:
            eval_data = json.load(f)

        expectations = [
            Expectation(desc=e["desc"], score=e.get("score", 1))
            for e in eval_data.get("expectations", [])
        ]
        tc = TestCase(
            id=eval_data["id"],
            prompt=eval_data["prompt"],
            expectations=expectations,
        )
        case_order.append(tc.id)

        trial_dirs = sorted(case_dir.glob("trial-*"))
        if trial_dirs:
            for td in trial_dirs:
                ws_path = td / "workspace"
                result_path = td / "result.json"
                if not ws_path.exists():
                    continue
                with open(result_path) as f:
                    result_data = json.load(f)
                grading_tasks.append((tc, td, result_data))
        else:
            # Legacy: no trial subdirectories
            ws_path = case_dir / "workspace"
            result_path = case_dir / "result.json"
            if not ws_path.exists():
                continue
            with open(result_path) as f:
                result_data = json.load(f)
            grading_tasks.append((tc, case_dir, result_data))

    async def _regrade_one(
        tc: TestCase, trial_dir: Path, result_data: dict,
    ) -> tuple[str, CaseResult]:
        async with sem:
            mock_result = RunResult()
            mock_result.exit_code = result_data.get("exit_code")

            cr, _ = await grade_case(
                tc, trial_dir, mock_result,
                result_data.get("duration_s", 0.0), client,
                grading_model=grading_model,
            )
            # Preserve original agent metrics
            cr.turns = result_data.get("turns", 0)
            cr.token_usage = result_data.get("token_usage", {})
            cr.total_cost = result_data.get("total_cost", 0.0)
            cr.duration_s = result_data.get("duration_s", 0.0)

            _write_json(trial_dir / "result.json", cr.to_dict())
            return (tc.id, cr)

    # Run all grading tasks concurrently
    task_coros = [
        asyncio.create_task(_regrade_one(tc, td, rd))
        for tc, td, rd in grading_tasks
    ]
    completed = await asyncio.gather(*task_coros)

    # Group by case_id and aggregate
    by_case: dict[str, list[CaseResult]] = defaultdict(list)
    for case_id, cr in completed:
        by_case[case_id].append(cr)

    results: list[CaseResult] = []
    for case_id in case_order:
        trials = by_case.get(case_id, [])
        if not trials:
            continue
        if len(trials) == 1 and num_trials <= 1:
            results.append(trials[0])
        else:
            agg = aggregate_trials(case_id, trials)
            results.append(agg)
        # Save case-level result
        case_dir = run_dir / case_id
        if case_dir.is_dir():
            _write_json(case_dir / "result.json", results[-1].to_dict())

    run_id = run_dir.name
    report = build_report(meta.get("suite_name", "unknown"), run_id, results)
    report.num_trials = num_trials
    _write_json(run_dir / "report.json", report.to_dict())

    return report


def error_result(case_id: str, error: Exception, test_case: TestCase | None = None) -> CaseResult:
    """Create a failed CaseResult from an exception."""
    max_score = sum(e.score for e in test_case.expectations) if test_case else 0
    return CaseResult(
        case_id=case_id,
        score=0,
        max_score=max_score,
        error=f"{type(error).__name__}: {error}",
    )


# ---------------------------------------------------------------------------
# Review agent
# ---------------------------------------------------------------------------


def _setup_review_workspace(case_dir: Path, trial_results: list[CaseResult]) -> Path:
    """Create workspace for the review agent with all trial artifacts."""
    review_ws = Path(tempfile.mkdtemp(prefix="ascot_review_"))

    trial_dirs = sorted(case_dir.glob("trial-*"))
    for td in trial_dirs:
        trial_name = td.name  # e.g. "trial-1"
        dest = review_ws / trial_name

        # Copy events
        events_src = td / "events.jsonl"
        if events_src.exists():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(events_src, dest / "events.jsonl")

        # Copy workspace output
        ws_src = td / "workspace"
        if ws_src.is_dir():
            shutil.copytree(ws_src, dest / "output")

    return review_ws


def _build_review_prompt(
    test_case: TestCase,
    trial_results: list[CaseResult],
) -> str:
    """Build the prompt for the review agent."""
    sections: list[str] = []

    sections.append(
        "You are a diagnostic reviewer analyzing why a benchmark test case "
        "failed. Do NOT re-grade. Your job is to identify root causes and "
        "patterns across trials.\n"
    )

    # Original task
    sections.append(f"## Original Task Prompt\n{test_case.prompt}\n")

    # Expectations
    exp_lines = []
    for i, exp in enumerate(test_case.expectations):
        exp_lines.append(f"{i + 1}. {exp.desc} ({exp.score} pts)")
    sections.append(f"## Expectations\n" + "\n".join(exp_lines) + "\n")

    # Per-trial results summary
    sections.append("## Trial Results\n")
    has_mixed = False
    any_pass = False
    any_fail = False
    for i, tr in enumerate(trial_results, 1):
        tag = "PASS" if tr.score == tr.max_score else "FAIL"
        if tr.score == tr.max_score:
            any_pass = True
        else:
            any_fail = True
        sections.append(f"### Trial {i}: [{tag}] {tr.score}/{tr.max_score}")
        if tr.error:
            sections.append(f"Error: {tr.error}")
        for er in tr.expectation_results:
            status = "PASS" if er.earned > 0 else "FAIL"
            line = f"- [{status}] {er.desc}"
            if er.reasoning:
                line += f" — {er.reasoning[:300]}"
            sections.append(line)
        sections.append("")

    has_mixed = any_pass and any_fail

    # Instructions
    sections.append("## Available Evidence\n")
    for i in range(1, len(trial_results) + 1):
        sections.append(
            f"- `trial-{i}/events.jsonl`: Agent execution log for trial {i}\n"
            f"- `trial-{i}/output/`: Files produced by the agent in trial {i}"
        )
    sections.append("")

    if has_mixed:
        sections.append(
            "IMPORTANT: Some trials passed and some failed. Compare the "
            "successful trial(s) against the failed one(s) to identify what "
            "the agent did differently. Focus on what made the difference.\n"
        )

    sections.append(
        "Analyze the evidence and write a diagnostic report in markdown. Include:\n"
        "- Summary of the root cause\n"
        "- Per-trial observations (what happened in each trial)\n"
        "- Failure patterns (if any)\n"
        "- Suggestions for fixing the issue\n"
    )

    return "\n".join(sections)


async def review_case(
    test_case: TestCase,
    case_dir: Path,
    trial_results: list[CaseResult],
    client: "AsyncOpenCodeClient",
    model: str | None = None,
) -> str:
    """Run a review agent to diagnose why a case failed.

    Analyzes all trials for a case, comparing successful and failed trials
    to identify patterns and root causes.

    Returns the review text (markdown).
    """
    from opencode_wrapper import RunConfig

    review_ws = _setup_review_workspace(case_dir, trial_results)
    try:
        prompt = _build_review_prompt(test_case, trial_results)
        cfg = RunConfig(model=model, permission=JUDGE_PERMISSION)
        result = await client.async_run(
            prompt, str(review_ws), run_cfg=cfg, timeout_s=300,
        )
        return _extract_text_from_result(result) or "(empty review response)"
    except Exception as e:
        log.error("Review error for case %s: %s", test_case.id, e)
        return f"Review error: {e}"
    finally:
        shutil.rmtree(review_ws, ignore_errors=True)
