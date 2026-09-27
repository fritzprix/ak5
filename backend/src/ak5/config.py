from importlib.metadata import PackageNotFoundError, version

from pydantic_settings import BaseSettings, SettingsConfigDict

try:
    _pkg_version = version("ak5")
except PackageNotFoundError:
    _pkg_version = "1.0.3"


class Settings(BaseSettings):
    PROJECT_NAME: str = "AK5"
    VERSION: str = _pkg_version
    API_V1_STR: str = "/api/v1"

    DATABASE_URL: str = "sqlite+aiosqlite:///./ak5.db"
    SQLITE_BUSY_TIMEOUT: int = 5000

    # Prefer AK5_JWT_SECRET env. Empty → auto-generate/persist under .ak5/jwt_secret.
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Optional shared secret for POST /auth/identify (AK5_IDENTIFY_SECRET).
    # When set, CLI/MCP send it automatically; web UI may use the auth cookie instead.
    IDENTIFY_SECRET: str = ""

    DEFAULT_BOARD_ID: str = "proj-core-engine"
    DEFAULT_BOARD_NAME: str = "Core Engine Project"

    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    # Optional web gate (prefer AK5_AUTH_* / AK5_WEB_* via os.environ in web_auth)
    AUTH_USERNAME: str = "admin"
    AUTH_PASSWORD: str = ""
    WEB_USER: str = ""
    WEB_PASSWORD: str = ""

    # Attachments configuration
    ATTACHMENTS_DIR: str = "./data/attachments"
    MAX_ATTACHMENT_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
