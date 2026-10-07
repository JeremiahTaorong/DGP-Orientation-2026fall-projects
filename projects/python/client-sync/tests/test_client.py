import httpx
import pytest

from text_service.client import command_request, exchange, main, read_text


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


def test_interactive_token_lifecycle(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    requests: list[tuple[str, str, str | None]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, request.headers.get("Authorization")))
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"data": {"token": "new-token", "expires_in": 300}})
        if request.url.path == "/texts":
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(200, json={"data": None})

    client = httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(respond))
    entered = iter(["login", "alice", "list", "logout", "q"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(entered))
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": "password1")
    monkeypatch.setattr("text_service.client.httpx.Client", lambda **_kwargs: client)
    monkeypatch.setattr("sys.argv", ["rm-client"])

    main()

    assert requests == [
        ("POST", "/sessions", None),
        ("GET", "/texts", "Bearer new-token"),
        ("DELETE", "/sessions/current", None),
    ]
    assert "Please log in again." in capsys.readouterr().out


def test_interactive_network_failure_recovers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"data": "pong"})

    client = httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(respond))
    entered = iter(["ping", "ping", "q"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(entered))
    monkeypatch.setattr("text_service.client.httpx.Client", lambda **_kwargs: client)
    monkeypatch.setattr("sys.argv", ["rm-client"])

    main()

    output = capsys.readouterr().out
    assert attempts == 2
    assert "Request failed: connection refused" in output
    assert "200 {'data': 'pong'}" in output
