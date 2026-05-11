"""Tests for ascot.verifiers.run_test_script."""

from pathlib import Path

from ascot.verifiers import run_test_script


def _write(p: Path, content: str) -> Path:
    p.write_text(content)
    return p


class TestRunTestScript:
    def test_all_pass(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        script = _write(tmp_path / "test_x.py", (
            "def test_a():\n    assert 1 == 1\n"
            "def test_b():\n    assert 2 == 2\n"
        ))

        results = run_test_script(ws, script)

        assert len(results) == 2
        assert {r.desc for r in results} == {"test_a", "test_b"}
        for r in results:
            assert r.score == 1
            assert r.earned == 1
            assert r.reasoning == ""

    def test_mixed_pass_fail(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        script = _write(tmp_path / "test_x.py", (
            "def test_pass():\n    assert True\n"
            "def test_fail():\n    assert False, 'expected to fail'\n"
        ))

        results = run_test_script(ws, script)

        by_name = {r.desc: r for r in results}
        assert by_name["test_pass"].earned == 1
        assert by_name["test_fail"].earned == 0
        assert "expected to fail" in by_name["test_fail"].reasoning or \
               "AssertionError" in by_name["test_fail"].reasoning

    def test_runs_in_workspace_cwd(self, tmp_path):
        """The script's relative file ops must resolve against workspace_dir."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "page_count.txt").write_text("42")
        script = _write(tmp_path / "test_x.py", (
            "def test_value():\n"
            "    with open('page_count.txt') as f:\n"
            "        assert f.read().strip() == '42'\n"
        ))

        results = run_test_script(ws, script)

        assert len(results) == 1
        assert results[0].earned == 1

    def test_skipped_tests_excluded(self, tmp_path):
        """Skipped tests must not count toward score (don't appear in results)."""
        ws = tmp_path / "ws"
        ws.mkdir()
        script = _write(tmp_path / "test_x.py", (
            "import pytest\n"
            "def test_real():\n    assert True\n"
            "@pytest.mark.skip('not now')\n"
            "def test_skip():\n    assert True\n"
        ))

        results = run_test_script(ws, script)

        assert len(results) == 1
        assert results[0].desc == "test_real"

    def test_missing_script(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        results = run_test_script(ws, tmp_path / "does-not-exist.py")

        assert len(results) == 1
        assert results[0].earned == 0
        assert results[0].score == 1
        assert "file not found" in results[0].reasoning

    def test_collection_error(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        # Syntax error → pytest collection failure
        script = _write(tmp_path / "test_x.py", "def test_a():\n    !!! syntax error\n")

        results = run_test_script(ws, script)

        # Either junit captures the error as a single failure, or we surface
        # a generic "no tests collected"; both forms must produce a single
        # failed ExpectationResult.
        assert len(results) == 1
        assert results[0].earned == 0
        assert results[0].reasoning != ""

    def test_no_tests_in_file(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        script = _write(tmp_path / "test_x.py", "x = 1\n")

        results = run_test_script(ws, script)

        assert len(results) == 1
        assert results[0].earned == 0
        assert "no tests collected" in results[0].reasoning

    def test_timeout(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        script = _write(tmp_path / "test_x.py", (
            "import time\n"
            "def test_slow():\n    time.sleep(5)\n"
        ))

        results = run_test_script(ws, script, timeout_s=1.0)

        assert len(results) == 1
        assert results[0].earned == 0
        assert "timed out" in results[0].reasoning

    def test_nodeid_selects_single_test(self, tmp_path):
        """Pytest nodeid form `file.py::test_name` runs only that one test."""
        ws = tmp_path / "ws"
        ws.mkdir()
        script_file = _write(tmp_path / "test_x.py", (
            "def test_a():\n    assert True\n"
            "def test_b():\n    assert False\n"
            "def test_c():\n    assert True\n"
        ))
        # Pass a Path whose name embeds ::test_a — verifier should treat the
        # `file.py` prefix as the file to check for existence and pass the
        # whole nodeid to pytest.
        nodeid = type(script_file)(f"{script_file}::test_a")

        results = run_test_script(ws, nodeid)

        assert len(results) == 1
        assert results[0].desc == "test_a"
        assert results[0].earned == 1

    def test_nodeid_unknown_test_name(self, tmp_path):
        """Unknown nodeid → 'no tests collected' single failure."""
        ws = tmp_path / "ws"
        ws.mkdir()
        script_file = _write(tmp_path / "test_x.py",
                             "def test_a():\n    assert True\n")
        nodeid = type(script_file)(f"{script_file}::test_does_not_exist")

        results = run_test_script(ws, nodeid)

        assert len(results) == 1
        assert results[0].earned == 0
        # pytest emits no junit for a fully unmatched nodeid → caught by
        # the "no junit output" branch with the pytest stderr in reasoning.
        assert results[0].reasoning != ""

    def test_nodeid_missing_file(self, tmp_path):
        """Nodeid on a non-existent file → 'file not found' on file part."""
        ws = tmp_path / "ws"
        ws.mkdir()
        nodeid = tmp_path / "does-not-exist.py::test_a"

        results = run_test_script(ws, nodeid)

        assert len(results) == 1
        assert results[0].earned == 0
        assert "file not found" in results[0].reasoning
        # file part in reasoning, no `::` suffix
        assert "::" not in results[0].reasoning
