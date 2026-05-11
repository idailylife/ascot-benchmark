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

1. **`suite.py`** — Resolves suite directory layout (`resolve_suite`) and loads YAML test case files (`load_test_suite`). Suite-level defaults (`default_timeout_s`, `default_model`, `default_workspace_files_from`, `grading_model`, `default_test_script_timeout_s`) are inherited by test cases unless overridden.

2. **`runner.py`** — `BenchmarkRunner.run_all()` orchestrates execution. For each `(case, trial)` pair, it creates a workspace, runs OpenCode via `opencode_wrapper.AsyncOpenCodeClient`, preserves output, then calls the grader. All pairs share a semaphore-based concurrency pool. Permissions are merged from defaults + suite's `opencode.json`.

3. **`workspace.py`** — Creates temp directories, copies suite config as `.opencode/`, copies `workspace_files_from` fixtures. Relative paths resolve against the testcases YAML directory.

4. **`graders.py`** — Grading orchestration. For each case, runs the deterministic `test_script` (if set) via `verifiers.run_test_script`, then runs the LLM judge over any `expectations`, and concatenates both result lists. The judge is a second OpenCode session in an isolated workspace with `events.jsonl` + `output/` files and a structured prompt; it parses the JSON verdict (`_parse_judge_response`) and scores 0 on parse failure. Accepts an optional `grading_model` to override the judge's model (priority: `grading_model` > `default_model` > opencode default). A case with only `test_script` (no `expectations`) skips the judge entirely — no judge cost. Also contains `review_case()` for the `ascot review` subcommand — a diagnostic agent that analyzes failed cases across trials, comparing successful vs failed trials to identify root causes.

5. **`verifiers.py`** — Deterministic pytest-based grading. `run_test_script(workspace_dir, script_path, timeout_s)` runs `python -m pytest <script>` with `cwd=workspace_dir`, parses the junit XML, and returns one `ExpectationResult` per pytest test (1 point each; skipped tests excluded). `script_path` may be a plain file or a pytest nodeid `file.py::test_name`. All edge cases (missing file, collection error, timeout, parse error) collapse to a single failed `ExpectationResult` so the case still gets a non-zero `max_score` and the failure reason is visible in the report.

6. **`models.py`** — Dataclasses (`TestCase`, `TestSuite`, `CaseResult`, `BenchmarkReport`). `aggregate_trials()` averages scores across multi-trial runs; handles timed-out trials (empty `expectation_results`) gracefully.

7. **`store.py`** — `RunStore` manages the `run-NNN/` output directory tree. Writes `meta.json`, `eval.json`, `result.json`, `events.jsonl`, and per-trial subdirectories.

8. **`report.py`** — Terminal and JSON formatting of `BenchmarkReport`.

9. **`inspect.py`** — Parses `events.jsonl` into per-step traces for performance debugging.

10. **`cli.py`** — Argparse CLI with subcommands: `run`, `grade`, `review`, `report`, `inspect`.

## Key design notes

- Version is defined solely in `pyproject.toml`; `__init__.py` reads it via `importlib.metadata`.
- `runner.py` and `graders.py` depend on `opencode_wrapper` (external process calls) — unit tests for these modules require mocking.
- All other modules (`models`, `suite`, `workspace`, `store`, `report`, `verifiers`) are pure logic / filesystem and are fully unit-testable without mocks.
- The YAML format supports suite-level defaults that cascade to test cases. See `docs/YAML_FORMAT.md` for the full schema.
