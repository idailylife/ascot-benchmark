"""Tests for ascot.publish."""

from __future__ import annotations

import json

import pytest

from ascot.publish import (
    CREATE_RUNS_TABLE,
    CREATE_TRIALS_TABLE,
    MIGRATIONS,
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
        self._fetchall_result: list = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        if self.conn.raise_on_execute:
            raise self.conn.raise_on_execute
        if "FROM ascot_schema_version" in sql and "SELECT" in sql.upper():
            self._fetchall_result = [(v,) for v in sorted(self.conn.applied_versions)]
        else:
            self._fetchall_result = []
        if sql.lstrip().upper().startswith("INSERT INTO ASCOT_SCHEMA_VERSION") and params:
            self.conn.applied_versions.add(params[0])

    def fetchone(self):
        return self.conn.fetchone_value

    def fetchall(self):
        return list(self._fetchall_result)


class FakeConnection:
    def __init__(
        self,
        *,
        fetchone_value=(42,),
        raise_on_execute=None,
        applied_versions: set[int] | None = None,
    ):
        self.executed: list = []
        self.fetchone_value = fetchone_value
        self.raise_on_execute = raise_on_execute
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.applied_versions: set[int] = (
            set(applied_versions) if applied_versions else set()
        )

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
                "trial_results": [
                    {
                        "score": 5,
                        "max_score": 5,
                        "turns": 2,
                        "token_usage": {
                            "total": 40, "input": 25, "output": 15,
                            "reasoning": 0, "cache_read": 5, "cache_write": 1,
                        },
                        "duration_s": 1.5,
                        "total_cost": 0.03,
                        "exit_code": 0,
                        "error": None,
                    },
                    {
                        "score": 5,
                        "max_score": 5,
                        "turns": 2,
                        "token_usage": {"total": 30, "input": 20, "output": 10},
                        "duration_s": 1.2,
                        "total_cost": 0.03,
                        "exit_code": 0,
                    },
                    {
                        "score": 0,
                        "max_score": 5,
                        "turns": 3,
                        "token_usage": {"total": 30},
                        "duration_s": 1.3,
                        "total_cost": 0.04,
                        "exit_code": 1,
                        "error": "timeout",
                    },
                ],
                "expectation_results": [{"ignored": True}],
            },
            {
                "case_id": "zero-max",
                "score": 0,
                "max_score": 0,
                "token_usage": {},
                "trial_results": [],
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


def test_migrations_are_well_formed():
    versions = [m[0] for m in MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1)), (
        "migration versions must start at 1 and be contiguous"
    )
    for _, description, statements in MIGRATIONS:
        assert description
        assert statements
        for sql in statements:
            assert "IF NOT EXISTS" in sql.upper(), (
                "migrations must be idempotent; use IF NOT EXISTS"
            )


def test_init_publish_schema_applies_all_pending_migrations():
    conn = FakeConnection()
    connector = FakeConnector(conn)

    applied = init_publish_schema(
        "mysql://user:pass@example.com/ascot", connector=connector
    )

    sqls = [sql for sql, _ in conn.executed]
    assert any("CREATE TABLE IF NOT EXISTS ascot_schema_version" in s for s in sqls)
    assert CREATE_RUNS_TABLE in sqls
    assert CREATE_TRIALS_TABLE in sqls

    version_inserts = [
        params for sql, params in conn.executed
        if "INSERT INTO ascot_schema_version" in sql
    ]
    assert [p[0] for p in version_inserts] == [m[0] for m in MIGRATIONS]
    assert applied == [(m[0], m[1]) for m in MIGRATIONS]
    assert conn.closed


def test_init_publish_schema_idempotent_when_up_to_date():
    conn = FakeConnection(applied_versions={m[0] for m in MIGRATIONS})
    connector = FakeConnector(conn)

    applied = init_publish_schema(
        "mysql://user:pass@example.com/ascot", connector=connector
    )

    assert applied == []
    table_creates = [
        sql for sql, _ in conn.executed
        if sql.lstrip().upper().startswith("CREATE TABLE")
        and "ascot_schema_version" not in sql
    ]
    assert table_creates == []


