import json
import os


def test_greeting_json_exists():
    assert os.path.exists("greeting.json"), "greeting.json not created"


def test_greeting_json_is_valid():
    with open("greeting.json") as f:
        json.load(f)


def test_greeting_json_message():
    with open("greeting.json") as f:
        data = json.load(f)
    assert data.get("message") == "Hello, World!", f"unexpected message: {data!r}"


def test_greeting_json_language():
    with open("greeting.json") as f:
        data = json.load(f)
    assert data.get("language") == "en", f"unexpected language: {data!r}"
