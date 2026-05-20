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
- **One pytest test = 1 point.** A test that passes earns 1 point; a test that fails earns 0. Weighting per pytest test is not supported in v1.
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

### pytest dependency

`pytest` must be importable in the same Python environment that runs `ascot`. Install with `pip install -e ".[dev]"`, or `pip install pytest`. The framework invokes pytest via `python -m pytest`, so no `pytest` binary on `$PATH` is required. If your `test_script` needs extra libraries (`pandas`, `openpyxl`, etc.), install them in the same environment.

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
