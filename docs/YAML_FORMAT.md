# Ascot Test Case YAML Format

## Structure

```yaml
name: <suite-name>
description: "<description>"
default_timeout_s: 300
default_model: opencode/deepseek-v4-flash-free  # optional model override
grading_model: null          # optional, model for LLM judge (defaults to default_model)
default_workspace_files_from: null  # optional, inherited by all cases
default_test_script_timeout_s: 60   # optional, default 60s for every test_script
default_max_continues: 3            # optional, auto-continue nudges (default 3; 0 = no nudges)

test_cases:
  - id: <kebab-case-id>
    description: "<short description>"
    prompt: |
      <prompt sent to the agent>
    expectations:              # optional, LLM-judged
      - desc: "<what to check>"
        score: 10              # points for this expectation (default: 1)
    test_script: <path>        # optional, pytest file run after the agent finishes
    workspace_files_from: <dir path>  # optional, copy directory (binary-safe)
    timeout_s: 300           # optional, per-case
    agent: null              # optional, per-case
    tags: []                 # optional, for --tag filtering
    max_continues: 3         # optional, auto-continue nudges (default 3; 0 = no nudges)
    append_grading_prompt: | # optional, extra text appended to the LLM judge prompt
      <extra grading guidance>
```

## Fields

### Suite-level fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Suite name |
| `description` | no | Suite description |
| `default_timeout_s` | no | Default timeout in seconds for all cases (default: 600) |
| `default_model` | no | Default model for agent runs; also used as grading model if `grading_model` is not set. Recommended because global OpenCode `model` is isolated by default. |
| `grading_model` | no | Model for the LLM judge; takes priority over `default_model` |
| `default_workspace_files_from` | no | Default workspace files directory, inherited by all cases |
| `default_test_script_timeout_s` | no | Timeout in seconds for every `test_script` invocation (default: 60) |
| `default_max_continues` | no | Default auto-continue nudge budget for all cases (default: 3; 0 = no nudges). See [Auto-continue](#auto-continue). |

### Per-case fields

| Field | Required | Description |
|---|---|---|
| `id` | yes | Unique kebab-case identifier |
| `prompt` | yes | Instruction sent to the agent |
| `expectations` | no | List of `{desc, score}` items evaluated by LLM judge |
| `test_script` | no | Path to a pytest file. Path is relative to the testcases YAML directory. See [Test script grading](#test-script-grading). |
| `workspace_files_from` | no | Directory copied into workspace (supports binary); inherits from suite-level `default_workspace_files_from` if not set |
| `timeout_s` | no | Timeout in seconds (default: 120) |
| `agent` | no | Agent override |
| `tags` | no | Tags for `--tag` filtering |
| `max_continues` | no | Auto-continue nudge budget (default: 3; 0 = no nudges); inherits from suite-level `default_max_continues`. See [Auto-continue](#auto-continue). |
| `append_grading_prompt` | no | Extra text appended to the end of the LLM judge prompt for this case. Use it to give the judge case-specific grading guidance (e.g. how to read a particular file type). Only affects cases with `expectations`. |

## Expectations (LLM-judged)

Each expectation has:
- `desc` (required): Natural language description of what to check
- `score` (optional, default: 1): Points awarded if this expectation is met

The LLM judge evaluates all expectations and assigns scores. Results are shown as `earned/total` (e.g., `30/50`).

## Test script grading

`test_script` points to a pytest file. After the agent finishes, the framework runs it with the agent's preserved workspace as the working directory:

```
pytest <test_script> --junit-xml=<tmp> -q --tb=line -p no:cacheprovider
```

Rules:
- **One pytest test = 1 point by default.** A test that passes earns its full weight; a test that fails earns 0. Weight a test with the `@pytest.mark.score(N)` decorator (see [Weighting tests](#weighting-tests)).
- **Skipped tests are excluded** from scoring (they don't appear in `expectation_results`).
- The script's relative file paths resolve against the case workspace, so the script can `open("output.txt")` to inspect the agent's output directly.
- Default timeout is 60 s (override with suite-level `default_test_script_timeout_s`).
- The pytest file path is relative to the testcases YAML directory.
- **Pytest nodeid syntax** `path/to/file.py::test_name` is supported, letting multiple cases share one verifier file and each pick a single test:
  ```yaml
  - id: title-extraction
    test_script: ./verifiers/test_pages.py::test_page1_md_exists
  - id: toc-parsing
    test_script: ./verifiers/test_pages.py::test_page2_md_exists
  ```
  Only the test named after `::` runs; existence is checked against the file part. An unknown test name surfaces as a single failed `ExpectationResult` with pytest's "no tests collected" message.

A case with only `test_script` and no `expectations` skips the LLM judge entirely — no judge cost, no judge tempdir. A case with both runs `test_script` first, then the LLM judge for the fuzzy expectations; the two result lists are concatenated.

### Weighting tests

By default each pytest test is worth 1 point. To weight a test, decorate it with `@pytest.mark.score(N)`, mirroring the `score` field on `expectations`:

```python
import pytest

@pytest.mark.score(5)
def test_title_present():
    assert Path("page1.md").exists()

def test_file_created():   # no marker → worth 1 point
    assert Path("page1.md").exists()
```

The above file is worth 6 points max (5 + 1). The `score` marker is registered by Ascot's built-in pytest plugin, so no `conftest.py` or marker registration is needed in your verifier. A failing weighted test earns 0 of its `N` points. A non-numeric or missing argument falls back to weight 1.

### pytest dependency

`pytest` must be importable in the same Python environment that runs `ascot`. Install with `pip install -e ".[dev]"`, or `pip install pytest`. The framework invokes pytest via `python -m pytest`, so no `pytest` binary on `$PATH` is required. If your `test_script` needs extra libraries (`pandas`, `openpyxl`, etc.), install them in the same environment.

## Auto-continue & completion signal

Every case runs as a multi-turn server session. Ascot always injects a completion-protocol
instruction into the agent's system prompt telling it to create an empty file
`.ascot/complete` *only when the task is genuinely done*. This serves two purposes:

1. **Auto-continue.** Some agents stop *early* — they pause before the task is actually
   finished but well before `timeout_s`. When the agent stops without having created the
   sentinel, the framework re-prompts it in the same session to keep working, up to
   `max_continues` times.
2. **Completion signal.** Whether the agent created the sentinel is recorded per trial as
   `signaled_completion` and shown in the report's `Done` column. It is **informational
   only** — it does not contribute to the score (which still comes from `expectations` +
   `test_script`).

```yaml
- id: long-refactor
  prompt: |
    Refactor the module and make all tests pass.
  max_continues: 3      # up to 3 "keep going" nudges after the initial turn (default)
  timeout_s: 900        # total wall-clock budget across ALL turns
```

How it works:
- **Sentinel file.** As soon as `.ascot/complete` exists, the loop ends — no further nudges.
- **Nudge budget.** `max_continues` caps how many "you stopped before signaling completion,
  continue working" re-prompts are sent after the initial turn. With `max_continues: 3` the
  agent can be prompted at most 4 times total (1 initial + 3 continues). `max_continues: 0`
  still asks for the sentinel but sends no nudges — a single turn.
- **`timeout_s` is the total budget.** It bounds the *entire* session — server startup plus
  every turn combined — not each turn. When it runs out mid-turn, the partial work captured
  so far is graded.
- **The loop stops** at the first of: sentinel created, nudge budget spent, or `timeout_s`
  exhausted.
- **`.ascot/` is private.** The injected instruction file and the completion sentinel live
  under `.ascot/` and are excluded from the preserved `workspace/`, so the judge never sees
  them.

## Example

```yaml
name: pdf-reading
description: "PDF reading benchmark"
default_timeout_s: 300
default_workspace_files_from: ../pdf-reading/input

test_cases:
  - id: page-count
    description: "Identify total page count"
    prompt: |
      Use the pdf-reader skill to check input/report.pdf metadata,
      write the page count to page_count.txt (number only).
    test_script: ./verifiers/test_page_count.py
    expectations:
      - desc: "the agent's explanation in summary.md addresses Q3 trends coherently"
        score: 3

  - id: title-extraction
    description: "Extract report title from page 1"
    prompt: |
      Use the pdf-reader skill to extract page 1 of input/report.pdf,
      save to page1.md.
    expectations:
      - desc: page1.md exists
        score: 3
      - desc: 'page1.md contains the report title "季度报告"'
        score: 7
```

Where `verifiers/test_page_count.py`:

```python
import os

def test_page_count_file_exists():
    assert os.path.exists("page_count.txt"), "page_count.txt not created"

def test_page_count_value():
    with open("page_count.txt") as f:
        assert f.read().strip() == "42"
```

The `page-count` case scores 2 (pytest) + 3 (judge) = 5 max; `title-extraction` is pure LLM-judge at 10 max.
