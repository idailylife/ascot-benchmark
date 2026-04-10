"""Tests for ascot.workspace."""

from pathlib import Path

import pytest

from ascot.models import TestCase
from ascot.workspace import setup_workspace, preserve_workspace, cleanup_workspace


@pytest.fixture
def suite_dir(tmp_path):
    """A minimal suite directory with opencode.json."""
    sd = tmp_path / "suite"
    sd.mkdir()
    (sd / "opencode.json").write_text("{}")
    return sd


@pytest.fixture
def fixtures_dir(tmp_path):
    """A directory containing fixture files to copy into workspace."""
    fd = tmp_path / "fixtures" / "input"
    fd.mkdir(parents=True)
    (fd / "data.txt").write_text("hello")
    (fd / "binary.bin").write_bytes(b"\x00\x01\x02")
    return fd


class TestSetupWorkspace:
    def test_creates_opencode_dir(self, suite_dir):
        tc = TestCase(id="c1", prompt="x")
        ws = setup_workspace(suite_dir, tc)
        try:
            assert (ws / ".opencode").is_dir()
            assert (ws / ".opencode" / "opencode.json").exists()
        finally:
            cleanup_workspace(ws)

    def test_copies_workspace_files_absolute(self, suite_dir, fixtures_dir):
        tc = TestCase(id="c1", prompt="x", workspace_files_from=str(fixtures_dir))
        ws = setup_workspace(suite_dir, tc)
        try:
            copied = ws / "input"
            assert copied.is_dir()
            assert (copied / "data.txt").read_text() == "hello"
            assert (copied / "binary.bin").read_bytes() == b"\x00\x01\x02"
        finally:
            cleanup_workspace(ws)

    def test_copies_workspace_files_relative(self, suite_dir, fixtures_dir):
        tc = TestCase(id="c1", prompt="x", workspace_files_from="input")
        testcases_dir = fixtures_dir.parent  # "fixtures/"
        ws = setup_workspace(suite_dir, tc, testcases_dir=testcases_dir)
        try:
            assert (ws / "input" / "data.txt").exists()
        finally:
            cleanup_workspace(ws)

    def test_no_workspace_files(self, suite_dir):
        tc = TestCase(id="c1", prompt="x")
        ws = setup_workspace(suite_dir, tc)
        try:
            entries = [e.name for e in ws.iterdir()]
            assert entries == [".opencode"]
        finally:
            cleanup_workspace(ws)


class TestPreserveWorkspace:
    def test_excludes_opencode_and_venv(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".opencode").mkdir()
        (ws / ".opencode" / "config.json").write_text("{}")
        (ws / ".venv").mkdir()
        (ws / ".venv" / "bin").mkdir()
        (ws / "output.txt").write_text("result")

        dest = tmp_path / "preserved"
        preserve_workspace(ws, dest)
        assert (dest / "output.txt").exists()
        assert not (dest / ".opencode").exists()
        assert not (dest / ".venv").exists()


class TestCleanupWorkspace:
    def test_removes_directory(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "file.txt").write_text("x")
        cleanup_workspace(ws)
        assert not ws.exists()

    def test_nonexistent_is_noop(self, tmp_path):
        cleanup_workspace(tmp_path / "nonexistent")
