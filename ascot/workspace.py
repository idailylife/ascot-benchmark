"""Workspace creation, suite copying, and output preservation."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from .models import TestCase

log = logging.getLogger(__name__)


def setup_workspace(
    suite_dir: Path,
    test_case: TestCase,
    testcases_dir: Path | None = None,
) -> Path:
    """Create a temporary workspace with suite config and pre-populated files.

    1. Creates a temp directory.
    2. Copies suite_dir as .opencode/ inside it.
    3. Copies workspace_files_from directory (binary-safe) if specified.

    Returns the workspace Path.
    """
    ws = Path(tempfile.mkdtemp(prefix="ascot_"))
    shutil.copytree(suite_dir, ws / ".opencode")

    if test_case.workspace_files_from:
        src = Path(test_case.workspace_files_from)
        if not src.is_absolute() and testcases_dir:
            src = testcases_dir / src
        src = src.resolve()
        if src.is_dir():
            dest = ws / src.name
            shutil.copytree(src, dest, dirs_exist_ok=True)

    return ws


def preserve_workspace(ws: Path, dest: Path) -> None:
    """Copy workspace output files to dest, excluding .opencode/ and .venv/."""
    shutil.copytree(ws, dest, ignore=shutil.ignore_patterns(".opencode", ".venv"))


def cleanup_workspace(ws: Path) -> None:
    """Remove temporary workspace directory."""
    shutil.rmtree(ws, ignore_errors=True)
