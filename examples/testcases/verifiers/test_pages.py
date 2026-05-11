"""Per-page existence checks for pdf-read-cases-std.yaml.

Each case references one test via pytest nodeid:
    test_script: ./verifiers/test_pages.py::test_page1_md_exists
"""

import os


def test_page1_md_exists():
    assert os.path.exists("page1.md"), "page1.md not created"


def test_page2_md_exists():
    assert os.path.exists("page2.md"), "page2.md not created"


def test_page3_md_exists():
    assert os.path.exists("page3.md"), "page3.md not created"


def test_page8_md_exists():
    assert os.path.exists("page8.md"), "page8.md not created"


def test_page12_md_exists():
    assert os.path.exists("page12.md"), "page12.md not created"
