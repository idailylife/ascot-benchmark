# MySQL Publish for Grafana

Ascot can publish aggregated benchmark results to MySQL so Grafana can show score, cost, token, and duration trends. This is optional and only stores key run/case metrics; detailed trial and expectation evidence stays in the local `benchmark/run-xxx/` files.

## Install

MySQL publishing requires the optional dependency:

```bash
pip install "ascot[mysql]"
```

For local development from this repo:

```bash
pip install -e ".[mysql]"
```

Core commands such as `run`, `report`, `grade`, `review`, and `inspect` do not require this extra.

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

Initialize the schema once:

```bash
ascot init-publish --config ascot-publish.yaml
```

This creates two tables:

- `ascot_runs`: one row per benchmark run.
- `ascot_case_results`: one row per aggregated case result.

The command is idempotent and can be run again after deployment.

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

Publishing is idempotent for the same `(suite_name, run_id, run_timestamp)`. Re-running `publish` updates the run row and replaces that run's case rows.

## Stored Data

`ascot_runs` stores:

- `suite_name`, `run_id`, `run_timestamp`
- `num_trials`, `total_cases`
- `total_score`, `max_score`, `score_pct`
- `total_turns`, `total_tokens`, `total_duration_s`, `total_cost`
- `source_path`

`ascot_case_results` stores:

- `case_id`
- `score`, `max_score`, `score_pct`, `passed`
- `turns`, `tokens_total`, `duration_s`, `total_cost`
- `error`, `num_trials`

Ascot does not publish trial rows, expectation rows, judge reasoning, final text, or phase JSON. Use `source_path` to find the original local run directory when deeper debugging is needed.

## Grafana Queries

Use Grafana's MySQL data source. Configure the Grafana datasource session timezone as UTC if your MySQL server is not already using UTC.

Score trend:

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

Case score trend:

```sql
SELECT
  $__time(r.run_timestamp),
  c.score_pct,
  c.case_id AS metric
FROM ascot_case_results c
JOIN ascot_runs r ON r.id = c.run_db_id
WHERE $__timeFilter(r.run_timestamp)
  AND r.suite_name = '$suite'
ORDER BY r.run_timestamp
```

Failing cases table:

```sql
SELECT
  r.run_timestamp,
  r.suite_name,
  r.run_id,
  c.case_id,
  c.score,
  c.max_score,
  c.error,
  r.source_path
FROM ascot_case_results c
JOIN ascot_runs r ON r.id = c.run_db_id
WHERE (c.passed = 0 OR c.error IS NOT NULL)
ORDER BY r.run_timestamp DESC
```

Suite variable:

```sql
SELECT DISTINCT suite_name FROM ascot_runs ORDER BY suite_name
```

## Troubleshooting

- `MySQL publish requires the optional dependency`: install `ascot[mysql]`.
- `Publish tables are missing`: run `ascot init-publish` first.
- `Missing MySQL configuration`: pass `--config`, set `ASCOT_PUBLISH_CONFIG`, pass `--mysql-url`, or set `ASCOT_MYSQL_URL`.
- Authentication succeeds with `mysql` CLI but fails in Ascot: check whether the MySQL user is scoped to `localhost` versus `%`, and whether Ascot is connecting to the same host.
