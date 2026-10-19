from importlib.metadata import PackageNotFoundError, version

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ak5.paths import get_default_attachments_dir, get_default_database_url

try:
    _pkg_version = version("ak5")
except PackageNotFoundError:
    _pkg_version = "1.0.3"


class Settings(BaseSettings):
    PROJECT_NAME: str = "AK5"
    VERSION: str = _pkg_version
    API_V1_STR: str = "/api/v1"

    DATABASE_URL: str = Field(default_factory=get_default_database_url)
    SQLITE_BUSY_TIMEOUT: int = 5000

    # Prefer AK5_JWT_SECRET env. Empty → auto-generate/persist under app local storage (jwt_secret).
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    # Web session → user JWT exchange (#72). Short so logout/XSS exposure is bounded;
    # override with AK5_SESSION_TOKEN_TTL_MINUTES.
    SESSION_TOKEN_TTL_MINUTES: int = 15

    # Optional shared secret for POST /auth/identify (AK5_IDENTIFY_SECRET).
    # When set, CLI/MCP send it automatically; web UI may use the auth cookie instead.
    IDENTIFY_SECRET: str = ""
    # Local-only insecure identify (identity.md §2). Also requires loopback client.
    DEV_INSECURE_IDENTIFY: bool = False

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
    ATTACHMENTS_DIR: str = Field(default_factory=lambda: str(get_default_attachments_dir()))
    MAX_ATTACHMENT_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB

    # Event long-poll / retention (events.md §1, §3)
    LONG_POLL_MAX_WAIT: int = 25
    LONG_POLL_DB_POLL_INTERVAL: float = 1.0
    # Local/integrated mode: wake waiters via in-process event_bus notify.
    # Serverless / multi-worker: set False to rely on DB polling only.
    LONG_POLL_USE_INPROCESS_NOTIFY: bool = True
    EVENT_RETENTION_DAYS: int = 7
    # Min seconds between lazy retention purges in one process (SQLite write lock).
    EVENT_PURGE_MIN_INTERVAL_SECONDS: float = 60.0
    CONSUMER_LEASE_SECONDS: int = 60
    ACTIVE_SUBSCRIPTION_GRACE_SECONDS: float = Field(
        default=300.0,
        validation_alias=AliasChoices("AK5_ACTIVE_SUBSCRIPTION_GRACE_SECONDS", "ACTIVE_SUBSCRIPTION_GRACE_SECONDS"),
    )
    ABANDONED_SUBSCRIPTION_HOURS: float = Field(
        default=24.0,
        validation_alias=AliasChoices("AK5_ABANDONED_SUBSCRIPTION_HOURS", "ABANDONED_SUBSCRIPTION_HOURS"),
    )
    # Postgres commit-order ≠ seq-order guard (issue #11 orchestrator note).
    # 0 = no-op (SQLite). >0 holds back events newer than this many seconds.
    EVENT_VISIBILITY_WINDOW_SECONDS: float = 0.0

    # Workflow v2 (workflow.md §2.2 / claim lease)
    CLAIM_LEASE_TTL_SECONDS: int = 300  # 5 minutes
    MAX_AUTO_BOARDS: int = 50

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
