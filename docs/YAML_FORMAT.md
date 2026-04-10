# Ascot Test Case YAML Format

## Structure

```yaml
name: <suite-name>
description: "<description>"
default_timeout_s: 300
default_model: null          # optional model override
default_workspace_files_from: null  # optional, inherited by all cases

test_cases:
  - id: <kebab-case-id>
    description: "<short description>"
    prompt: |
      <prompt sent to the agent>
    expectations:              # list of grading expectations
      - desc: "<what to check>"
        score: 10              # points for this expectation (default: 1)
      - desc: "<another check>"
        score: 5
    workspace_files_from: <dir path>  # optional, copy directory (binary-safe)
    timeout_s: 300           # optional, per-case
    model: null              # optional, per-case
    agent: null              # optional, per-case
    tags: []                 # optional, for --tag filtering
```

## Fields

| Field | Required | Description |
|---|---|---|
| `id` | yes | Unique kebab-case identifier |
| `prompt` | yes | Instruction sent to the agent |
| `expectations` | no | List of `{desc, score}` items evaluated by LLM judge |
| `workspace_files_from` | no | Directory copied into workspace (supports binary); inherits from suite-level `default_workspace_files_from` if not set |
| `timeout_s` | no | Timeout in seconds (default: 120) |
| `model` | no | Model override |
| `agent` | no | Agent override |
| `tags` | no | Tags for `--tag` filtering |

## Expectations

Each expectation has:
- `desc` (required): Natural language description of what to check
- `score` (optional, default: 1): Points awarded if this expectation is met

The LLM judge evaluates all expectations and assigns scores. Results are shown as `earned/total` (e.g., `30/50`).

## Example

Draft input from user:
```
- 解析PDF标题为"季度报告"
- PDF共42页
- 表格有4列
```

Converted output:
```yaml
name: pdf-reading
description: "PDF reading benchmark"
default_timeout_s: 300
default_workspace_files_from: ../pdf-reading/input

test_cases:
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

  - id: page-count
    description: "Identify total page count"
    prompt: |
      Use the pdf-reader skill to check input/report.pdf metadata,
      write the page count to page_count.txt (number only).
    expectations:
      - desc: page_count.txt exists
        score: 2
      - desc: page_count.txt contains the number 42
        score: 3

  - id: table-columns
    description: "Verify table has 4 columns"
    prompt: |
      Use the pdf-reader skill to extract the table from input/report.pdf,
      save to table.md.
    expectations:
      - desc: table.md exists
        score: 2
      - desc: table.md contains a table with exactly 4 columns
        score: 8
```
