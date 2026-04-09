"""Inspect: parse and summarize events.jsonl for performance analysis."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StepTrace:
    """Timing and token data for a single agent step."""

    step: int
    reasoning_ms: int = 0
    tool_name: str | None = None
    tool_detail: str | None = None
    tool_call_id: str | None = None
    tool_time_ms: int = 0
    tool_status: str | None = None
    tokens: dict[str, Any] = field(default_factory=dict)
    cost: float = 0.0
    finish_reason: str = ""


@dataclass
class CaseTrace:
    """Aggregated trace for a single case execution."""

    case_id: str
    steps: list[StepTrace] = field(default_factory=list)
    total_duration_ms: int = 0
    total_tokens: dict[str, Any] = field(default_factory=dict)
    total_cost: float = 0.0


def _tool_detail(tool_name: str | None, state: dict[str, Any]) -> str | None:
    """Extract a brief detail string from the tool's input/output."""
    if not tool_name:
        return None
    inp = state.get("input", {})
    if not isinstance(inp, dict):
        return None

    if tool_name in ("read", "write"):
        fp = inp.get("filePath") or inp.get("file_path") or ""
        if fp:
            return Path(fp).name
    elif tool_name == "edit":
        fp = inp.get("filePath") or inp.get("file_path") or ""
        if fp:
            return Path(fp).name
    elif tool_name in ("glob", "grep"):
        return inp.get("pattern")
    elif tool_name == "bash":
        cmd = inp.get("command") or ""
        # Show first 60 chars of command
        return cmd[:60] + ("..." if len(cmd) > 60 else "") if cmd else None
    elif tool_name == "skill":
        return inp.get("skill") or inp.get("name")

    return None


