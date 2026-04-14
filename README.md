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

### Multi-Trial Runs

LLM outputs can vary between runs. Use `--trials` to run each case multiple times and average the results:

```bash
python -m ascot run ./my-suite ./tests.yaml --trials 3
```

Each trial runs independently with its own workspace. Scores are averaged across trials (per-expectation pass rate determines earned points). All `(case, trial)` pairs share the concurrency pool, so `--trials 3 -c 6` can run up to 6 trials simultaneously across different cases.

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
grading_model: null                               # optional, model for LLM judge (defaults to default_model)
default_workspace_files_from: fixtures/shared  # optional, inherited by all cases

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
| `workspace_files_from` | no | Directory of files to copy into workspace (supports binary); inherits from suite-level `default_workspace_files_from` if not set |
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

Ascot uses an LLM judge for grading. A separate OpenCode session evaluates the output against all `expectations`. The judge runs in its own isolated workspace with full permissions, so it can write and run scripts to inspect binary files (xlsx, docx, images, etc.).

The judge receives two sources of evidence:
- **`events.jsonl`**: The agent's complete execution log — every reasoning step, tool call, and output — so the judge can understand exactly what the agent did.
- **`output/`**: All files the agent produced, for direct inspection.

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
| `-c, --concurrency` | 4 | Max parallel cases |
| `-t, --timeout` | per-case | Override timeout for all cases (seconds) |
| `-n, --trials` | 3 | Number of times to run each test case |
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

### `ascot review`

Analyze failed cases from an existing run. A diagnostic LLM agent reads each failed case's events and outputs across all trials, compares successful vs failed trials, and writes a markdown report:

```bash
python -m ascot review ./benchmark/run-001
python -m ascot review ./benchmark/run-001 --model anthropic/claude-sonnet-4-20250514
```

| Flag | Default | Description |
|---|---|---|
| `--binary` | `opencode` | Path to OpenCode binary |
| `--model` | opencode default | Model for the review agent |

Results are saved to `<case_dir>/review.md`. Cases where all trials passed are skipped.

### `ascot report`

Display report from an existing run:

```bash
python -m ascot report ./benchmark/run-001
python -m ascot report ./benchmark/run-001 -f json --show-cost
```

### `ascot inspect`

Analyze a single case's execution for performance debugging. Shows per-step reasoning time, tool execution time, token usage, and cost:

```bash
python -m ascot inspect ./benchmark/run-001/create-report/trial-1
python -m ascot inspect ./benchmark/run-001/create-report/trial-1 -f json
```

## Output Structure

Each run produces:

```
benchmark/
  run-001/
    meta.json              # Run metadata (suite, model, timestamp, trials)
    report.json            # Aggregated results
    create-report/
      eval.json            # Test case definition
      result.json          # Aggregated scores across trials
      trial-1/
        result.json        # Per-trial scores, metrics, phases
        events.jsonl       # Raw OpenCode event stream
        workspace/         # Preserved output files
          report.docx
      trial-2/
        ...
    csv-to-xlsx/
      ...
```

When `--trials 1` (default), each case still has a `trial-1/` subdirectory.

### `result.json` Phases

Each `result.json` includes a `phases` object with per-phase timing, token usage, and cost:

```json
{
  "phases": {
    "workspace_setup": {"duration_s": 0.006},
    "agent_run": {
      "duration_s": 23.6, "turns": 3, "cost": 0.05,
      "tokens": {"total": 28954, "input": 7550, "output": 69,
                 "reasoning": 87, "cache_read": 21248, "cache_write": 0}
    },
    "workspace_preserve": {"duration_s": 0.001},
    "grading": {
      "duration_s": 21.8, "turns": 2, "cost": 0.03,
      "tokens": {"total": 19518, "input": 7450, "output": 63,
                 "reasoning": 229, "cache_read": 11776, "cache_write": 0}
    }
  }
}
```

## Report Example

```
======================================================================
 Ascot Benchmark: doc-generation  |  run-001  |  3 trials (avg)
======================================================================
 Case                   Score      Turns   Tokens    Time
----------------------------------------------------------------------
 create-report          25/25         3    6,402   24.6s
 summarize-pdf          20/20         2    5,409   18.3s
 csv-to-xlsx            12/20         5    9,603   36.3s
----------------------------------------------------------------------
 Total: 3 | Score: 57/65 (87.7%)
 Turns: 10 | Tokens: 21,414 | Time: 79.2s

 Details:
   csv-to-xlsx (12/20):
     Trial 1: [PASS] 20/20
     Trial 2: [FAIL] 5/20
       [FAIL]  15pts  output.xlsx contains all rows... - missing city column
     Trial 3: [FAIL] 10/20
       [FAIL]   5pts  output.xlsx exists - file named output.xl instead
       [FAIL]   5pts  data is formatted correctly - no header row
======================================================================
```

## Inspect Example

```
===========================================================================
 Ascot Inspect: csv-to-xlsx
===========================================================================
 Steps (5 total, 12.1s):

   Step  Reasoning  Tool         Tool Time    Tokens    Cost  Status
   ------------------------------------------------------------------------
      1       3.9s  read              18ms     9,588    0.00  completed
      2      942ms  glob              24ms     9,652    0.00  completed
      3       2.2s  read              42ms     9,806    0.00  completed
      4       2.6s  write             15ms     9,941    0.00  completed
      5       74ms  (none)               -    10,007    0.00  stop
   ------------------------------------------------------------------------

 Summary:
   Reasoning: 9.7s (80%)   Tool exec: 99ms (1%)
   Input: 8,013   Output: 143   Reasoning: 134   Cache read: 40,704
===========================================================================
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
