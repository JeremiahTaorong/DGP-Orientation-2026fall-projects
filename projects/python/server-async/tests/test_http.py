import asyncio
import threading
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx2 import ASGITransport, AsyncClient

from text_service.server import create_app
from text_service.service import Service

pytestmark = pytest.mark.anyio


class FakeClock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


class BlockingService(Service):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.resume = threading.Event()

    def handle(
        self, method: str, path: str, body: object, authorization: str
    ) -> tuple[int, dict[str, Any]]:
        if method == "POST" and path == "/users":
            self.started.set()
            assert self.resume.wait(timeout=5)
            return 201, {"data": {"username": "slow"}}
        return super().handle(method, path, body, authorization)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as test_client,
    ):
        yield test_client


async def register_and_login(
    client: AsyncClient, username: str = "alice", expected_ttl: int = 300
) -> str:
    account = {"username": username, "password": "password1"}
    assert (await client.post("/users", json=account)).status_code == 201
    response = await client.post("/sessions", json=account)
    assert response.status_code == 200
    assert response.json()["data"]["expires_in"] == expected_ttl
    return response.json()["data"]["token"]


async def test_app_uses_supplied_service() -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    app = create_app(service)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        assert (await client.post("/sessions", json=account)).status_code == 200


async def test_complete_http_flow(client: AsyncClient) -> None:
    assert (await client.get("/ping")).json() == {"data": "pong"}
    assert (await client.post("/echo", json={"text": "你好\nRM"})).json() == {"data": "你好\nRM"}
    token = await register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    assert (
        await client.put("/texts/zeta", json={"text": "last"}, headers=headers)
    ).status_code == 200
    assert (await client.put("/texts/alpha", json={"text": ""}, headers=headers)).status_code == 200
    assert (await client.get("/texts", headers=headers)).json() == {"data": ["alpha", "zeta"]}
    assert (await client.get("/texts/alpha", headers=headers)).json() == {"data": ""}
    assert (await client.delete("/texts/alpha", headers=headers)).json() == {"data": None}
    assert (await client.get("/texts/alpha", headers=headers)).status_code == 404
    assert (await client.delete("/users/me", headers=headers)).json() == {"data": None}
    assert (await client.get("/texts", headers=headers)).status_code == 401


@pytest.mark.parametrize("body", [b"not JSON", b"\xff", b"NaN"])
async def test_invalid_json(client: AsyncClient, body: bytes) -> None:
    assert (await client.post("/users", content=body)).status_code == 400


async def test_body_and_text_limits(client: AsyncClient) -> None:
    exact = b"{}" + b" " * (524288 - 2)
    assert (await client.post("/users", content=exact)).status_code == 400
    assert (await client.post("/users", content=exact + b" ")).status_code == 413
    assert (await client.post("/echo", json={"text": "a" * 65_536})).status_code == 200
    assert (await client.post("/echo", json={"text": "a" * 65_537})).status_code == 413


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
async def test_routing(client: AsyncClient, method: str, path: str, status: int) -> None:
    assert (await client.request(method, path)).status_code == status


async def test_query_string_is_not_part_of_route(client: AsyncClient) -> None:
    assert (await client.get("/ping?test=1")).json() == {"data": "pong"}


async def test_authentication_and_user_isolation(client: AsyncClient) -> None:
    alice_token = await register_and_login(client, "alice")
    bob_token = await register_and_login(client, "bob")
    alice = {"Authorization": f"Bearer {alice_token}"}
    bob = {"Authorization": f"Bearer {bob_token}"}

    assert (
        await client.put("/texts/note", json={"text": "alice"}, headers=alice)
    ).status_code == 200
    assert (await client.put("/texts/note", json={"text": "bob"}, headers=bob)).status_code == 200
    assert (await client.get("/texts/note", headers=alice)).json() == {"data": "alice"}
    assert (await client.get("/texts/note", headers=bob)).json() == {"data": "bob"}
    assert (await client.get("/texts")).status_code == 401


async def test_token_expiration_over_http() -> None:
    clock = FakeClock()
    service = Service(token_ttl_seconds=2, clock=clock)
    app = create_app(service)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        token = await register_and_login(client, expected_ttl=2)
        headers = {"Authorization": f"Bearer {token}"}
        clock.now = 11.999
        assert (await client.get("/texts", headers=headers)).status_code == 200
        clock.now = 12.0
        assert (await client.get("/texts", headers=headers)).status_code == 401


async def test_request_field_validation(client: AsyncClient) -> None:
    assert (await client.post("/echo", json={})).status_code == 400
    assert (await client.post("/echo", json={"text": "", "extra": True})).status_code == 400
    assert (await client.post("/users", json={"username": "alice"})).status_code == 400


async def test_blocking_service_work_does_not_block_event_loop() -> None:
    service = BlockingService()
    app = create_app(service)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        slow_request = asyncio.create_task(
            client.post("/users", json={"username": "slow", "password": "password1"})
        )
        assert await asyncio.to_thread(service.started.wait, 2)
        assert (await client.get("/ping")).status_code == 200
        service.resume.set()
        assert (await slow_request).status_code == 201
