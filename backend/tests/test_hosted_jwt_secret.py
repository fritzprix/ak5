"""Hosted JWT secret must be explicit — no per-instance auto-generate (#165)."""

from __future__ import annotations

from pathlib import Path

import pytest
from ak5.server.security import (
    reset_secret_caches,
    resolve_jwt_secret,
    validate_hosted_jwt_secret_config,
)


@pytest.fixture(autouse=True)
def _clear_jwt_cache():
    reset_secret_caches()
    yield
    reset_secret_caches()


def test_validate_hosted_requires_jwt_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "hosted")
    monkeypatch.delenv("AK5_JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("AK5_JWT_SECRET_FILE", raising=False)
    monkeypatch.setattr("ak5.server.config.settings.JWT_SECRET", "")
    with pytest.raises(ValueError, match="AK5_JWT_SECRET"):
        validate_hosted_jwt_secret_config()


def test_validate_hosted_accepts_env_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "hosted")
    monkeypatch.setenv("AK5_JWT_SECRET", "hosted-fixed-secret-value")
    monkeypatch.delenv("AK5_JWT_SECRET_FILE", raising=False)
    validate_hosted_jwt_secret_config()


def test_validate_hosted_accepts_secret_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "hosted")
    monkeypatch.delenv("AK5_JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setattr("ak5.server.config.settings.JWT_SECRET", "")
    secret_file = tmp_path / "jwt"
    secret_file.write_text("file-mounted-secret\n", encoding="utf-8")
    monkeypatch.setenv("AK5_JWT_SECRET_FILE", str(secret_file))
    validate_hosted_jwt_secret_config()
    assert resolve_jwt_secret() == "file-mounted-secret"


def test_local_profile_allows_generate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "local")
    monkeypatch.delenv("AK5_JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("AK5_JWT_SECRET_FILE", raising=False)
    monkeypatch.setattr("ak5.server.config.settings.JWT_SECRET", "")
    monkeypatch.setenv("AK5_DATA_DIR", str(tmp_path / "data"))
    validate_hosted_jwt_secret_config()
    secret = resolve_jwt_secret()
    assert len(secret) >= 32
    assert (tmp_path / "data" / "jwt_secret").is_file()


def test_resolve_hosted_refuses_generate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "hosted")
    monkeypatch.delenv("AK5_JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("AK5_JWT_SECRET_FILE", raising=False)
    monkeypatch.setattr("ak5.server.config.settings.JWT_SECRET", "")
    monkeypatch.setenv("AK5_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(ValueError, match="AK5_JWT_SECRET"):
        resolve_jwt_secret()
