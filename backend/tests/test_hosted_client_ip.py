"""Hosted client-IP trust strategy (#125)."""

from __future__ import annotations

import pytest
from ak5.server.web_auth import (
    resolve_client_ip,
    validate_hosted_client_ip_config,
)


def test_validate_hosted_requires_proxies_or_header(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "hosted")
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("AK5_CLIENT_IP_HEADER", raising=False)
    with pytest.raises(ValueError, match="AK5_TRUSTED_PROXIES|AK5_CLIENT_IP_HEADER"):
        validate_hosted_client_ip_config()


def test_validate_hosted_accepts_client_ip_header(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "hosted")
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    monkeypatch.setenv("AK5_CLIENT_IP_HEADER", "x-vercel-forwarded-for")
    validate_hosted_client_ip_config()


def test_validate_hosted_rejects_unknown_header(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "hosted")
    monkeypatch.setenv("AK5_CLIENT_IP_HEADER", "x-forwarded-for")
    with pytest.raises(ValueError, match="Invalid AK5_CLIENT_IP_HEADER"):
        validate_hosted_client_ip_config()


def test_local_profile_skips_hosted_gate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "local")
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("AK5_CLIENT_IP_HEADER", raising=False)
    validate_hosted_client_ip_config()


def test_platform_header_mode_uses_header(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_CLIENT_IP_HEADER", "x-vercel-forwarded-for")
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    ip = resolve_client_ip(
        {"x-vercel-forwarded-for": "203.0.113.9, 10.0.0.1"},
        "10.0.0.1",
    )
    assert ip == "203.0.113.9"


def test_without_platform_mode_spoofed_header_ignored(monkeypatch: pytest.MonkeyPatch):
    """Spoofing: untrusted peer + X-Real-IP must not change the bucket (#125)."""
    monkeypatch.delenv("AK5_CLIENT_IP_HEADER", raising=False)
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    ip = resolve_client_ip(
        {"x-real-ip": "203.0.113.9", "x-forwarded-for": "203.0.113.9"},
        "198.51.100.2",
    )
    assert ip == "198.51.100.2"
