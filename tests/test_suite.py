"""Tests for ascot.suite."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from ascot.suite import resolve_suite, load_test_suite


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


class TestResolveSuite:
    def test_with_opencode_subdir(self, tmp_dir):
        (tmp_dir / ".opencode").mkdir()
        result = resolve_suite(tmp_dir)
        assert result == (tmp_dir / ".opencode").resolve()

    def test_with_opencode_json(self, tmp_dir):
        (tmp_dir / "opencode.json").touch()
        result = resolve_suite(tmp_dir)
        assert result == tmp_dir.resolve()

    def test_with_opencode_jsonc(self, tmp_dir):
        (tmp_dir / "opencode.jsonc").touch()
        result = resolve_suite(tmp_dir)
        assert result == tmp_dir.resolve()

    def test_with_skills_dir(self, tmp_dir):
        (tmp_dir / "skills").mkdir()
        result = resolve_suite(tmp_dir)
        assert result == tmp_dir.resolve()

    def test_with_commands_dir(self, tmp_dir):
        (tmp_dir / "commands").mkdir()
        result = resolve_suite(tmp_dir)
        assert result == tmp_dir.resolve()

    def test_nonexistent_raises(self, tmp_dir):
        with pytest.raises(ValueError, match="does not exist"):
            resolve_suite(tmp_dir / "nope")

    def test_invalid_dir_raises(self, tmp_dir):
        with pytest.raises(ValueError, match="Invalid suite directory"):
            resolve_suite(tmp_dir)


class TestLoadTestSuite:
    def _write_yaml(self, path, data):
        with open(path, "w") as f:
            yaml.dump(data, f)

    def test_single_file(self, tmp_dir):
        data = {
            "name": "my-suite",
            "test_cases": [
                {"id": "c1", "prompt": "hello", "expectations": [{"desc": "works"}]},
            ],
        }
        p = tmp_dir / "tests.yaml"
        self._write_yaml(p, data)
        suite = load_test_suite(p)
        assert suite.name == "my-suite"
        assert len(suite.test_cases) == 1
        assert suite.test_cases[0].id == "c1"
        assert suite.test_cases[0].expectations[0].desc == "works"
        assert suite.test_cases[0].expectations[0].score == 1

    def test_defaults_inherited(self, tmp_dir):
        data = {
            "name": "s",
            "default_timeout_s": 300,
            "default_model": "gpt-4",
            "default_workspace_files_from": "../fixtures",
            "test_cases": [
                {"id": "c1", "prompt": "hi"},
                {"id": "c2", "prompt": "hi", "timeout_s": 60, "model": "gpt-3", "workspace_files_from": "../other"},
            ],
        }
        p = tmp_dir / "tests.yaml"
        self._write_yaml(p, data)
        suite = load_test_suite(p)
        # c1 inherits defaults
        assert suite.test_cases[0].timeout_s == 300
        assert suite.test_cases[0].model == "gpt-4"
        assert suite.test_cases[0].workspace_files_from == "../fixtures"
        # c2 overrides
        assert suite.test_cases[1].timeout_s == 60
        assert suite.test_cases[1].model == "gpt-3"
        assert suite.test_cases[1].workspace_files_from == "../other"

    def test_directory_single_file(self, tmp_dir):
        data = {"name": "dir-suite", "test_cases": [{"id": "c1", "prompt": "x"}]}
        self._write_yaml(tmp_dir / "cases.yaml", data)
        suite = load_test_suite(tmp_dir)
        assert suite.name == "dir-suite"

    def test_directory_multiple_files(self, tmp_dir):
        self._write_yaml(tmp_dir / "a.yaml", {
            "name": "suite-a",
            "test_cases": [{"id": "a1", "prompt": "x"}],
        })
        self._write_yaml(tmp_dir / "b.yaml", {
            "name": "suite-b",
            "test_cases": [{"id": "b1", "prompt": "y"}],
        })
        suite = load_test_suite(tmp_dir)
        assert len(suite.test_cases) == 2
        ids = {tc.id for tc in suite.test_cases}
        assert ids == {"a1", "b1"}

    def test_empty_directory_raises(self, tmp_dir):
        with pytest.raises(ValueError, match="No YAML files"):
            load_test_suite(tmp_dir)

    def test_nonexistent_path_raises(self, tmp_dir):
        with pytest.raises(ValueError, match="does not exist"):
            load_test_suite(tmp_dir / "nope")

    def test_expectation_scores(self, tmp_dir):
        data = {
            "name": "s",
            "test_cases": [{
                "id": "c1", "prompt": "x",
                "expectations": [
                    {"desc": "a", "score": 5},
                    {"desc": "b"},  # default score 1
                ],
            }],
        }
        self._write_yaml(tmp_dir / "t.yaml", data)
        suite = load_test_suite(tmp_dir / "t.yaml")
        assert suite.test_cases[0].expectations[0].score == 5
        assert suite.test_cases[0].expectations[1].score == 1

    def test_grading_model(self, tmp_dir):
        data = {
            "name": "s",
            "default_model": "gpt-4",
            "grading_model": "gpt-3.5",
            "test_cases": [{"id": "c1", "prompt": "hi"}],
        }
        self._write_yaml(tmp_dir / "t.yaml", data)
        suite = load_test_suite(tmp_dir / "t.yaml")
        assert suite.default_model == "gpt-4"
        assert suite.grading_model == "gpt-3.5"

    def test_grading_model_defaults_to_none(self, tmp_dir):
        data = {
            "name": "s",
            "default_model": "gpt-4",
            "test_cases": [{"id": "c1", "prompt": "hi"}],
        }
        self._write_yaml(tmp_dir / "t.yaml", data)
        suite = load_test_suite(tmp_dir / "t.yaml")
        assert suite.grading_model is None

    def test_tags_and_agent(self, tmp_dir):
        data = {
            "name": "s",
            "test_cases": [{
                "id": "c1", "prompt": "x",
                "tags": ["fast", "smoke"],
                "agent": "my-agent",
            }],
        }
        self._write_yaml(tmp_dir / "t.yaml", data)
        suite = load_test_suite(tmp_dir / "t.yaml")
        assert suite.test_cases[0].tags == ["fast", "smoke"]
        assert suite.test_cases[0].agent == "my-agent"
