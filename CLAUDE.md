# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Ascot

Ascot is a benchmark framework for evaluating [OpenCode](https://opencode.ai) suites. A **suite** is a set of skills, MCP servers, instructions, and OpenCode configurations. Ascot runs test cases against a suite, then uses an LLM judge (a separate OpenCode session) to grade the results against expectations.

## Commands

```bash
pip install -e .                          # install in dev mode
pip install -e ".[dev]"                   # install with pytest
python -m pytest tests/ -v               # run all tests
python -m pytest tests/test_models.py -v  # run one test file
python -m pytest tests/ -k "test_timed"   # run tests matching pattern
```

Requires `opencode` CLI on PATH for `ascot run` / `ascot grade` (not needed for unit tests).

## Architecture

The pipeline for `ascot run` flows through these modules in order:

1. **`suite.py`** — Resolves suite directory layout (`resolve_suite`) and loads YAML test case files (`load_test_suite`). Suite-level defaults (`default_timeout_s`, `default_model`, `default_workspace_files_from`, `grading_model`) are inherited by test cases unless overridden.

2. **`runner.py`** — `BenchmarkRunner.run_all()` orchestrates execution. For each `(case, trial)` pair, it creates a workspace, runs OpenCode via `opencode_wrapper.AsyncOpenCodeClient`, preserves output, then calls the grader. All pairs share a semaphore-based concurrency pool. Permissions are merged from defaults + suite's `opencode.json`.

3. **`workspace.py`** — Creates temp directories, copies suite config as `.opencode/`, copies `workspace_files_from` fixtures. Relative paths resolve against the testcases YAML directory.

4. **`graders.py`** — LLM judge grading. Sets up an isolated judge workspace with `events.jsonl` + `output/` files, runs a second OpenCode session with a structured prompt, parses the JSON verdict (`_parse_judge_response`). On parse failure, all expectations score 0. Accepts an optional `grading_model` to override the judge's model (priority: `grading_model` > `default_model` > opencode default).

5. **`models.py`** — Dataclasses (`TestCase`, `TestSuite`, `CaseResult`, `BenchmarkReport`). `aggregate_trials()` averages scores across multi-trial runs; handles timed-out trials (empty `expectation_results`) gracefully.

6. **`store.py`** — `RunStore` manages the `run-NNN/` output directory tree. Writes `meta.json`, `eval.json`, `result.json`, `events.jsonl`, and per-trial subdirectories.

7. **`report.py`** — Terminal and JSON formatting of `BenchmarkReport`.

8. **`inspect.py`** — Parses `events.jsonl` into per-step traces for performance debugging.

9. **`cli.py`** — Argparse CLI with subcommands: `run`, `grade`, `report`, `inspect`.

## Key design notes

- Version is defined solely in `pyproject.toml`; `__init__.py` reads it via `importlib.metadata`.
- `runner.py` and `graders.py` depend on `opencode_wrapper` (external process calls) — unit tests for these modules require mocking.
- All other modules (`models`, `suite`, `workspace`, `store`, `report`) are pure logic / filesystem and are fully unit-testable without mocks.
- The YAML format supports suite-level defaults that cascade to test cases. See `docs/YAML_FORMAT.md` for the full schema.
