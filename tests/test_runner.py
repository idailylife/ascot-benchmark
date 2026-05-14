"""Tests for ascot.runner helpers."""

import json

from ascot.runner import _preserve_workspace_best_effort, build_permission


def test_build_permission_reads_json_with_schema_url(tmp_path):
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    (suite_dir / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "permission": {"bash": "deny"},
    }))

    permission = build_permission(suite_dir)

    assert permission["*"] == "allow"
    assert permission["bash"] == "deny"


def test_build_permission_reads_jsonc_and_nested_permission(tmp_path):
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    (suite_dir / "opencode.jsonc").write_text(
        """
        {
          // allow reads from a fixture path outside the workspace
          "$schema": "https://opencode.ai/config.json",
          "permission": {
            "external_directory": {
              "/tmp/fixtures/**": "allow",
            },
          },
        }
        """
    )

    permission = build_permission(suite_dir)

    assert permission["question"] == "deny"
    assert permission["external_directory"] == {"/tmp/fixtures/**": "allow"}


def test_preserve_workspace_best_effort_returns_duration(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "out.txt").write_text("ok")
    dest = tmp_path / "dest"

    duration = _preserve_workspace_best_effort(ws, dest)

    assert duration is not None
    assert (dest / "out.txt").read_text() == "ok"


def test_preserve_workspace_best_effort_swallows_errors(monkeypatch, tmp_path):
    def boom(ws, dest):
        raise OSError("disk full")

    monkeypatch.setattr("ascot.runner.preserve_workspace", boom)

    duration = _preserve_workspace_best_effort(tmp_path / "ws", tmp_path / "dest")

    assert duration is None
