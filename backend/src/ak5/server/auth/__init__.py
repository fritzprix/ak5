"""Identity & tenancy auth surface (identity.md §2) — Principal + access tokens."""

from __future__ import annotations

from ak5.server.auth.principal import Principal, workflow_role_for_principal
from ak5.server.auth.scope import scoped
from ak5.server.auth.tokens import (
    get_current_principal,
    issue_access_token,
    verify_access_token,
)

__all__ = [
    "Principal",
    "get_current_principal",
    "issue_access_token",
    "scoped",
    "verify_access_token",
    "workflow_role_for_principal",
]