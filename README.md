# Ascot

Benchmark framework for evaluating [OpenCode](https://opencode.ai) suites.

A **suite** is a set of skills, MCP servers, instructions, and other OpenCode configurations that work together to solve a class of problems (e.g. document read/write, data processing). Ascot measures how well a suite performs by running test cases and grading the results.

## Install

```bash
pip install -e .
```

Requires `opencode` CLI on PATH. See [OpenCode docs](https://opencode.ai/docs/).

## Quick Start

```bash
python -m ascot run ./my-suite ./tests.yaml
```

This will:
1. Load the suite configuration from `./my-suite`
2. Parse test cases from `./tests.yaml`
3. For each test case, create an isolated workspace, copy the suite config in, run OpenCode
4. Grade results: LLM judge evaluates each expectation and assigns scores
5. Print a report with scores, turns, tokens, and duration

## Concepts

### Suite

A directory containing OpenCode configuration. Ascot accepts two layouts:

```
my-suite/                    # Layout A: directory IS the config
  opencode.json
  skills/
    my-skill/SKILL.md
  commands/
    my-cmd.md
```

```
my-project/                  # Layout B: has .opencode/ subdirectory
  .opencode/
    opencode.json
    skills/
      my-skill/SKILL.md
```

The suite's `opencode.json` does not need permission settings. Ascot injects sensible defaults (`"*": "allow"`, with `question`/`external_directory`/`doom_loop` denied). To override, add a `permission` block in the suite's `opencode.json` -- it will be merged on top of the defaults.

### Environment Setup

If your suite's skills or scripts depend on external packages (Python libraries, Node modules, system tools, etc.), set up the environment **before** running benchmarks.

**Option A: Activate environment in shell**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r my-suite/requirements.txt
ascot run ./my-suite ./tests.yaml
```

**Option B: Pass venv path via `--venv`**

```bash
python -m venv .venv
.venv/bin/pip install -r my-suite/requirements.txt
ascot run ./my-suite ./tests.yaml --venv .venv
```

Both approaches are equivalent. `--venv` injects the venv's `bin/` into `PATH` for each test case.

### Test Cases

YAML files defining what to test. A single file can contain multiple cases:

```yaml
name: doc-generation
description: "Document generation suite benchmark"
default_timeout_s: 180
default_model: anthropic/claude-sonnet-4-20250514

test_cases:
  - id: create-report
    prompt: |
      Create report.docx with title "Q1 Report" and a summary paragraph.
    expectations:
      - desc: report.docx exists
        score: 5
      - desc: 'report.docx title is "Q1 Report"'
        score: 10
      - desc: Contains a coherent summary paragraph
        score: 10

  - id: summarize-pdf
    prompt: |
      Read input.pdf and provide a concise summary.
    workspace_files_from: fixtures/summarize-pdf
    expectations:
      - desc: The summary accurately captures the key points of input.pdf
        score: 15
      - desc: The summary is concise (under 500 words)
        score: 5

  - id: csv-to-xlsx
    prompt: "Convert data.csv to formatted output.xlsx"
    workspace_files_from: fixtures/csv-to-xlsx
    expectations:
      - desc: output.xlsx exists
        score: 5
      - desc: output.xlsx contains all rows from data.csv with headers name, age, city
        score: 15
    tags: [excel]
```

### Test Case Fields

| Field | Required | Description |
|---|---|---|
| `id` | yes | Unique identifier |
| `prompt` | yes | Prompt sent to OpenCode |
| `expectations` | no | List of `{desc, score}` items evaluated by LLM judge |
| `workspace_files_from` | no | Directory of files to copy into workspace (supports binary) |
| `timeout_s` | no | Per-case timeout in seconds (default: 120) |
| `model` | no | Model override for this case |
| `agent` | no | Agent override for this case |
| `tags` | no | Tags for filtering (`--tag`) |

### Expectations

Each expectation has a `desc` (what to check) and an optional `score` (points, default: 1).

```yaml
expectations:
  - desc: output.xlsx exists
    score: 5
  - desc: 'output.xlsx contains all rows with correct headers'
    score: 15
  - desc: data is formatted correctly    # score defaults to 1
```

The LLM judge evaluates all expectations and assigns scores. Results are displayed as `earned/total` (e.g., `20/21`).

### Grading

Ascot uses an LLM judge for grading. A separate OpenCode session evaluates the output against all `expectations`. The judge runs in its own isolated workspace with full permissions, so it can write and run scripts to inspect binary files (xlsx, docx, etc.). It also sees the agent's text output, useful for tasks like "read a PDF and summarize it".

Grading is strictly outcome-based: if the agent did extensive work but didn't produce the expected result, the expectation scores 0.

## CLI Reference

### `ascot run`

```bash
python -m ascot run <suite_dir> <testcases> [options]
```

| Flag | Default | Description |
|---|---|---|
| `-o, --output` | `./benchmark` | Output directory for results |
| `-m, --model` | suite default | Override model for all cases |
| `-c, --concurrency` | 2 | Max parallel cases |
| `-t, --timeout` | per-case | Override timeout for all cases (seconds) |
| `--binary` | `opencode` | Path to OpenCode binary |
| `--venv` | none | Path to pre-configured virtual environment |
| `--tag` | all | Only run cases matching tag (repeatable) |
| `--show-cost` | off | Show cost column in report |
| `-f, --format` | `terminal` | Output format: `terminal` or `json` |
| `-v, --verbose` | off | Debug logging (global flag, before subcommand) |

### `ascot grade`

Re-run LLM judge grading on a previous run (e.g. after updating expectations):

```bash
python -m ascot grade ./benchmark/run-001
```

### `ascot report`

Display report from an existing run:

```bash
python -m ascot report ./benchmark/run-001
python -m ascot report ./benchmark/run-001 -f json --show-cost
```

## Output Structure

Each run produces:

```
benchmark/
  run-001/
    meta.json              # Run metadata (suite, model, timestamp)
    report.json            # Aggregated results
    create-report/
      eval.json            # Test case definition
      result.json          # Scores, expectation results, metrics
      events.jsonl         # Raw OpenCode event stream
      workspace/           # Preserved output files
        report.docx
    csv-to-xlsx/
      ...
```

## Report Example

```
======================================================================
 Ascot Benchmark: doc-generation  |  run-001
======================================================================
 Case                   Score      Turns   Tokens    Time
----------------------------------------------------------------------
 create-report          25/25         3    2,134    8.2s
 summarize-pdf          20/20         2    1,803    6.1s
 csv-to-xlsx            5/20          5    3,201   12.1s
----------------------------------------------------------------------
 Total: 3 | Score: 50/65 (76.9%)
 Turns: 10 | Tokens: 7,138 | Time: 26.4s

 Details:
   csv-to-xlsx (5/20):
     [PASS]   5pts  output.xlsx exists
     [FAIL]  15pts  output.xlsx contains all rows... - missing city column
======================================================================
```

## Permissions

Ascot injects default permissions via `OPENCODE_CONFIG_CONTENT`:

```json
{"*": "allow", "question": "deny", "external_directory": "deny", "doom_loop": "deny"}
```

- All tools allowed inside the workspace
- `question` denied (non-interactive)
- `external_directory` denied (sandbox to workspace)
- `doom_loop` denied (prevent infinite retries)

To override, add `permission` in your suite's `opencode.json`. User overrides are merged on top of defaults:

```json
{
  "permission": {
    "bash": "deny",
    "webfetch": "deny"
  }
}
```
