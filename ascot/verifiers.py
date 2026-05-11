"""Deterministic test_script grading: runs pytest in case workspace, parses junit XML."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import ExpectationResult

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 60.0
_REASONING_MAX = 500


def run_test_script(
    workspace_dir: Path,
    script_path: Path,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> list[ExpectationResult]:
    """Run pytest on script_path with cwd=workspace_dir; return one
    ExpectationResult per pytest test (1 point each).

    `script_path` may be either a plain path or a pytest nodeid of the
    form `<file>::<test_name>` to run a single test from a shared file.
    The full string is passed to pytest; existence is checked only against
    the file part.

    All edge cases collapse to a single failed ExpectationResult so the
    case still gets a non-zero max_score and the failure reason is visible
    in the report:

    - script not found
    - pytest binary not on PATH
    - pytest collection error / no tests collected (incl. unknown nodeid)
    - timeout
    - junit XML parse error
    """
    file_part_str, _, _ = str(script_path).partition("::")
    file_part = Path(file_part_str)
    if not file_part.exists():
        return [_single_failure(
            f"test_script: {Path(script_path).name}",
            f"file not found: {file_part}",
        )]

    junit_dir = Path(tempfile.mkdtemp(prefix="ascot_junit_"))
    junit_path = junit_dir / "junit.xml"
    try:
        try:
            proc = subprocess.run(
                [
                    sys.executable, "-m", "pytest", str(script_path),
                    f"--junit-xml={junit_path}",
                    "-q", "--tb=line",
                    "-p", "no:cacheprovider",
                ],
                cwd=str(workspace_dir),
                capture_output=True, text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return [_single_failure(
                f"test_script: {script_path.name}",
                f"test_script timed out after {timeout_s}s",
            )]
        except FileNotFoundError:
            return [_single_failure(
                f"test_script: {script_path.name}",
                "pytest not installed (install with: pip install pytest)",
            )]

        if not junit_path.exists():
            tail = (proc.stderr or proc.stdout or "")[-_REASONING_MAX:]
            return [_single_failure(
                f"test_script: {script_path.name}",
                f"pytest produced no junit output (exit {proc.returncode}): {tail}",
            )]

        results = _parse_junit(junit_path)
        if not results:
            tail = (proc.stderr or proc.stdout or "")[-_REASONING_MAX:]
            return [_single_failure(
                f"test_script: {script_path.name}",
                f"no tests collected (exit {proc.returncode}): {tail}",
            )]
        return results
    finally:
        shutil.rmtree(junit_dir, ignore_errors=True)


def _single_failure(desc: str, reasoning: str) -> ExpectationResult:
    return ExpectationResult(
        desc=desc, score=1, earned=0, reasoning=reasoning[:_REASONING_MAX],
    )


def _parse_junit(junit_path: Path) -> list[ExpectationResult]:
    """Parse pytest --junit-xml into ExpectationResults.

    Each <testcase> = one ExpectationResult worth 1 point.
    Skipped tests are excluded from scoring (not added to the list).
    """
    try:
        tree = ET.parse(junit_path)
    except ET.ParseError as e:
        return [_single_failure(
            "test_script: junit-xml parse error",
            f"junit XML parse error: {e}",
        )]

    results: list[ExpectationResult] = []
    for tc in tree.iter("testcase"):
        if tc.find("skipped") is not None:
            continue

        name = tc.get("name", "(unknown)")
        node = tc.find("failure")
        if node is None:
            node = tc.find("error")

        if node is not None:
            msg = node.get("message", "") or ""
            text = (node.text or "").strip()
            if msg and text:
                reasoning = f"{msg}: {text}"
            else:
                reasoning = msg or text or "(no failure message)"
            results.append(ExpectationResult(
                desc=name, score=1, earned=0,
                reasoning=reasoning[:_REASONING_MAX],
            ))
        else:
            results.append(ExpectationResult(
                desc=name, score=1, earned=1, reasoning="",
            ))
    return results
