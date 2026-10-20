import logging
import os
from importlib.metadata import PackageNotFoundError, version

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ak5.paths import get_default_attachments_dir, get_default_database_url

try:
    _pkg_version = version("ak5")
except PackageNotFoundError:
    _pkg_version = "1.0.3"

logger = logging.getLogger(__name__)

ALLOWED_DEPLOY_PROFILES = frozenset({"local", "hosted"})
_legacy_deploy_profile_warned = False


def get_deploy_profile() -> str:
    """Return the server deployment profile ('local' or 'hosted').

    Precedence (operations.md §7, #156):
    1. AK5_DEPLOY_PROFILE: explicit server deployment profile ('local' or 'hosted').
       Unknown values raise ``ValueError`` (fail-closed; never treated as local).
    2. Deprecated fallback: AK5_PROFILE if set to 'hosted' or 'local'.
       Logs a one-time ``logger.warning`` per process.
       Note: If AK5_PROFILE is set to anything else (such as a CLI profile name 'worker-1'),
       the server does not fallback and defaults to 'local'.
    3. Default: 'local'.
    """
    global _legacy_deploy_profile_warned

    deploy_profile = os.environ.get("AK5_DEPLOY_PROFILE")
    if deploy_profile is not None and deploy_profile.strip():
        val = deploy_profile.strip().lower()
        if val not in ALLOWED_DEPLOY_PROFILES:
            allowed = ", ".join(sorted(ALLOWED_DEPLOY_PROFILES))
            raise ValueError(
                f"Invalid AK5_DEPLOY_PROFILE={deploy_profile!r}. Allowed values: {allowed}."
            )
        return val

    legacy_profile = os.environ.get("AK5_PROFILE")
    if legacy_profile is not None and legacy_profile.strip():
        val = legacy_profile.strip().lower()
        if val in ALLOWED_DEPLOY_PROFILES:
            if not _legacy_deploy_profile_warned:
                logger.warning(
                    "AK5_PROFILE=%r is deprecated for server deployment profile. "
                    "Use AK5_DEPLOY_PROFILE instead. "
                    "Note: If your CLI profile name is 'hosted' or 'local', "
                    "set AK5_DEPLOY_PROFILE explicitly.",
                    legacy_profile,
                )
                _legacy_deploy_profile_warned = True
            return val

    return "local"


def hosted_profile() -> bool:
    """Return True if the active server deployment profile is 'hosted'."""
    return get_deploy_profile() == "hosted"


ALLOWED_NOTIFY_MODES = frozenset({"inproc", "poll", "pg", "redis"})


def get_notify_mode() -> str:
    """Return AK5_NOTIFY mode (deployment.md §3, #108).

    Legacy: ``LONG_POLL_USE_INPROCESS_NOTIFY=false`` with default ``inproc``
    behaves as ``poll`` so existing serverless env still works.
    """
    raw = (settings.NOTIFY or "inproc").strip().lower()
    if raw not in ALLOWED_NOTIFY_MODES:
        allowed = ", ".join(sorted(ALLOWED_NOTIFY_MODES))
        raise ValueError(f"Invalid AK5_NOTIFY={settings.NOTIFY!r}. Allowed: {allowed}.")
    if raw == "inproc" and not settings.LONG_POLL_USE_INPROCESS_NOTIFY:
        return "poll"
    return raw


def notify_poll_interval_seconds() -> float:
    """Wait slice for ``poll`` mode (AK5_NOTIFY_POLL_MS)."""
    return max(0.05, float(settings.NOTIFY_POLL_MS) / 1000.0)


def validate_notify_config() -> None:
    """Fail closed on unsupported or invalid notify combinations (deployment.md §7)."""
    mode = get_notify_mode()
    if mode == "redis":
        raise ValueError(
            "AK5_NOTIFY=redis is not implemented yet; use inproc, poll, or pg."
        )
    if mode == "pg":
        url = (settings.DATABASE_URL or "").lower()
        if "sqlite" in url or not any(
            x in url for x in ("postgres", "postgresql")
        ):
            raise ValueError("AK5_NOTIFY=pg requires a PostgreSQL DATABASE_URL.")
        try:
            import asyncpg  # noqa: F401
        except ImportError as exc:
            raise ValueError(
                "AK5_NOTIFY=pg requires the asyncpg package "
                "(install ak5 with the server/postgres extra)."
            ) from exc
    if mode == "inproc" and int(settings.INSTANCES) > 1:
        raise ValueError(
            "AK5_NOTIFY=inproc is incompatible with AK5_INSTANCES>1; "
            "use poll or pg for multi-instance deployments."
        )


class Settings(BaseSettings):
    PROJECT_NAME: str = "AK5"
    VERSION: str = _pkg_version
    API_V1_STR: str = "/api/v1"

    DATABASE_URL: str = Field(default_factory=get_default_database_url)
    # Optional admin/migration URL (BYPASSRLS role). When empty, admin sessions use
    # DATABASE_URL plus the ak5.bypass_rls GUC (deployment.md §1.5 / #106).
    DATABASE_ADMIN_URL: str = Field(
        default="",
        validation_alias=AliasChoices("AK5_DATABASE_ADMIN_URL", "DATABASE_ADMIN_URL"),
    )
    SQLITE_BUSY_TIMEOUT: int = 5000
    AK5_DB_POOL_SIZE: int = 5
    AK5_DB_MAX_OVERFLOW: int = 10
    AK5_DB_POOL_TIMEOUT: float = 30.0

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
    # Prefer AK5_NOTIFY=poll|pg (deployment.md §3); this flag maps inproc→poll when False.
    LONG_POLL_USE_INPROCESS_NOTIFY: bool = True
    # Cross-instance wake-up (deployment.md §3, #108): inproc | poll | pg | redis
    NOTIFY: str = Field(
        default="inproc",
        validation_alias=AliasChoices("AK5_NOTIFY", "NOTIFY"),
    )
    NOTIFY_POLL_MS: int = Field(
        default=1000,
        validation_alias=AliasChoices("AK5_NOTIFY_POLL_MS", "NOTIFY_POLL_MS"),
    )
    # Declared replica count for start-time guards (deployment.md §7).
    INSTANCES: int = Field(
        default=1,
        validation_alias=AliasChoices("AK5_INSTANCES", "INSTANCES"),
    )
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
