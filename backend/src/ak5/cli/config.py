import json
from pathlib import Path
from typing import Any

SESSION_FILE = Path.home() / ".ak5_session.json"
DEFAULT_API_URL = "http://127.0.0.1:8000/api/v1"


def load_session() -> dict[str, Any]:
    if SESSION_FILE.exists():
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_session(data: dict[str, Any]) -> None:
    session = load_session()
    session.update(data)
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)


def get_token() -> str | None:
    return load_session().get("token")


def get_actor_id() -> str | None:
    return load_session().get("actor_id")


def get_api_url() -> str:
    return load_session().get("api_url", DEFAULT_API_URL)
