"""Report formatting: terminal and JSON output."""

from __future__ import annotations

import json

from .models import BenchmarkReport


def format_terminal(report: BenchmarkReport, *, show_cost: bool = False) -> str:
    """Format a report for terminal display."""
    lines: list[str] = []
    w = 70

    lines.append("=" * w)
    lines.append(f" Ascot Benchmark: {report.suite_name}  |  {report.run_id}")
    lines.append("=" * w)

    # Header
    header = f" {'Case':<22} {'Score':<10} {'Turns':>5} {'Tokens':>8} {'Time':>7}"
    if show_cost:
        header += f" {'Cost':>8}"
    lines.append(header)
    lines.append("-" * w)

    # Per-case rows
    for r in report.results:
        score_str = f"{r.score}/{r.max_score}" if r.max_score > 0 else "-"
        tokens = r.token_usage.get("total", 0)
        row = f" {r.case_id:<22} {score_str:<10} {r.turns:>5} {tokens:>8,} {r.duration_s:>6.1f}s"
        if show_cost:
            row += f" ${r.total_cost:>7.4f}"
        lines.append(row)

    lines.append("-" * w)

    # Summary
    if report.max_score > 0:
        score_pct = report.total_score / report.max_score
        summary = (
            f" Total: {report.total} | "
            f"Score: {report.total_score}/{report.max_score} ({score_pct:.1%})"
        )
    else:
        summary = f" Total: {report.total}"
    lines.append(summary)

    metrics = (
        f" Turns: {report.total_turns} | "
        f"Tokens: {report.total_tokens:,} | "
        f"Time: {report.total_duration_s:.1f}s"
    )
    if show_cost:
        metrics += f" | Cost: ${report.total_cost:.4f}"
    lines.append(metrics)

    # Show cases that lost points
    imperfect = [r for r in report.results if r.score < r.max_score or r.error]
    if imperfect:
        lines.append("")
        lines.append(" Details:")
        for r in imperfect:
            lines.append(f"   {r.case_id} ({r.score}/{r.max_score}):")
            if r.error:
                lines.append(f"     Error: {r.error}")
            for er in r.expectation_results:
                tag = "PASS" if er.earned > 0 else "FAIL"
                line = f"     [{tag}] {er.score:>3}pts  {er.desc}"
                if er.earned == 0 and er.reasoning:
                    line += f" - {er.reasoning[:200]}"
                lines.append(line)

    # Phase breakdown
    cases_with_phases = [r for r in report.results if r.phases]
    if cases_with_phases:
        lines.append("")
        lines.append(" Phase Breakdown:")
        lines.append(f"   {'Case':<22} {'Setup':>6} {'Agent':>7} {'Save':>6} {'Grade':>7} {'G.Cost':>8}")
        lines.append("   " + "-" * 58)
        for r in cases_with_phases:
            p = r.phases
            ws_s = p.get("workspace_setup", {}).get("duration_s", 0)
            ag_s = p.get("agent_run", {}).get("duration_s", 0)
            pres_s = p.get("workspace_preserve", {}).get("duration_s", 0)
            gr_s = p.get("grading", {}).get("duration_s", 0)
            gr_cost = p.get("grading", {}).get("cost", 0)
            lines.append(
                f"   {r.case_id:<22} {ws_s:>5.1f}s {ag_s:>6.1f}s {pres_s:>5.1f}s {gr_s:>6.1f}s ${gr_cost:>7.4f}"
            )

    lines.append("=" * w)
    return "\n".join(lines)


def format_json(report: BenchmarkReport) -> str:
    """Format report as JSON."""
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