def parse_events(case_dir: Path) -> CaseTrace:
    """Parse events.jsonl from a case directory into a CaseTrace."""
    events_path = case_dir / "events.jsonl"
    if not events_path.exists():
        raise FileNotFoundError(f"No events.jsonl in {case_dir}")

    events: list[dict[str, Any]] = []
    with open(events_path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    case_id = case_dir.name
    steps: list[StepTrace] = []
    step_num = 0
    step_start_ts: int | None = None
    first_ts: int | None = None
    last_ts: int | None = None

    # Pending tool data for current step
    cur_tool_name: str | None = None
    cur_tool_detail: str | None = None
    cur_tool_call_id: str | None = None
    cur_tool_time_ms: int = 0
    cur_tool_status: str | None = None
    cur_tool_start_ts: int | None = None

    for ev in events:
        ev_type = ev.get("type")
        ts = ev.get("timestamp", 0)

        if first_ts is None and ts:
            first_ts = ts
        if ts:
            last_ts = ts

        if ev_type == "step_start":
            step_num += 1
            step_start_ts = ts
            cur_tool_name = None
            cur_tool_detail = None
            cur_tool_call_id = None
            cur_tool_time_ms = 0
            cur_tool_status = None
            cur_tool_start_ts = None

        elif ev_type == "tool_use":
            part = ev.get("part", {})
            state = part.get("state", {})
            time_info = state.get("time", {})

            cur_tool_name = part.get("tool")
            cur_tool_call_id = part.get("callID")
            cur_tool_status = state.get("status")
            cur_tool_detail = _tool_detail(cur_tool_name, state)
            cur_tool_start_ts = time_info.get("start")
            tool_end = time_info.get("end")
            if cur_tool_start_ts and tool_end:
                cur_tool_time_ms = tool_end - cur_tool_start_ts

        elif ev_type == "step_finish":
            part = ev.get("part", {})
            tokens = part.get("tokens", {})
            cost = part.get("cost", 0) or 0
            reason = part.get("reason", "")

            # Calculate reasoning time
            reasoning_ms = 0
            if step_start_ts:
                if cur_tool_start_ts:
                    reasoning_ms = cur_tool_start_ts - step_start_ts
                else:
                    reasoning_ms = ts - step_start_ts

            steps.append(StepTrace(
                step=step_num,
                reasoning_ms=max(0, reasoning_ms),
                tool_name=cur_tool_name,
                tool_detail=cur_tool_detail,
                tool_call_id=cur_tool_call_id,
                tool_time_ms=cur_tool_time_ms,
                tool_status=cur_tool_status if cur_tool_name else reason,
                tokens=tokens,
                cost=cost,
                finish_reason=reason,
            ))

    # Aggregate totals
    total_duration_ms = (last_ts - first_ts) if first_ts and last_ts else 0
    total_tokens: dict[str, Any] = {}
    total_cost = 0.0
    for s in steps:
        total_cost += s.cost
        for k, v in s.tokens.items():
            if k == "cache" and isinstance(v, dict):
                cache = total_tokens.setdefault("cache", {})
                for ck, cv in v.items():
                    cache[ck] = cache.get(ck, 0) + (cv or 0)
            elif isinstance(v, (int, float)):
                total_tokens[k] = total_tokens.get(k, 0) + v

    return CaseTrace(
        case_id=case_id,
        steps=steps,
        total_duration_ms=total_duration_ms,
        total_tokens=total_tokens,
        total_cost=total_cost,
    )


def _fmt_ms(ms: int) -> str:
    """Format milliseconds as a human-readable duration."""
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def format_trace_terminal(trace: CaseTrace, *, show_cost: bool = False) -> str:
    """Format a CaseTrace for terminal display."""
    lines: list[str] = []
    w = 100

    lines.append("=" * w)
    lines.append(f" Ascot Inspect: {trace.case_id}")
    lines.append("=" * w)

    total_s = trace.total_duration_ms / 1000
    lines.append(f" Steps ({len(trace.steps)} total, {total_s:.1f}s):")
    lines.append("")

    header = f"   {'Step':>4}  {'Reasoning':>9}  {'Tool':<12} {'Detail':<24} {'Tool Time':>9}  {'Tokens':>8}"
    if show_cost:
        header += f"  {'Cost':>6}"
    header += f"  {'Status'}"
    lines.append(header)
    lines.append("   " + "-" * (w - 3))

    total_reasoning_ms = 0
    total_tool_ms = 0

    for s in trace.steps:
        total_reasoning_ms += s.reasoning_ms
        total_tool_ms += s.tool_time_ms

        tool_name = s.tool_name or "(none)"
        detail = s.tool_detail or ""
        if len(detail) > 24:
            detail = detail[:21] + "..."
        tool_time = _fmt_ms(s.tool_time_ms) if s.tool_name else "-"
        tokens = s.tokens.get("total", 0)
        status = s.tool_status or ""

        row = f"   {s.step:>4}  {_fmt_ms(s.reasoning_ms):>9}  {tool_name:<12} {detail:<24} {tool_time:>9}  {tokens:>8,}"
        if show_cost:
            row += f"  {s.cost:>6.2f}"
        row += f"  {status}"
        lines.append(row)

    lines.append("   " + "-" * (w - 3))

    # Summary
    lines.append("")
    lines.append(" Summary:")

    reasoning_pct = (total_reasoning_ms / trace.total_duration_ms * 100) if trace.total_duration_ms else 0
    tool_pct = (total_tool_ms / trace.total_duration_ms * 100) if trace.total_duration_ms else 0

    lines.append(
        f"   Reasoning: {_fmt_ms(total_reasoning_ms)} ({reasoning_pct:.0f}%)"
        f"   Tool exec: {_fmt_ms(total_tool_ms)} ({tool_pct:.0f}%)"
    )

    t = trace.total_tokens
    cache = t.get("cache", {})
    lines.append(
        f"   Input: {t.get('input', 0):,}"
        f"   Output: {t.get('output', 0):,}"
        f"   Reasoning: {t.get('reasoning', 0):,}"
        f"   Cache read: {cache.get('read', 0):,}"
    )

    if show_cost and trace.total_cost > 0:
        lines.append(f"   Total cost: ${trace.total_cost:.4f}")

    lines.append("=" * w)
    return "\n".join(lines)


def format_trace_json(trace: CaseTrace) -> str:
    """Format a CaseTrace as JSON."""
    return json.dumps(asdict(trace), indent=2, ensure_ascii=False)
