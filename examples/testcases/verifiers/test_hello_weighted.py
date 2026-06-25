import os

import pytest


# Demonstrates per-test weighting with @pytest.mark.score(N).
# Total possible: 1 (exists) + 4 (content) = 5 points.

@pytest.mark.score(1)
def test_hello_file_exists():
    assert os.path.exists("hello.txt"), "hello.txt not created"


@pytest.mark.score(4)
def test_hello_file_content():
    with open("hello.txt") as f:
        content = f.read()
    assert "Hello, World!" in content, f"unexpected content: {content!r}"
