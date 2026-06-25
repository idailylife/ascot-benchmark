"""pytest plugin: lets test_script authors weight a test with @pytest.mark.score(N).

Loaded by verifiers.run_test_script via `-p ascot._score_plugin`. Registers the
`score` marker (so no PytestUnknownMarkWarning) and copies its argument into the
testcase's junit user_properties, which _parse_junit reads back as the point weight.
"""

from __future__ import annotations


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "score(n): point weight for this test in Ascot grading (default 1)"
    )


def pytest_collection_modifyitems(items):
    for item in items:
        marker = item.get_closest_marker("score")
        if marker and marker.args:
            item.user_properties.append(("score", marker.args[0]))
