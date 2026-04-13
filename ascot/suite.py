"""Suite directory resolution and YAML test case loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import Expectation, TestCase, TestSuite


def resolve_suite(suite_dir: str | Path) -> Path:
    """Resolve a suite directory to the path that should become .opencode/.

    Accepts either:
    - A directory containing a .opencode/ subdirectory (returns .opencode/)
    - A directory that IS the .opencode content (has opencode.json or skills/ etc.)
    """
    p = Path(suite_dir).resolve()
    if not p.is_dir():
        raise ValueError(f"Suite directory does not exist: {p}")
    if (p / ".opencode").is_dir():
        return p / ".opencode"
    if (p / "opencode.json").exists() or (p / "opencode.jsonc").exists():
        return p
    if (p / "skills").is_dir() or (p / "commands").is_dir():
        return p
    raise ValueError(
        f"Invalid suite directory: {p}\n"
        "Expected .opencode/ subdirectory, opencode.json, or skills/ directory."
    )


def _parse_test_case(raw: dict[str, Any], defaults: dict[str, Any]) -> TestCase:
    raw_expectations = raw.get("expectations", [])
    expectations = [
        Expectation(desc=e["desc"], score=e.get("score", 1))
        for e in raw_expectations
    ]
    return TestCase(
        id=raw["id"],
        prompt=raw["prompt"],
        expectations=expectations,
        description=raw.get("description", ""),
        workspace_files_from=raw.get("workspace_files_from", defaults.get("default_workspace_files_from")),
        timeout_s=raw.get("timeout_s", defaults.get("default_timeout_s", 600.0)),
        model=raw.get("model", defaults.get("default_model")),
        agent=raw.get("agent"),
        tags=raw.get("tags", []),
    )


def _load_yaml_file(path: Path) -> dict[str, Any]:
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML dict at top level, got {type(data).__name__}: {path}")
    return data


def load_test_suite(path: str | Path) -> TestSuite:
    """Load a TestSuite from a YAML file or a directory of YAML files.

    Single file: parsed as one suite definition.
    Directory: all .yaml/.yml files merged into one suite.
    """
    p = Path(path).resolve()

    if p.is_file():
        data = _load_yaml_file(p)
        return _build_suite(data)

    if p.is_dir():
        yamls = sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml"))
        if not yamls:
            raise ValueError(f"No YAML files found in: {p}")
        if len(yamls) == 1:
            return _build_suite(_load_yaml_file(yamls[0]))
        # Merge multiple YAML files into one suite
        all_cases: list[dict[str, Any]] = []
        suite_name = p.name
        for yf in yamls:
            data = _load_yaml_file(yf)
            suite_name = data.get("name", suite_name)
            all_cases.extend(data.get("test_cases", []))
        return _build_suite({"name": suite_name, "test_cases": all_cases})

    raise ValueError(f"Test cases path does not exist: {p}")


def _build_suite(data: dict[str, Any]) -> TestSuite:
    defaults = {
        "default_timeout_s": data.get("default_timeout_s", 600.0),
        "default_model": data.get("default_model"),
        "default_workspace_files_from": data.get("default_workspace_files_from"),
    }
    cases = [_parse_test_case(dict(tc), defaults) for tc in data.get("test_cases", [])]
    return TestSuite(
        name=data.get("name", "unnamed"),
        test_cases=cases,
        description=data.get("description", ""),
        default_timeout_s=defaults["default_timeout_s"],
        default_model=defaults["default_model"],
        grading_model=data.get("grading_model"),
        default_workspace_files_from=defaults["default_workspace_files_from"],
    )
