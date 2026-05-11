import os


def test_page_count_file_exists():
    assert os.path.exists("page_count.txt"), "page_count.txt not created"


def test_page_count_value():
    with open("page_count.txt") as f:
        content = f.read().strip()
    assert "42" in content, f"expected '42' in page_count.txt, got: {content!r}"
