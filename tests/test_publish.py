"""Tests for ascot.publish."""

from __future__ import annotations

import json

import pytest

from ascot.publish import (
    CREATE_CASES_TABLE,
    CREATE_RUNS_TABLE,
    PublishError,
    _parse_mysql_url,
    _resolve_connect_kwargs,
    init_publish_schema,
    publish_run,
    resolve_mysql_url,
)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        if self.conn.raise_on_execute:
            raise self.conn.raise_on_execute

    def fetchone(self):
        return self.conn.fetchone_value


class FakeConnection:
    def __init__(self, *, fetchone_value=(42,), raise_on_execute=None):
        self.executed = []
        self.fetchone_value = fetchone_value
        self.raise_on_execute = raise_on_execute
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakeConnector:
    def __init__(self, conn):
        self.conn = conn
        self.connect_kwargs = None

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs
        return self.conn


class MissingTableError(Exception):
    def __init__(self):
        super().__init__(1146, "Table does not exist")


def _write_report(tmp_path, data):
    run_dir = tmp_path / "run-001"
    run_dir.mkdir()
    (run_dir / "report.json").write_text(json.dumps(data), encoding="utf-8")
    return run_dir


def _sample_report():
    return {
        "suite_name": "suite",
        "run_id": "run-001",
        "timestamp": "2026-04-16T08:47:38.640240+00:00",
        "num_trials": 3,
        "total": 2,
        "total_score": 7,
        "max_score": 10,
        "total_turns": 4,
        "total_tokens": 1234,
        "total_duration_s": 12.5,
        "total_cost": 0.42,
        "results": [
            {
                "case_id": "pass-case",
                "score": 5,
                "max_score": 5,
                "turns": 2,
                "token_usage": {"total": 100},
                "duration_s": 4.0,
                "total_cost": 0.1,
                "error": None,
                "num_trials": 3,
                "trial_results": [{"ignored": True}],
                "expectation_results": [{"ignored": True}],
            },
            {
                "case_id": "zero-max",
                "score": 0,
                "max_score": 0,
                "token_usage": {},
            },
        ],
    }


def test_resolve_mysql_url_prefers_explicit(monkeypatch):
    monkeypatch.setenv("ASCOT_MYSQL_URL", "mysql://env:pw@host/db")
    assert resolve_mysql_url("mysql://arg:pw@host/db") == "mysql://arg:pw@host/db"


def test_resolve_mysql_url_requires_value(monkeypatch):
    monkeypatch.delenv("ASCOT_MYSQL_URL", raising=False)
    with pytest.raises(PublishError, match="Missing MySQL URL"):
        resolve_mysql_url(None)


def test_parse_mysql_url():
    parsed = _parse_mysql_url("mysql://user:pass@example.com:3307/ascot")
    assert parsed["host"] == "example.com"
    assert parsed["port"] == 3307
    assert parsed["user"] == "user"
    assert parsed["password"] == "pass"
    assert parsed["database"] == "ascot"


def test_parse_mysql_url_requires_mysql_scheme():
    with pytest.raises(PublishError, match="mysql://"):
        _parse_mysql_url("postgres://user:pass@example.com/db")


def test_config_file_connection_kwargs(tmp_path):
    config = tmp_path / "publish.yaml"
    config.write_text(
        """
mysql:
  host: localhost
  port: 3307
  user: admin_user
  password: admAdmin!!!
  database: ascot_test
""",
        encoding="utf-8",
    )

    parsed = _resolve_connect_kwargs(None, config)

    assert parsed["host"] == "localhost"
    assert parsed["port"] == 3307
    assert parsed["user"] == "admin_user"
    assert parsed["password"] == "admAdmin!!!"
    assert parsed["database"] == "ascot_test"


def test_config_file_supports_mysql_url(tmp_path):
    config = tmp_path / "publish.yaml"
    config.write_text("mysql_url: mysql://user:pass@example.com/ascot\n", encoding="utf-8")

    parsed = _resolve_connect_kwargs(None, config)

    assert parsed["host"] == "example.com"
    assert parsed["database"] == "ascot"


def test_mysql_url_overrides_config(tmp_path):
    config = tmp_path / "publish.yaml"
    config.write_text(
        """
mysql:
  host: config-host
  user: config-user
  database: config-db
""",
        encoding="utf-8",
    )

    parsed = _resolve_connect_kwargs("mysql://arg:pw@arg-host/arg-db", config)

    assert parsed["host"] == "arg-host"
    assert parsed["database"] == "arg-db"


def test_init_publish_schema_executes_tables():
    conn = FakeConnection()
    connector = FakeConnector(conn)

    init_publish_schema("mysql://user:pass@example.com/ascot", connector=connector)

    sqls = [sql for sql, _ in conn.executed]
    assert CREATE_RUNS_TABLE in sqls
    assert CREATE_CASES_TABLE in sqls
    assert conn.committed
    assert conn.closed


def test_publish_run_writes_run_and_case_rows(tmp_path):
    run_dir = _write_report(tmp_path, _sample_report())
    conn = FakeConnection()
    connector = FakeConnector(conn)

    summary = publish_run(run_dir, "mysql://user:pass@example.com/ascot", connector=connector)

    assert summary == {"suite_name": "suite", "run_id": "run-001", "case_count": 2}
    assert conn.committed
    assert conn.closed

    run_inserts = [item for item in conn.executed if "INSERT INTO ascot_runs" in item[0]]
    assert len(run_inserts) == 1
    assert run_inserts[0][1][7] == 0.7  # score_pct

    case_inserts = [item for item in conn.executed if "INSERT INTO ascot_case_results" in item[0]]
    assert len(case_inserts) == 2
    assert case_inserts[0][1][5] == 1  # passed
    assert case_inserts[1][1][4] is None  # score_pct with max_score = 0


def test_publish_run_requires_report_json(tmp_path):
    run_dir = tmp_path / "run-001"
    run_dir.mkdir()
    with pytest.raises(PublishError, match="No report.json"):
        publish_run(run_dir, "mysql://user:pass@example.com/ascot", connector=FakeConnector(FakeConnection()))


def test_publish_run_missing_table_suggests_init(tmp_path):
    run_dir = _write_report(tmp_path, _sample_report())
    conn = FakeConnection(raise_on_execute=MissingTableError())
    connector = FakeConnector(conn)

    with pytest.raises(PublishError, match="init-publish"):
        publish_run(run_dir, "mysql://user:pass@example.com/ascot", connector=connector)

    assert conn.rolled_back
    assert conn.closed
