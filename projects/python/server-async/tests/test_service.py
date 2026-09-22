import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from text_service.service import Service, route_error

ACCOUNT = {"username": "alice", "password": "password1"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def register_and_login(service: Service, account: dict[str, str] = ACCOUNT) -> str:
    assert service.handle("POST", "/users", account, "")[0] == 201
    status, result = service.handle("POST", "/sessions", account, "")
    assert status == 200
    return result["data"]["token"]


def test_account_and_token_lifecycle() -> None:
    service = Service()
    assert service.handle("GET", "/ping", None, "") == (200, {"data": "pong"})
    assert service.handle("POST", "/users", ACCOUNT, "")[0] == 201
    assert service.handle("POST", "/users", ACCOUNT, "")[0] == 409
    assert service.handle("POST", "/sessions", {**ACCOUNT, "password": "incorrect"}, "")[0] == 401

    status, result = service.handle("POST", "/sessions", ACCOUNT, "")
    first_token = result["data"]["token"]
    assert status == 200
    assert result["data"]["expires_in"] == 300

    next_token = service.handle("POST", "/sessions", ACCOUNT, "")[1]["data"]["token"]
    assert first_token != next_token
    assert service.handle("GET", "/texts", None, f"Bearer {first_token}")[0] == 401
    assert service.handle("GET", "/texts", None, f"Bearer {next_token}") == (200, {"data": []})
    assert service.handle("DELETE", "/sessions/current", None, f"Bearer {next_token}")[0] == 200
    assert service.handle("GET", "/texts", None, f"Bearer {next_token}")[0] == 401


@pytest.mark.parametrize("text", ["", "hello\nworld", "你好", "😀"])
def test_echo(text: str) -> None:
    assert Service().handle("POST", "/echo", {"text": text}, "") == (200, {"data": text})


@pytest.mark.parametrize(
    "body",
    [None, [], {}, {"text": 42}, {"text": "ok", "extra": True}, {"text": "\ud800"}],
)
def test_invalid_echo_body(body: object) -> None:
    assert Service().handle("POST", "/echo", body, "")[0] == 400


def test_text_size_is_measured_in_utf8_bytes() -> None:
    service = Service()
    assert service.handle("POST", "/echo", {"text": "你" * 21_845 + "a"}, "")[0] == 200
    assert service.handle("POST", "/echo", {"text": "你" * 21_846}, "")[0] == 413


def test_text_crud_sorting_and_user_isolation() -> None:
    service = Service()
    alice_token = register_and_login(service)
    bob = {"username": "bob", "password": "password2"}
    bob_token = register_and_login(service, bob)

    assert service.handle("PUT", "/texts/zeta", {"text": "alice z"}, f"Bearer {alice_token}") == (
        200,
        {"data": None},
    )
    assert service.handle("PUT", "/texts/alpha", {"text": ""}, f"Bearer {alice_token}")[0] == 200
    assert (
        service.handle("PUT", "/texts/zeta", {"text": "new value"}, f"Bearer {alice_token}")[0]
        == 200
    )
    assert service.handle("PUT", "/texts/zeta", {"text": "bob z"}, f"Bearer {bob_token}")[0] == 200

    assert service.handle("GET", "/texts", None, f"Bearer {alice_token}") == (
        200,
        {"data": ["alpha", "zeta"]},
    )
    assert service.handle("GET", "/texts/zeta", None, f"Bearer {alice_token}") == (
        200,
        {"data": "new value"},
    )
    assert service.handle("GET", "/texts/zeta", None, f"Bearer {bob_token}") == (
        200,
        {"data": "bob z"},
    )
    assert service.handle("DELETE", "/texts/zeta", None, f"Bearer {alice_token}") == (
        200,
        {"data": None},
    )
    assert service.handle("GET", "/texts/zeta", None, f"Bearer {alice_token}")[0] == 404
    assert service.handle("DELETE", "/texts/zeta", None, f"Bearer {alice_token}")[0] == 404
    assert service.handle("GET", "/texts/zeta", None, f"Bearer {bob_token}")[0] == 200


def test_delete_user_clears_identity_and_texts() -> None:
    service = Service()
    token = register_and_login(service)
    assert service.handle("PUT", "/texts/note", {"text": "old"}, f"Bearer {token}")[0] == 200
    assert service.handle("DELETE", "/users/me", None, f"Bearer {token}") == (200, {"data": None})
    assert service.handle("GET", "/texts", None, f"Bearer {token}")[0] == 401

    new_token = register_and_login(service)
    assert service.handle("GET", "/texts", None, f"Bearer {new_token}") == (200, {"data": []})


def test_token_expiration_uses_fixed_deadline() -> None:
    clock = FakeClock()
    service = Service(token_ttl_seconds=10, clock=clock)
    token = register_and_login(service)

    clock.now = 109.999
    assert service.handle("GET", "/texts", None, f"Bearer {token}")[0] == 200
    clock.now = 110.0
    assert service.handle("GET", "/texts", None, f"Bearer {token}")[0] == 401
    assert service.handle("DELETE", "/sessions/current", None, f"Bearer {token}")[0] == 401


@pytest.mark.parametrize("ttl", [0, -1, True, 1.5])
def test_invalid_token_ttl(ttl: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        Service(ttl)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"username": True, "password": "password1"},
        {"username": "a/b", "password": "password1"},
        {"username": "alice", "password": "short"},
        {"username": "alice", "password": "\ud800" * 8},
    ],
)
def test_account_validation(body: object) -> None:
    assert Service().handle("POST", "/users", body, "")[0] == 400


