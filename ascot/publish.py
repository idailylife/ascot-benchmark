"""Publish Ascot benchmark results to MySQL."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yaml


class PublishError(RuntimeError):
    """User-facing publish error."""


CREATE_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS ascot_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  suite_name VARCHAR(255) NOT NULL,
  run_id VARCHAR(64) NOT NULL,
  run_timestamp DATETIME(6) NOT NULL,
  num_trials INT NOT NULL DEFAULT 1,
  total_cases INT NOT NULL DEFAULT 0,
  total_score DOUBLE NOT NULL DEFAULT 0,
  max_score DOUBLE NOT NULL DEFAULT 0,
  score_pct DOUBLE NULL,
  total_turns INT NOT NULL DEFAULT 0,
  total_tokens BIGINT NOT NULL DEFAULT 0,
  total_duration_s DOUBLE NOT NULL DEFAULT 0,
  total_cost DOUBLE NOT NULL DEFAULT 0,
  source_path TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_ascot_runs_identity (suite_name, run_id, run_timestamp),
  KEY idx_ascot_runs_timestamp (run_timestamp),
  KEY idx_ascot_runs_suite_timestamp (suite_name, run_timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


CREATE_CASES_TABLE = """
CREATE TABLE IF NOT EXISTS ascot_case_results (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  run_db_id BIGINT UNSIGNED NOT NULL,
  case_id VARCHAR(255) NOT NULL,
  score DOUBLE NOT NULL DEFAULT 0,
  max_score DOUBLE NOT NULL DEFAULT 0,
  score_pct DOUBLE NULL,
  passed TINYINT(1) NOT NULL DEFAULT 0,
  turns INT NOT NULL DEFAULT 0,
  tokens_total BIGINT NOT NULL DEFAULT 0,
  duration_s DOUBLE NOT NULL DEFAULT 0,
  total_cost DOUBLE NOT NULL DEFAULT 0,
  error TEXT NULL,
  num_trials INT NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_ascot_case_results_identity (run_db_id, case_id),
  KEY idx_ascot_case_results_case_id (case_id),
  CONSTRAINT fk_ascot_case_results_run
    FOREIGN KEY (run_db_id) REFERENCES ascot_runs(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


INSERT_RUN_SQL = """
INSERT INTO ascot_runs (
  suite_name, run_id, run_timestamp, num_trials, total_cases,
  total_score, max_score, score_pct, total_turns, total_tokens,
  total_duration_s, total_cost, source_path
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
  num_trials = VALUES(num_trials),
  total_cases = VALUES(total_cases),
  total_score = VALUES(total_score),
  max_score = VALUES(max_score),
  score_pct = VALUES(score_pct),
  total_turns = VALUES(total_turns),
  total_tokens = VALUES(total_tokens),
  total_duration_s = VALUES(total_duration_s),
  total_cost = VALUES(total_cost),
  source_path = VALUES(source_path),
  updated_at = CURRENT_TIMESTAMP
"""


SELECT_RUN_ID_SQL = """
SELECT id FROM ascot_runs
WHERE suite_name = %s AND run_id = %s AND run_timestamp = %s
"""


INSERT_CASE_SQL = """
INSERT INTO ascot_case_results (
  run_db_id, case_id, score, max_score, score_pct, passed,
  turns, tokens_total, duration_s, total_cost, error, num_trials
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def resolve_mysql_url(mysql_url: str | None) -> str:
    """Resolve a MySQL URL from CLI args or environment.

    This helper is kept for callers that only support URL-style configuration.
    CLI publish commands use the richer config resolution in ``_connect``.
    """
    resolved = mysql_url or os.environ.get("ASCOT_MYSQL_URL")
    if not resolved:
        raise PublishError("Missing MySQL URL. Pass --mysql-url or set ASCOT_MYSQL_URL.")
    return resolved


def init_publish_schema(
    mysql_url: str | None = None,
    *,
    config_path: str | Path | None = None,
    connector: Any | None = None,
) -> None:
    """Initialize MySQL tables for Ascot publish."""
    conn = _connect(mysql_url, config_path=config_path, connector=connector)
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_RUNS_TABLE)
            cur.execute(CREATE_CASES_TABLE)
        conn.commit()
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def publish_run(
    run_dir: str | Path,
    mysql_url: str | None = None,
    *,
    config_path: str | Path | None = None,
    connector: Any | None = None,
) -> dict[str, Any]:
    """Publish one run's report.json to MySQL.

    Returns a short summary dict for CLI output.
    """
    run_path = Path(run_dir).resolve()
    report = _load_report(run_path)
    run_timestamp = _parse_timestamp(report["timestamp"])
    run_score_pct = _score_pct(report.get("total_score", 0), report.get("max_score", 0))

    conn = _connect(mysql_url, config_path=config_path, connector=connector)
    try:
        with conn.cursor() as cur:
            cur.execute(
                INSERT_RUN_SQL,
                (
                    report["suite_name"],
                    report["run_id"],
                    run_timestamp,
                    report.get("num_trials", 1),
                    report.get("total", len(report.get("results", []))),
                    report.get("total_score", 0),
                    report.get("max_score", 0),
                    run_score_pct,
                    report.get("total_turns", 0),
                    report.get("total_tokens", 0),
                    report.get("total_duration_s", 0.0),
                    report.get("total_cost", 0.0),
                    str(run_path),
                ),
            )
            cur.execute(
                SELECT_RUN_ID_SQL,
                (report["suite_name"], report["run_id"], run_timestamp),
            )
            row = cur.fetchone()
            if not row:
                raise PublishError("Could not read run id after publishing run.")
            run_db_id = row[0]

            cur.execute("DELETE FROM ascot_case_results WHERE run_db_id = %s", (run_db_id,))
            for case in report.get("results", []):
                cur.execute(INSERT_CASE_SQL, _case_params(run_db_id, case))
        conn.commit()
    except Exception as exc:
        _rollback_quietly(conn)
        if _is_missing_table_error(exc):
            raise PublishError(
                "Publish tables are missing. Run ascot init-publish first."
            ) from None
        raise
    finally:
        conn.close()

    return {
        "suite_name": report["suite_name"],
        "run_id": report["run_id"],
        "case_count": len(report.get("results", [])),
    }


def _case_params(run_db_id: int, case: dict[str, Any]) -> tuple[Any, ...]:
    score = case.get("score", 0)
    max_score = case.get("max_score", 0)
    return (
        run_db_id,
        case["case_id"],
        score,
        max_score,
        _score_pct(score, max_score),
        1 if max_score > 0 and score >= max_score and not case.get("error") else 0,
        case.get("turns", 0),
        case.get("token_usage", {}).get("total", 0),
        case.get("duration_s", 0.0),
        case.get("total_cost", 0.0),
        case.get("error"),
        case.get("num_trials", 1),
    )


def _load_report(run_dir: Path) -> dict[str, Any]:
    report_path = run_dir / "report.json"
    if not report_path.exists():
        raise PublishError(f"No report.json found in {run_dir}")
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)
    for key in ("suite_name", "run_id", "timestamp"):
        if key not in report:
            raise PublishError(f"Invalid report.json: missing {key}")
    return report


def _score_pct(score: float, max_score: float) -> float | None:
    return None if max_score <= 0 else score / max_score


def _parse_timestamp(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PublishError(f"Invalid report timestamp: {value}") from exc
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _connect(
    mysql_url: str | None,
    *,
    config_path: str | Path | None = None,
    connector: Any | None = None,
) -> Any:
    connector = connector or _import_pymysql()
    return connector.connect(**_resolve_connect_kwargs(mysql_url, config_path))


def _import_pymysql() -> Any:
    try:
        import pymysql  # type: ignore[import-not-found]
    except ImportError:
        raise PublishError(
            "MySQL publish requires pymysql. Reinstall ascot or run: pip install pymysql"
        ) from None
    return pymysql


def _parse_mysql_url(mysql_url: str) -> dict[str, Any]:
    parsed = urlparse(mysql_url)
    if parsed.scheme != "mysql":
        raise PublishError("MySQL URL must start with mysql://")
    if not parsed.hostname or not parsed.username or not parsed.path.strip("/"):
        raise PublishError("MySQL URL must include user, host, and database.")
    try:
        port = parsed.port or 3306
    except ValueError as exc:
        raise PublishError("MySQL URL has an invalid port.") from exc
    return {
        "host": parsed.hostname,
        "port": port,
        "user": unquote(parsed.username),
        "password": unquote(parsed.password or ""),
        "database": unquote(parsed.path.lstrip("/")),
        "charset": "utf8mb4",
        "autocommit": False,
    }


def _resolve_connect_kwargs(
    mysql_url: str | None,
    config_path: str | Path | None,
) -> dict[str, Any]:
    if mysql_url:
        return _parse_mysql_url(mysql_url)
    if config_path or os.environ.get("ASCOT_PUBLISH_CONFIG"):
        return _load_config_connect_kwargs(Path(config_path or os.environ["ASCOT_PUBLISH_CONFIG"]))
    if os.environ.get("ASCOT_MYSQL_URL"):
        return _parse_mysql_url(os.environ["ASCOT_MYSQL_URL"])
    raise PublishError(
        "Missing MySQL configuration. Pass --mysql-url, pass --config, "
        "or set ASCOT_MYSQL_URL / ASCOT_PUBLISH_CONFIG."
    )


def _load_config_connect_kwargs(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PublishError(f"Publish config file does not exist: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise PublishError(f"Expected publish config to be a YAML object: {path}")

    if data.get("mysql_url"):
        return _parse_mysql_url(str(data["mysql_url"]))

    mysql = data.get("mysql")
    if not isinstance(mysql, dict):
        raise PublishError("Publish config must contain mysql_url or mysql settings.")

    missing = [k for k in ("host", "user", "database") if not mysql.get(k)]
    if missing:
        raise PublishError(f"Publish config missing mysql field(s): {', '.join(missing)}")

    return {
        "host": str(mysql["host"]),
        "port": int(mysql.get("port", 3306)),
        "user": str(mysql["user"]),
        "password": str(mysql.get("password", "")),
        "database": str(mysql["database"]),
        "charset": str(mysql.get("charset", "utf8mb4")),
        "autocommit": False,
    }


def _rollback_quietly(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:
        pass


def _is_missing_table_error(exc: Exception) -> bool:
    args = getattr(exc, "args", ())
    return bool(args and args[0] == 1146)
