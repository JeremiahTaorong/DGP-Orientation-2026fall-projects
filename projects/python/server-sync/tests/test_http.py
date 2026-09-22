from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from text_service.server import create_app
from text_service.service import Service


class FakeClock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def client() -> Generator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def register_and_login(client: TestClient, username: str = "alice", expected_ttl: int = 300) -> str:
    account = {"username": username, "password": "password1"}
    assert client.post("/users", json=account).status_code == 201
    response = client.post("/sessions", json=account)
    assert response.status_code == 200
    assert response.json()["data"]["expires_in"] == expected_ttl
    return response.json()["data"]["token"]


def test_app_uses_supplied_service() -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    with TestClient(create_app(service)) as configured, TestClient(create_app()) as fresh:
        assert configured.post("/sessions", json=account).status_code == 200
        assert fresh.post("/sessions", json=account).status_code == 401


def test_complete_http_flow(client: TestClient) -> None:
    assert client.get("/ping").json() == {"data": "pong"}
    assert client.post("/echo", json={"text": "你好\nRM"}).json() == {"data": "你好\nRM"}
    token = register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.put("/texts/zeta", json={"text": "last"}, headers=headers).status_code == 200
    assert client.put("/texts/alpha", json={"text": ""}, headers=headers).status_code == 200
    assert client.get("/texts", headers=headers).json() == {"data": ["alpha", "zeta"]}
    assert client.get("/texts/alpha", headers=headers).json() == {"data": ""}
    assert client.delete("/texts/alpha", headers=headers).json() == {"data": None}
    assert client.get("/texts/alpha", headers=headers).status_code == 404
    assert client.delete("/users/me", headers=headers).json() == {"data": None}
    assert client.get("/texts", headers=headers).status_code == 401


@pytest.mark.parametrize("body", [b"not JSON", b"\xff", b"NaN"])
def test_invalid_json(client: TestClient, body: bytes) -> None:
    assert client.post("/users", content=body).status_code == 400


def test_body_and_text_limits(client: TestClient) -> None:
    exact = b"{}" + b" " * (524288 - 2)
    assert client.post("/users", content=exact).status_code == 400
    assert client.post("/users", content=exact + b" ").status_code == 413
    assert client.post("/echo", json={"text": "a" * 65_536}).status_code == 200
    assert client.post("/echo", json={"text": "a" * 65_537}).status_code == 413


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/missing", 404),
        ("GET", "/echo", 405),
        ("PATCH", "/ping", 405),
        ("PATCH", "/texts/note", 405),
        ("GET", "/texts/", 400),
        ("GET", "/texts/a/b", 400),
    ],
)
def test_routing(client: TestClient, method: str, path: str, status: int) -> None:
    assert client.request(method, path).status_code == status


def test_query_string_is_not_part_of_route(client: TestClient) -> None:
    assert client.get("/ping?test=1").json() == {"data": "pong"}


def test_authentication_and_user_isolation(client: TestClient) -> None:
    alice_token = register_and_login(client, "alice")
    bob_token = register_and_login(client, "bob")
    alice = {"Authorization": f"Bearer {alice_token}"}
    bob = {"Authorization": f"Bearer {bob_token}"}

    assert client.put("/texts/note", json={"text": "alice"}, headers=alice).status_code == 200
    assert client.put("/texts/note", json={"text": "bob"}, headers=bob).status_code == 200
    assert client.get("/texts/note", headers=alice).json() == {"data": "alice"}
    assert client.get("/texts/note", headers=bob).json() == {"data": "bob"}
    assert client.get("/texts").status_code == 401


def test_token_expiration_over_http() -> None:
    clock = FakeClock()
    service = Service(token_ttl_seconds=2, clock=clock)
    with TestClient(create_app(service)) as client:
        token = register_and_login(client, expected_ttl=2)
        headers = {"Authorization": f"Bearer {token}"}
        clock.now = 11.999
        assert client.get("/texts", headers=headers).status_code == 200
        clock.now = 12.0
        assert client.get("/texts", headers=headers).status_code == 401


def test_request_field_validation(client: TestClient) -> None:
    assert client.post("/echo", json={}).status_code == 400
    assert client.post("/echo", json={"text": "", "extra": True}).status_code == 400
    assert client.post("/users", json={"username": "alice"}).status_code == 400
