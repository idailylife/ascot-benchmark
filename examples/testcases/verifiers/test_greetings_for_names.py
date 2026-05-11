import os


def test_greetings_file_exists():
    assert os.path.exists("greetings.txt"), "greetings.txt not created"


def _read():
    with open("greetings.txt") as f:
        return f.read()


def test_greetings_contains_alice():
    assert "Alice" in _read()


def test_greetings_contains_bob():
    assert "Bob" in _read()


def test_greetings_contains_charlie():
    assert "Charlie" in _read()
