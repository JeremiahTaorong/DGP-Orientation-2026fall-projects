import argparse
import getpass
from typing import Any

import httpx

END_MARKER = ":end"


def exchange(
    client: httpx.Client, method: str, path: str, token: str = "", body: object = None
) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.request(method, path, json=body, headers=headers)
    try:
        result = response.json()
    except ValueError:
        result = {"message": response.text}
    return response.status_code, result


def read_text() -> str:
    """Read multiline text, preserving empty input and a trailing newline."""
    print(f"Enter text. Finish with {END_MARKER}; use \\{END_MARKER} for a literal marker.")
    lines: list[str] = []
    while True:
        line = input()
        if line == END_MARKER:
            return "\n".join(lines)
        if line == f"\\{END_MARKER}":
            line = END_MARKER
        lines.append(line)


def command_request(command: str) -> tuple[str, str, object]:
    """Collect command input and return the corresponding HTTP request."""
    if command in ("register", "login"):
        body = {
            "username": input("username: "),
            "password": getpass.getpass("password: "),
        }
        return "POST", "/users" if command == "register" else "/sessions", body
    if command in ("ping", "logout", "list", "delete-user"):
        method, path = {
            "ping": ("GET", "/ping"),
            "logout": ("DELETE", "/sessions/current"),
            "list": ("GET", "/texts"),
            "delete-user": ("DELETE", "/users/me"),
        }[command]
        return method, path, None
    if command == "echo":
        return "POST", "/echo", {"text": read_text()}
    if command in ("put", "get", "delete"):
        name = input("name: ")
        method = {"put": "PUT", "get": "GET", "delete": "DELETE"}[command]
        body = {"text": read_text()} if command == "put" else None
        return method, f"/texts/{name}", body
    raise ValueError("Unknown command")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7878")
    args = parser.parse_args()
    token = ""
    with httpx.Client(
        base_url=args.url, timeout=12, follow_redirects=False, trust_env=False
    ) as client:
        try:
            while True:
                command = input(
                    "ping / register / login / logout / list / echo / "
                    "delete-user / put / get / delete / q > "
                ).strip()
                if command == "q":
                    break
                try:
                    method, path, body = command_request(command)
                except ValueError:
                    print("Unknown command.")
                    continue
                try:
                    status, result = exchange(client, method, path, token, body)
                    print(status, result)
                    if command == "login" and status == 200:
                        token = result["data"]["token"]
                    if status == 401:
                        print("Please log in again.")
                    if status == 401 or (command in ("logout", "delete-user") and status == 200):
                        token = ""
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    print(f"Request failed: {exc}")
        except (EOFError, KeyboardInterrupt):
            print()


if __name__ == "__main__":
    main()
