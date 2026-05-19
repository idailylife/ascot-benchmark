# MySQL Publish for Grafana

Ascot can publish benchmark results to MySQL so Grafana can show score, cost, token, and duration trends. It stores per-trial rows so dashboards can compute their own aggregates (averages, pass rates, variance). Detailed expectation evidence, judge reasoning, and final text stay in the local `benchmark/run-xxx/` files.

## Install

MySQL publishing is included in the default Ascot install:

```bash
pip install ascot
```

For local development from this repo:

```bash
pip install -e .
```

## MySQL Setup

Create a database and a user with write access for Ascot:

```sql
CREATE DATABASE ascot;
CREATE USER 'ascot_writer'@'localhost' IDENTIFIED BY 'change-me';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX
  ON ascot.* TO 'ascot_writer'@'localhost';
```

For Grafana, use a separate read-only user:

```sql
CREATE USER 'ascot_reader'@'%' IDENTIFIED BY 'change-me';
GRANT SELECT ON ascot.* TO 'ascot_reader'@'%';
```

In stricter environments, run `ascot init-publish` with a schema-management user, then use a writer that only has `SELECT, INSERT, UPDATE, DELETE` for `ascot publish`.

## Configuration

The recommended configuration format is YAML because passwords can be written normally without URL encoding:

```yaml
mysql:
  host: localhost
  port: 3306
  user: ascot_writer
  password: change-me
  database: ascot
```

Save it as `ascot-publish.yaml` and restrict permissions if it contains a password:

```bash
chmod 600 ascot-publish.yaml
```

You can also use a URL:

```yaml
mysql_url: mysql://ascot_writer:change-me@localhost:3306/ascot
```

Configuration precedence is:

1. `--mysql-url`
2. `--config` or `ASCOT_PUBLISH_CONFIG`
3. `ASCOT_MYSQL_URL`

When using `mysql://...`, URL-encode special characters in the password. The YAML `mysql:` object avoids that issue.

## Initialize Tables

Initialize (or upgrade) the schema:

```bash
ascot init-publish --config ascot-publish.yaml
```

`init-publish` applies any pending schema migrations. It is idempotent — re-running on an up-to-date database does nothing — and is the same command you use to upgrade after a future Ascot release that adds tables or columns. Each applied migration is printed so you can see what changed.

The current schema has three tables:

- `ascot_schema_version`: tracks which migrations have been applied.
- `ascot_runs`: one row per benchmark run with overall totals.
- `ascot_trial_results`: one row per `(run, case, trial)` with per-trial metrics.

There is no separate case-level table — case aggregates are computed in SQL via `GROUP BY case_id` on `ascot_trial_results`.

## Publish a Run

Publish an existing run directory:

```bash
ascot publish ./benchmark/run-001 --config ascot-publish.yaml
```

Or set the config path once:

```bash
export ASCOT_PUBLISH_CONFIG=ascot-publish.yaml
ascot publish ./benchmark/run-001
```

Publishing is idempotent for the same `(suite_name, run_id, run_timestamp)`. Re-running `publish` updates the run row and replaces that run's trial rows.

## Upgrading from Ascot 0.7.x

Ascot 0.8.0 reshapes the schema (removes `ascot_case_results`, adds `ascot_trial_results`) and does not migrate existing data. Drop the old tables and re-init:

```sql
DROP TABLE IF EXISTS ascot_case_results;
DROP TABLE IF EXISTS ascot_runs;
```

Then upgrade and re-init:

```bash
pip install -U ascot
ascot init-publish --config ascot-publish.yaml
```

Historical run data is not automatically backfilled. If you want old runs visible in the new schema, re-publish them from the on-disk `run-NNN/` directories:

```bash
for d in benchmark/run-*; do
  ascot publish "$d" --config ascot-publish.yaml
done
```

## Stored Data

`ascot_runs` stores:

- `suite_name`, `run_id`, `run_timestamp`
- `num_trials`, `total_cases`
- `total_score`, `max_score`, `score_pct`
- `total_turns`, `total_tokens`, `total_duration_s`, `total_cost`
- `source_path` (path to the on-disk run directory)

`ascot_trial_results` stores, per trial:

