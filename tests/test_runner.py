"""Tests for ascot.runner helpers."""

import json

from ascot.runner import build_permission


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
