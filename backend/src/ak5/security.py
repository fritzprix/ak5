"""Runtime secrets for JWT signing and optional identify gate.

UX stays the same: CLI/MCP pick up ``AK5_IDENTIFY_SECRET`` from the environment
automatically. The dashboard can mint actor JWTs after a successful web-login
cookie when the identify secret is enabled.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
from pathlib import Path

from fastapi import Request

from ak5.paths import get_app_data_dir
from ak5.web_auth import COOKIE_NAME, get_web_auth_config, verify_session_cookie

logger = logging.getLogger(__name__)

IDENTIFY_SECRET_HEADER = "X-AK5-Identify-Secret"

# Legacy hard-coded value — never treat as a configured production secret.
_LEGACY_INSECURE_JWT = "ak5-dev-secret-key-change-in-production-2026"

_jwt_secret_cache: str | None = None


def jwt_secret_file() -> Path:
    """Path used to persist an auto-generated JWT signing key."""
    override = os.environ.get("AK5_JWT_SECRET_FILE", "").strip()
    if override:
        return Path(override).expanduser()

    app_secret = get_app_data_dir() / "jwt_secret"
    legacy_secret = Path.cwd() / ".ak5" / "jwt_secret"
    if not app_secret.exists() and legacy_secret.is_file():
        try:
            shutil.copy2(legacy_secret, app_secret)
            logger.info("Migrated legacy JWT secret from %s to %s", legacy_secret, app_secret)
        except OSError as e:
            logger.warning(
                "Failed to copy legacy JWT secret from %s to %s: %s (a new secret will be generated)",
                legacy_secret,
                app_secret,
                e,
            )
    return app_secret


def reset_secret_caches() -> None:
    """Clear cached secrets (tests only)."""
    global _jwt_secret_cache
    _jwt_secret_cache = None


def _env_or_settings_jwt() -> str:
    for key in ("AK5_JWT_SECRET", "JWT_SECRET"):
        value = os.environ.get(key, "").strip()
        if value and value != _LEGACY_INSECURE_JWT:
            return value

    from ak5.config import settings

    configured = (settings.JWT_SECRET or "").strip()
    if configured and configured != _LEGACY_INSECURE_JWT:
        return configured
    return ""


def resolve_jwt_secret() -> str:
    """Resolve JWT signing secret: env → settings → persisted file → generate."""
    from_env = _env_or_settings_jwt()
    if from_env:
        return from_env

    path = jwt_secret_file()
    try:
        if path.is_file():
            stored = path.read_text(encoding="utf-8").strip()
            if stored:
                return stored
    except OSError as exc:
        logger.warning("Could not read JWT secret file %s: %s", path, exc)

    generated = secrets.token_urlsafe(48)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(generated + "\n")
        logger.info("Generated JWT signing secret at %s (set AK5_JWT_SECRET to override)", path)
    except OSError as exc:
        logger.warning(
            "Could not persist JWT secret to %s (%s); using in-memory secret for this process",
            path,
            exc,
        )
    return generated


def get_jwt_secret() -> str:
    """Cached JWT signing secret for the process lifetime."""
    global _jwt_secret_cache
    if _jwt_secret_cache is None:
        _jwt_secret_cache = resolve_jwt_secret()
    return _jwt_secret_cache


def get_identify_secret() -> str:
    """Optional shared secret required for ``POST /auth/identify`` when set."""
    for key in ("AK5_IDENTIFY_SECRET", "IDENTIFY_SECRET"):
        value = os.environ.get(key, "").strip()
        if value:
            return value

    from ak5.config import settings

    return (settings.IDENTIFY_SECRET or "").strip()


def identify_headers_from_env() -> dict[str, str]:
    """Headers CLI/MCP/SDK should attach when calling identify (empty if unset)."""
    secret = get_identify_secret()
    if not secret:
        return {}
    return {IDENTIFY_SECRET_HEADER: secret}


def extract_identify_secret(request: Request, body_secret: str | None = None) -> str | None:
    header_val = request.headers.get(IDENTIFY_SECRET_HEADER)
    if header_val and header_val.strip():
        return header_val.strip()
    if body_secret and body_secret.strip():
        return body_secret.strip()
    return None


def is_identify_authorized(request: Request, provided_secret: str | None) -> bool:
    """Allow identify when secret unset, matches, or valid web-auth cookie (gate on)."""
    required = get_identify_secret()
    if not required:
        return True

    if provided_secret is not None and secrets.compare_digest(provided_secret, required):
        return True

    # Dashboard UX: after web login, cookie proves the browser passed the gate.
    cfg = get_web_auth_config()
    if cfg.enabled:
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie and verify_session_cookie(cookie):
            return True

    return False
