"""Grading: LLM judge evaluates expectations and assigns scores."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

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


def _setup_judge_workspace(output_ws: Path) -> Path:
    """Create an isolated workspace for the judge with output files copied in."""
    judge_ws = Path(tempfile.mkdtemp(prefix="ascot_judge_"))
    shutil.copytree(
        output_ws, judge_ws / "output",
        ignore=shutil.ignore_patterns(".opencode"),
    )
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
    ws: Path,
    test_case: TestCase,
    run_result: "RunResult",
    client: "AsyncOpenCodeClient",
) -> list[ExpectationResult]:
    """Run an OpenCode session to judge output against all expectations."""
    from opencode_wrapper import RunConfig

    judge_ws = _setup_judge_workspace(ws)
    try:
        file_listing = _list_workspace_files(judge_ws / "output")
        final_text = _extract_text_from_result(run_result)

        if final_text:
            (judge_ws / "agent_output.txt").write_text(final_text)

        # Build numbered expectations list
        exp_lines = []
        for i, exp in enumerate(test_case.expectations):
            exp_lines.append(f"{i + 1}. {exp.desc} ({exp.score} pts)")
        exp_list = "\n".join(exp_lines)

        sections = [
            "You are a grading judge. Evaluate whether the agent's output "
            "meets EACH of the following expectations.\n",
            f"## Expectations\n{exp_list}\n",
        ]

        if final_text:
            display_text = final_text if len(final_text) <= 4000 else final_text[:4000] + "\n... (truncated, full text in agent_output.txt)"
            sections.append(f"## Agent Text Output\n{display_text}\n")

        if file_listing.strip() and file_listing.strip() != "(no files)":
            sections.append(
                f"## Output Files (in output/ directory)\n{file_listing}\n"
            )

        sections.append(
            "Evaluate based on ALL available evidence: the agent's text output "
            "AND any files in output/. For binary files (xlsx, docx, etc.), "
            "write and run Python scripts to parse and verify their contents.\n\n"
            "Reply with ONLY a JSON object in this exact format:\n"
            "```json\n"
            "{\n"
            '  "results": [\n'
            '    {"index": 0, "passed": true, "reasoning": "brief explanation"},\n'
            '    {"index": 1, "passed": false, "reasoning": "brief explanation"}\n'
            "  ]\n"
            "}\n"
            "```\n"
            "You MUST include exactly one entry per expectation, in order, using 0-based index."
        )

        prompt = "\n".join(sections)
        cfg = RunConfig(permission=JUDGE_PERMISSION)
        result = await client.async_run(
            prompt, str(judge_ws), run_cfg=cfg, timeout_s=300,
        )
        text = _extract_text_from_result(result)
        return _parse_judge_response(text, test_case.expectations)
    except Exception as e:
        # On error, all expectations score 0
        return [
            ExpectationResult(
                desc=exp.desc, score=exp.score, earned=0,
                reasoning=f"Judge error: {e}",
            )
            for exp in test_case.expectations
        ]
    finally:
        shutil.rmtree(judge_ws, ignore_errors=True)


def _parse_judge_response(
    text: str, expectations: list[Expectation],
) -> list[ExpectationResult]:
    """Parse judge response into per-expectation results."""
    # Try to find JSON with "results" array
    for match in re.finditer(r'\{[^{}]*"results"\s*:\s*\[.*?\]\s*\}', text, re.DOTALL):
        try:
            obj = json.loads(match.group())
            if "results" in obj and isinstance(obj["results"], list):
                return _map_results(obj["results"], expectations)
        except json.JSONDecodeError:
            continue

    # Fallback: try parsing the entire text as JSON
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict) and "results" in obj:
            return _map_results(obj["results"], expectations)
    except (json.JSONDecodeError, ValueError):
        pass

    # Could not parse — all expectations fail
    return [
        ExpectationResult(
            desc=exp.desc, score=exp.score, earned=0,
            reasoning=f"Could not parse judge response: {text[:300]}",
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


# ---------------------------------------------------------------------------
# Combined grading
# ---------------------------------------------------------------------------


async def grade_case(
    test_case: TestCase,
    ws: Path,
    run_result: "RunResult",
    duration: float,
    client: "AsyncOpenCodeClient",
) -> CaseResult:
    """Grade a test case by running LLM judge on all expectations."""
    expectation_results: list[ExpectationResult] = []
    score = 0
    max_score = 0

    if test_case.expectations:
        expectation_results = await llm_judge(ws, test_case, run_result, client)
        score = sum(er.earned for er in expectation_results)
        max_score = sum(er.score for er in expectation_results)

    token_dict = {}
    if hasattr(run_result, "token_usage"):
        tu = run_result.token_usage
        token_dict = {
            "total": tu.total,
            "input": tu.input,
            "output": tu.output,
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
    )


def error_result(case_id: str, error: Exception, test_case: TestCase | None = None) -> CaseResult:
    """Create a failed CaseResult from an exception."""
    max_score = sum(e.score for e in test_case.expectations) if test_case else 0
    return CaseResult(
        case_id=case_id,
        score=0,
        max_score=max_score,
        error=f"{type(error).__name__}: {error}",
    )