def test_publish_run_writes_run_and_trial_rows(tmp_path):
    run_dir = _write_report(tmp_path, _sample_report())
    conn = FakeConnection()
    connector = FakeConnector(conn)

    summary = publish_run(run_dir, "mysql://user:pass@example.com/ascot", connector=connector)

    assert summary == {
        "suite_name": "suite",
        "run_id": "run-001",
        "case_count": 2,
        "trial_count": 3,
    }
    assert conn.committed
    assert conn.closed

    run_inserts = [item for item in conn.executed if "INSERT INTO ascot_runs" in item[0]]
    assert len(run_inserts) == 1
    assert run_inserts[0][1][7] == 0.7  # score_pct
    # benchmark_model + grading_model trail the value tuple
    assert run_inserts[0][1][-2] is None
    assert run_inserts[0][1][-1] is None

    # case_results table no longer exists
    case_inserts = [
        item for item in conn.executed if "INSERT INTO ascot_case_results" in item[0]
    ]
    assert case_inserts == []

    trial_inserts = [
        item for item in conn.executed if "INSERT INTO ascot_trial_results" in item[0]
    ]
    assert len(trial_inserts) == 3
    assert [t[1][2] for t in trial_inserts] == [1, 2, 3]

    # First trial: token columns wired correctly.
    # (run_db_id, case_id, trial_num, score, max_score, score_pct, passed,
    #  turns, tokens_total, tokens_input, tokens_output, tokens_reasoning,
    #  tokens_cache_read, tokens_cache_write, duration_s, total_cost,
    #  exit_code, error)
    first = trial_inserts[0][1]
    assert first[1] == "pass-case"
    assert first[6] == 1   # passed
    assert first[8] == 40  # tokens_total
    assert first[9] == 25  # tokens_input
    assert first[10] == 15 # tokens_output
    assert first[12] == 5  # tokens_cache_read
    assert first[13] == 1  # tokens_cache_write
    assert first[16] == 0  # exit_code
    assert first[17] is None  # error

    # Third trial: failure -> passed=0, exit_code=1, error set.
    third = trial_inserts[2][1]
    assert third[6] == 0
    assert third[16] == 1
    assert third[17] == "timeout"

    # Idempotency: prior trial rows for this run are deleted before re-insert.
    delete_sqls = [
        sql for sql, _ in conn.executed
        if sql.startswith("DELETE FROM ascot_trial_results")
    ]
    assert len(delete_sqls) == 1


def test_publish_run_writes_model_columns(tmp_path):
    report = _sample_report()
    report["benchmark_model"] = "opencode/sonnet-4-6"
    report["grading_model"] = "opencode/haiku-4-5"
    run_dir = _write_report(tmp_path, report)
    conn = FakeConnection()
    connector = FakeConnector(conn)

    publish_run(run_dir, "mysql://user:pass@example.com/ascot", connector=connector)

    run_inserts = [item for item in conn.executed if "INSERT INTO ascot_runs" in item[0]]
    assert run_inserts[0][1][-2] == "opencode/sonnet-4-6"
    assert run_inserts[0][1][-1] == "opencode/haiku-4-5"


def test_publish_run_requires_report_json(tmp_path):
    run_dir = tmp_path / "run-001"
    run_dir.mkdir()
    with pytest.raises(PublishError, match="No report.json"):
        publish_run(
            run_dir,
            "mysql://user:pass@example.com/ascot",
            connector=FakeConnector(FakeConnection()),
        )


def test_publish_run_missing_table_suggests_init(tmp_path):
    run_dir = _write_report(tmp_path, _sample_report())
    conn = FakeConnection(raise_on_execute=MissingTableError())
    connector = FakeConnector(conn)

    with pytest.raises(PublishError, match="init-publish"):
        publish_run(run_dir, "mysql://user:pass@example.com/ascot", connector=connector)

    assert conn.rolled_back
    assert conn.closed
