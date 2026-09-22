"""Thread-safe in-memory implementation of the text service protocol."""

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

ROUTES = (
    ("GET", "/ping"),
    ("POST", "/echo"),
    ("POST", "/users"),
    ("POST", "/sessions"),
    ("DELETE", "/sessions/current"),
    ("DELETE", "/users/me"),
    ("GET", "/texts"),
    ("PUT", "/texts/{name}"),
    ("GET", "/texts/{name}"),
    ("DELETE", "/texts/{name}"),
)

STATIC_METHODS = {
    "/ping": {"GET"},
    "/echo": {"POST"},
    "/users": {"POST"},
    "/sessions": {"POST"},
    "/sessions/current": {"DELETE"},
    "/users/me": {"DELETE"},
    "/texts": {"GET"},
}
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")
TEXT_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_TEXT_BYTES = 65_536


def route_error(method: str, path: str) -> int | None:
    allowed = {"PUT", "GET", "DELETE"} if path.startswith("/texts/") else STATIC_METHODS.get(path)
    if allowed is None:
        return 404
    return None if method in allowed else 405


@dataclass
class User:
    salt: bytes
    digest: bytes
    token: str | None = None
    token_deadline: float | None = None
    texts: dict[str, str] = field(default_factory=dict)


class Service:
    def __init__(
        self,
        token_ttl_seconds: int = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(token_ttl_seconds) is not int or token_ttl_seconds <= 0:
            raise ValueError("token TTL must be a positive integer")
        self.token_ttl_seconds = token_ttl_seconds
        self.clock = clock
        self.users: dict[str, User] = {}
        self.lock = threading.Lock()

    def _authenticate_locked(self, authorization: str) -> tuple[str, User] | None:
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        if not token:
            return None
        for name, user in self.users.items():
            if user.token != token:
                continue
            if user.token_deadline is None or self.clock() >= user.token_deadline:
                user.token = None
                user.token_deadline = None
                return None
            return name, user
        return None

    @staticmethod
    def _text_from_body(body: Any) -> tuple[int, dict[str, Any]] | str:
        if not isinstance(body, dict) or set(body) != {"text"} or not isinstance(body["text"], str):
            return 400, {"message": "Expected a text string"}
        text = body["text"]
        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            return 400, {"message": "Text must be valid Unicode"}
        if size > MAX_TEXT_BYTES:
            return 413, {"message": "Text is too large"}
        return text

    def handle(
        self, method: str, path: str, body: Any, authorization: str
    ) -> tuple[int, dict[str, Any]]:
        if status := route_error(method, path):
            return status, {"message": "Not found" if status == 404 else "Method not allowed"}
        if method == "GET" and path == "/ping":
            return 200, {"data": "pong"}
        if method == "POST" and path == "/echo":
            text = self._text_from_body(body)
            if not isinstance(text, str):
                return text
            return 200, {"data": text}
        if path in ("/users", "/sessions") and method == "POST":
            if not isinstance(body, dict) or set(body) != {"username", "password"}:
                return 400, {"message": "Expected username and password"}
            name, password = body["username"], body["password"]
            if (
                not isinstance(name, str)
                or USERNAME_PATTERN.fullmatch(name) is None
                or not isinstance(password, str)
                or not 8 <= len(password) <= 128
            ):
                return 400, {"message": "Invalid username or password length"}
            try:
                encoded_password = password.encode("utf-8")
            except UnicodeError:
                return 400, {"message": "Password must be valid Unicode"}

            # Password hashing stays outside the lock so unrelated requests can progress.
            if path == "/users":
                salt = secrets.token_bytes(16)
                digest = hashlib.pbkdf2_hmac("sha256", encoded_password, salt, 100_000)
                with self.lock:
                    if name in self.users:
                        return 409, {"message": "Username exists"}
                    self.users[name] = User(salt, digest)
                return 201, {"data": {"username": name}}

            with self.lock:
                user = self.users.get(name)
                if user is None:
                    return 401, {"message": "Invalid username or password"}
                salt, expected = user.salt, user.digest
            digest = hashlib.pbkdf2_hmac("sha256", encoded_password, salt, 100_000)
            with self.lock:
                # The username may have been deleted and registered again while hashing.
                if self.users.get(name) is not user or not hmac.compare_digest(digest, expected):
                    return 401, {"message": "Invalid username or password"}
                user.token = secrets.token_urlsafe(32)
                user.token_deadline = self.clock() + self.token_ttl_seconds
                return 200, {
                    "data": {
                        "token": user.token,
                        "expires_in": self.token_ttl_seconds,
                    }
                }

        text_name: str | None = None
        text_value: str | None = None
        if path.startswith("/texts/"):
            text_name = path.removeprefix("/texts/")
            if TEXT_NAME_PATTERN.fullmatch(text_name) is None:
                return 400, {"message": "Invalid text name"}
            if method == "PUT":
                parsed_text = self._text_from_body(body)
                if not isinstance(parsed_text, str):
                    return parsed_text
                text_value = parsed_text

        with self.lock:
            identity = self._authenticate_locked(authorization)
            if identity is None:
                return 401, {"message": "Login required"}
            username, user = identity

            if path == "/sessions/current":
                user.token = None
                user.token_deadline = None
                return 200, {"data": None}
            if path == "/users/me":
                del self.users[username]
                return 200, {"data": None}
            if path == "/texts":
                return 200, {"data": sorted(user.texts)}
            if text_name is not None and method == "PUT":
                assert text_value is not None
                user.texts[text_name] = text_value
                return 200, {"data": None}
            if text_name is not None and method == "GET":
                if text_name not in user.texts:
                    return 404, {"message": "Text not found"}
                return 200, {"data": user.texts[text_name]}
            if text_name is not None and method == "DELETE":
                if text_name not in user.texts:
                    return 404, {"message": "Text not found"}
                del user.texts[text_name]
                return 200, {"data": None}

        return 404, {"message": "Not found"}
