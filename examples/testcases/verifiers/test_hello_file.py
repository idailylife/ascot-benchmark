import os


def test_hello_file_exists():
    assert os.path.exists("hello.txt"), "hello.txt not created"


def test_hello_file_content():
    with open("hello.txt") as f:
        content = f.read()
    assert "Hello, World!" in content, f"unexpected content: {content!r}"
