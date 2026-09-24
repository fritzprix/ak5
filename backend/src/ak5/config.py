from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "AK5"
    VERSION: str = "1.0.2"
    API_V1_STR: str = "/api/v1"

    DATABASE_URL: str = "sqlite+aiosqlite:///./ak5.db"
    SQLITE_BUSY_TIMEOUT: int = 5000

    JWT_SECRET: str = "ak5-dev-secret-key-change-in-production-2026"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
