"""Guard: every non-nullable workspace_id tenant table must be in TENANT_RLS_TABLES (#106)."""

from __future__ import annotations

from ak5.server.models.base import Base
from ak5.server.rls import TENANT_RLS_TABLES

# Tables that carry workspace_id but are global / identity-root and must not use tenant RLS.
_RLS_EXEMPT_TABLES = frozenset(
    {
        "workspaces",
        "oauth_device_codes",  # optional workspace_id on pending device grants
    }
)


def test_tenant_tables_with_workspace_id_are_rls_protected():
    if columns := _missing_rls_tables():
        missing = ", ".join(sorted(columns))
        raise AssertionError(
            f"Tables with workspace_id missing from TENANT_RLS_TABLES: {missing}. "
            f"Add them to rls.py and ship an Alembic migration that calls apply_postgres_rls."
        )


def _missing_rls_tables() -> set[str]:
    protected = set(TENANT_RLS_TABLES)
    missing: set[str] = set()
    for table in Base.metadata.sorted_tables:
        if table.name in _RLS_EXEMPT_TABLES:
            continue
        cols = {c.name for c in table.columns}
        if "workspace_id" not in cols:
            continue
        ws_col = table.c.workspace_id
        if (
            ws_col.nullable
            and ws_col.primary_key is False
            and table.name not in protected
        ):
            # Nullable workspace_id (e.g. blob_gc) still gets RLS when listed; skip unlisted nullable.
            continue
        if table.name not in protected:
            missing.add(table.name)
    # Sanity: every listed table exists.
    known = {t.name for t in Base.metadata.sorted_tables}
    orphan = protected - known
    assert not orphan, f"TENANT_RLS_TABLES references unknown tables: {orphan}"
    return missing


def test_rls_list_matches_model_tables():
    """Every TENANT_RLS_TABLES entry has workspace_id on the model."""
    for name in TENANT_RLS_TABLES:
        table = Base.metadata.tables[name]
        assert "workspace_id" in {c.name for c in table.columns}, name
        assert name not in _RLS_EXEMPT_TABLES, name