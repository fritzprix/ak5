import json
from pathlib import Path
from typing import Any

SESSION_FILE = Path.home() / ".ak5_session.json"
DEFAULT_API_URL = "http://127.0.0.1:8000/api/v1"


def load_session() -> dict[str, Any]:
    if SESSION_FILE.exists():
        try:
            with open(SESSION_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
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


def get_auth_headers(api_url: str | None = None) -> dict[str, str]:
    token = get_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    target_url = api_url or get_api_url()
    try:
        import httpx

        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{target_url}/auth/identify",
                json={"actor_id": "cli_user", "actor_type": "human", "name": "CLI User", "role": "PM"},
            )
            if resp.is_success:
                data = resp.json()
                token_val = data.get("access_token")
                if token_val:
                    # Persist session so subsequent CLI calls reuse it without repeating auth handshake
                    save_session({
                        "token": token_val,
                        "actor_id": "cli_user",
                        "actor_type": "human",
                        "role": "PM",
                        "api_url": target_url,
                    })
                    return {"Authorization": f"Bearer {token_val}"}
    except Exception:
        pass
    return {}