@pytest.mark.parametrize("name", ["", "a/b", "x" * 65, "你好"])
def test_text_name_validation(name: str) -> None:
    assert Service().handle("GET", f"/texts/{name}", None, "")[0] == 400


def test_text_body_validation() -> None:
    service = Service()
    token = register_and_login(service)
    authorization = f"Bearer {token}"
    for body in (None, {}, {"text": 3}, {"text": "ok", "extra": 1}, {"text": "\ud800"}):
        assert service.handle("PUT", "/texts/note", body, authorization)[0] == 400
    assert service.handle("PUT", "/texts/note", {"text": "😀" * 16_385}, authorization)[0] == 413


def test_routing_distinguishes_unknown_paths_and_methods() -> None:
    assert route_error("GET", "/missing") == 404
    assert route_error("PATCH", "/ping") == 405
    assert route_error("PATCH", "/texts/note") == 405
    assert route_error("GET", "/texts/note") is None


def test_concurrent_registration() -> None:
    service = Service()
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(
            pool.map(lambda _: service.handle("POST", "/users", ACCOUNT, "")[0], range(4))
        )
    assert sorted(statuses) == [201, 409, 409, 409]


def test_old_login_cannot_attach_to_reregistered_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Service()
    old_token = register_and_login(service)
    old_salt = service.users["alice"].salt
    hashing_started = threading.Event()
    resume_hashing = threading.Event()
    original_hash = hashlib.pbkdf2_hmac

    def controlled_hash(
        hash_name: str,
        password: bytes,
        salt: bytes,
        iterations: int,
    ) -> bytes:
        if salt == old_salt and not hashing_started.is_set():
            hashing_started.set()
            assert resume_hashing.wait(timeout=5)
        return original_hash(hash_name, password, salt, iterations)

    monkeypatch.setattr(hashlib, "pbkdf2_hmac", controlled_hash)
    with ThreadPoolExecutor(max_workers=2) as pool:
        stale_login = pool.submit(service.handle, "POST", "/sessions", ACCOUNT, "")
        assert hashing_started.wait(timeout=5)
        assert service.handle("DELETE", "/users/me", None, f"Bearer {old_token}")[0] == 200
        replacement = {"username": "alice", "password": "replacement"}
        assert service.handle("POST", "/users", replacement, "")[0] == 201
        resume_hashing.set()
        assert stale_login.result(timeout=5)[0] == 401

    assert service.handle("POST", "/sessions", ACCOUNT, "")[0] == 401
    assert service.handle("POST", "/sessions", replacement, "")[0] == 200


def test_concurrent_text_work_cannot_survive_account_deletion() -> None:
    service = Service()
    token = register_and_login(service)
    authorization = f"Bearer {token}"

    def mutate(index: int) -> int:
        if index % 2:
            return service.handle("DELETE", "/texts/note", None, authorization)[0]
        return service.handle("PUT", "/texts/note", {"text": str(index)}, authorization)[0]

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(mutate, index) for index in range(40)]
        deletion = pool.submit(service.handle, "DELETE", "/users/me", None, authorization)
        statuses = [future.result() for future in futures]
        assert deletion.result()[0] in (200, 401)

    assert set(statuses) <= {200, 404, 401}
    assert service.handle("GET", "/texts", None, authorization)[0] == 401