- `case_id`, `trial_num`
- `score`, `max_score`, `score_pct`, `passed`
- `turns`
- `tokens_total`, `tokens_input`, `tokens_output`, `tokens_reasoning`, `tokens_cache_read`, `tokens_cache_write`
- `duration_s`, `total_cost`
- `exit_code`, `error`

Ascot does not publish expectation rows, judge reasoning, final text, or phase JSON. Use `source_path` to find the original local run directory when deeper debugging is needed.

## Grafana Queries

Use Grafana's MySQL data source. Configure the Grafana datasource session timezone as UTC if your MySQL server is not already using UTC.

Run-level score trend:

```sql
SELECT
  $__time(run_timestamp),
  score_pct
FROM ascot_runs
WHERE $__timeFilter(run_timestamp)
  AND suite_name = '$suite'
ORDER BY run_timestamp
```

Cost and token trend:

```sql
SELECT
  $__time(run_timestamp),
  total_cost,
  total_tokens
FROM ascot_runs
WHERE $__timeFilter(run_timestamp)
  AND suite_name = '$suite'
ORDER BY run_timestamp
```

Case-level score trend (mean across trials):

```sql
SELECT
  $__time(r.run_timestamp),
  AVG(t.score_pct) AS score_pct,
  t.case_id AS metric
FROM ascot_trial_results t
JOIN ascot_runs r ON r.id = t.run_db_id
WHERE $__timeFilter(r.run_timestamp)
  AND r.suite_name = '$suite'
GROUP BY r.run_timestamp, t.case_id
ORDER BY r.run_timestamp
```

Pass rate per case (how often the case succeeds across trials):

```sql
SELECT
  $__time(r.run_timestamp),
  AVG(t.passed) AS pass_rate,
  t.case_id AS metric
FROM ascot_trial_results t
JOIN ascot_runs r ON r.id = t.run_db_id
WHERE $__timeFilter(r.run_timestamp)
  AND r.suite_name = '$suite'
GROUP BY r.run_timestamp, t.case_id
ORDER BY r.run_timestamp
```

Flakiness — cases with non-zero score variance across trials in the same run:

```sql
SELECT
  r.run_timestamp,
  t.case_id,
  COUNT(*) AS trials,
  AVG(t.score_pct) AS mean_score,
  STDDEV_SAMP(t.score_pct) AS stddev_score
FROM ascot_trial_results t
JOIN ascot_runs r ON r.id = t.run_db_id
WHERE $__timeFilter(r.run_timestamp)
  AND r.suite_name = '$suite'
GROUP BY r.id, t.case_id
HAVING STDDEV_SAMP(t.score_pct) > 0
ORDER BY stddev_score DESC
```

Duration distribution (P50 / P95 per case):

```sql
SELECT
  t.case_id,
  AVG(t.duration_s) AS mean_s,
  MAX(t.duration_s) AS max_s,
  MIN(t.duration_s) AS min_s
FROM ascot_trial_results t
JOIN ascot_runs r ON r.id = t.run_db_id
WHERE $__timeFilter(r.run_timestamp)
  AND r.suite_name = '$suite'
GROUP BY t.case_id
ORDER BY mean_s DESC
```

Failing trials table:

```sql
SELECT
  r.run_timestamp,
  r.suite_name,
  r.run_id,
  t.case_id,
  t.trial_num,
  t.score,
  t.max_score,
  t.error,
  r.source_path
FROM ascot_trial_results t
JOIN ascot_runs r ON r.id = t.run_db_id
WHERE (t.passed = 0 OR t.error IS NOT NULL)
ORDER BY r.run_timestamp DESC
```

Suite variable:

```sql
SELECT DISTINCT suite_name FROM ascot_runs ORDER BY suite_name
```

## Troubleshooting

- `MySQL publish requires pymysql`: reinstall Ascot or run `pip install pymysql`.
- `Publish tables are missing`: run `ascot init-publish` first.
- `Missing MySQL configuration`: pass `--config`, set `ASCOT_PUBLISH_CONFIG`, pass `--mysql-url`, or set `ASCOT_MYSQL_URL`.
- Authentication succeeds with `mysql` CLI but fails in Ascot: check whether the MySQL user is scoped to `localhost` versus `%`, and whether Ascot is connecting to the same host.
