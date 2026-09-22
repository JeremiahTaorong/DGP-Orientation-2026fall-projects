import httpx
import pytest

from text_service.client import command_request, exchange, read_text


def test_request() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/texts"
        assert request.headers["Authorization"] == "Bearer example"
        return httpx.Response(200, json={"data": []})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "GET", "/texts", "example") == (200, {"data": []})


def test_non_json_response() -> None:
    with httpx.Client(
        base_url="http://localhost",
        transport=httpx.MockTransport(lambda _: httpx.Response(502, text="bad gateway")),
    ) as client:
        assert exchange(client, "GET", "/ping") == (502, {"message": "bad gateway"})


@pytest.mark.parametrize(
    ("entered", "expected"),
    [
        ([":end"], ""),
        (["hello", "world", ":end"], "hello\nworld"),
        (["hello", "", ":end"], "hello\n"),
        (["\\:end", ":end"], ":end"),
        (["你好", "😀", ":end"], "你好\n😀"),
    ],
)
def test_read_text(monkeypatch: pytest.MonkeyPatch, entered: list[str], expected: str) -> None:
    values = iter(entered)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(values))
    assert read_text() == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ping", ("GET", "/ping", None)),
        ("logout", ("DELETE", "/sessions/current", None)),
        ("list", ("GET", "/texts", None)),
        ("delete-user", ("DELETE", "/users/me", None)),
        ("get", ("GET", "/texts/note", None)),
        ("delete", ("DELETE", "/texts/note", None)),
    ],
)
def test_command_request_without_body(
    monkeypatch: pytest.MonkeyPatch, command: str, expected: tuple[str, str, object]
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt="": "note")
    assert command_request(command) == expected


def test_text_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    values = iter(["note", "line one", "line two", ":end"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(values))
    assert command_request("put") == (
        "PUT",
        "/texts/note",
        {"text": "line one\nline two"},
    )

    values = iter(["hello", ":end"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(values))
    assert command_request("echo") == ("POST", "/echo", {"text": "hello"})


def test_account_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt="": "alice")
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": "password1")
    assert command_request("register") == (
        "POST",
        "/users",
        {"username": "alice", "password": "password1"},
    )


def test_unknown_command() -> None:
    with pytest.raises(ValueError, match="Unknown command"):
        command_request("missing")
