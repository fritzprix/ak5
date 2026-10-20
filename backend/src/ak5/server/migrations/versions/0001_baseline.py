"""v2.0.0 baseline schema (deployment.md §1.2, #105).

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-19

The schema every new database starts from. Databases created before Alembic are
brought to this shape by the frozen legacy path in ``ak5.server.database`` and then
stamped at this revision. Do not edit: change the schema with a new revision.

``upgrade()`` creates only what is missing (``_create_table`` / ``_create_index``), so
the same code both builds a new database and *adopts* a pre-Alembic one without
touching tables that already exist. Adopted tables have missing baseline indexes
supplemented (via ``if_not_exists=True``) when all indexed columns exist (#120).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_adopted: set[str] = set()
_adopted_columns: dict[str, set[str]] = {}


def _create_table(name, *args, **kwargs):
    if name in set(sa.inspect(op.get_bind()).get_table_names()):
        _adopted.add(name)  # keep the existing table (and its indexes) as it is
        return None
    return op.create_table(name, *args, **kwargs)


def _create_index(index_name, table_name, columns, unique=False, **kwargs):
    if table_name in _adopted:
        bind = op.get_bind()
        if table_name not in _adopted_columns:
            _adopted_columns[table_name] = {
                col["name"] for col in sa.inspect(bind).get_columns(table_name)
            }
        col_names = {c if isinstance(c, str) else getattr(c, "name", str(c)) for c in columns}
        if not col_names.issubset(_adopted_columns[table_name]):
            return
        op.create_index(index_name, table_name, columns, unique=unique, if_not_exists=True, **kwargs)
        return
    op.create_index(index_name, table_name, columns, unique=unique, **kwargs)


def upgrade() -> None:
    _adopted.clear()
    _adopted_columns.clear()
    _create_table('actors',
    sa.Column('actor_id', sa.String(length=128), nullable=False),
    sa.Column('actor_type', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('role', sa.String(length=128), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('capabilities', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('avatar_url', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("actor_type IN ('human', 'agent')", name='check_actor_type'),
    sa.CheckConstraint("status IN ('idle', 'busy', 'offline')", name='check_actor_status'),
    sa.PrimaryKeyConstraint('actor_id')
    )
    _create_table('agent_credentials',
    sa.Column('credential_id', sa.String(length=128), nullable=False),
    sa.Column('agent_id', sa.String(length=128), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('board_scope', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('credential_id')
    )
    _create_index(op.f('ix_agent_credentials_agent_id'), 'agent_credentials', ['agent_id'], unique=False)
    _create_table('agents',
    sa.Column('agent_id', sa.String(length=128), nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('workflow_role', sa.String(length=32), nullable=False),
    sa.Column('capabilities', sa.Text(), nullable=False),
    sa.Column('board_scope', sa.Text(), nullable=False),
    sa.Column('created_by', sa.String(length=128), nullable=False),
    sa.Column('disabled_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('agent_id')
    )
    _create_index(op.f('ix_agents_workspace_id'), 'agents', ['workspace_id'], unique=False)
    _create_table('event_log_meta',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    _create_table('events',
    sa.Column('seq', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('event_id', sa.String(length=64), nullable=False),
    sa.Column('type', sa.String(length=64), nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('board_id', sa.String(length=128), nullable=True),
    sa.Column('ticket_id', sa.String(length=64), nullable=True),
    sa.Column('actor_id', sa.String(length=128), nullable=True),
    sa.Column('data_json', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('seq'),
    sa.UniqueConstraint('event_id'),
    sqlite_autoincrement=True
    )
    _create_index('idx_events_board_seq', 'events', ['board_id', 'seq'], unique=False)
    _create_index('idx_events_created', 'events', ['created_at'], unique=False)
    _create_index('idx_events_type', 'events', ['type'], unique=False)
    _create_index('idx_events_workspace_seq', 'events', ['workspace_id', 'seq'], unique=False)
    _create_index(op.f('ix_events_workspace_id'), 'events', ['workspace_id'], unique=False)
    _create_table('idempotency_keys',
    sa.Column('key', sa.String(length=256), nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('actor', sa.String(length=128), nullable=False),
    sa.Column('op', sa.String(length=64), nullable=False),
    sa.Column('response_json', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    _create_index('idx_idempotency_actor_op', 'idempotency_keys', ['actor', 'op'], unique=False)
    _create_index(op.f('ix_idempotency_keys_workspace_id'), 'idempotency_keys', ['workspace_id'], unique=False)
    _create_table('memberships',
    sa.Column('membership_id', sa.String(length=128), nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('membership_id')
    )
    _create_index(op.f('ix_memberships_user_id'), 'memberships', ['user_id'], unique=False)
    _create_index(op.f('ix_memberships_workspace_id'), 'memberships', ['workspace_id'], unique=False)
    _create_table('oauth_device_codes',
    sa.Column('device_code', sa.String(length=128), nullable=False),
    sa.Column('user_code', sa.String(length=32), nullable=False),
    sa.Column('client_id', sa.String(length=64), nullable=False),
    sa.Column('principal', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=True),
    sa.Column('approved_by_user_id', sa.String(length=128), nullable=True),
    sa.Column('requested_name', sa.String(length=128), nullable=True),
    sa.Column('requested_role', sa.String(length=64), nullable=True),
    sa.Column('requested_capabilities', sa.Text(), nullable=False),
    sa.Column('requested_board_scope', sa.Text(), nullable=False),
    sa.Column('interval_seconds', sa.Integer(), nullable=False),
    sa.Column('last_poll_at', sa.DateTime(), nullable=True),
    sa.Column('user_code_attempts', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('device_code')
    )
    _create_index(op.f('ix_oauth_device_codes_user_code'), 'oauth_device_codes', ['user_code'], unique=True)
    _create_table('passkey_credentials',
    sa.Column('passkey_id', sa.String(length=128), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('credential_id', sa.LargeBinary(), nullable=False),
    sa.Column('public_key', sa.LargeBinary(), nullable=False),
    sa.Column('sign_count', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('passkey_id'),
    sa.UniqueConstraint('credential_id')
    )
    _create_index(op.f('ix_passkey_credentials_user_id'), 'passkey_credentials', ['user_id'], unique=False)
    _create_table('subscriptions',
    sa.Column('subscription_id', sa.String(length=128), nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('board_id', sa.String(length=128), nullable=True),
    sa.Column('events', sa.Text(), nullable=True),
    sa.Column('role', sa.String(length=64), nullable=True),
    sa.Column('capabilities', sa.Text(), nullable=True),
    sa.Column('for_agent', sa.String(length=128), nullable=True),
    sa.Column('created_by', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('ack_cursor', sa.Integer(), nullable=False),
    sa.Column('consumer_id', sa.String(length=128), nullable=True),
    sa.Column('consumer_lease_expires_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('subscription_id')
    )
    _create_index(op.f('ix_subscriptions_board_id'), 'subscriptions', ['board_id'], unique=False)
    _create_index(op.f('ix_subscriptions_workspace_id'), 'subscriptions', ['workspace_id'], unique=False)
    _create_table('user_identities',
    sa.Column('identity_id', sa.String(length=128), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('issuer', sa.String(length=512), nullable=False),
    sa.Column('subject', sa.String(length=512), nullable=False),
    sa.Column('email_verified', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('identity_id')
    )
    _create_index(op.f('ix_user_identities_user_id'), 'user_identities', ['user_id'], unique=False)
    _create_table('user_sessions',
    sa.Column('session_id', sa.String(length=128), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('refresh_token_hash', sa.String(length=128), nullable=False),
    sa.Column('client', sa.String(length=16), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('session_id')
    )
    _create_index(op.f('ix_user_sessions_user_id'), 'user_sessions', ['user_id'], unique=False)
    _create_table('users',
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('display_name', sa.String(length=256), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('user_id')
    )
    _create_table('webauthn_challenges',
    sa.Column('challenge_id', sa.String(length=128), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('purpose', sa.String(length=16), nullable=False),
    sa.Column('challenge', sa.LargeBinary(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('challenge_id')
    )
    _create_index(op.f('ix_webauthn_challenges_user_id'), 'webauthn_challenges', ['user_id'], unique=False)
    _create_table('workspace_invites',
    sa.Column('invite_id', sa.String(length=128), nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('invited_by', sa.String(length=128), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('accepted_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('invite_id')
    )
    _create_index(op.f('ix_workspace_invites_email'), 'workspace_invites', ['email'], unique=False)
    _create_index(op.f('ix_workspace_invites_token_hash'), 'workspace_invites', ['token_hash'], unique=True)
    _create_index(op.f('ix_workspace_invites_workspace_id'), 'workspace_invites', ['workspace_id'], unique=False)
    _create_table('workspaces',
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('policy', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('workspace_id')
    )
    _create_table('audit_logs',
    sa.Column('log_id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('actor_id', sa.String(length=128), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('target_type', sa.String(length=32), nullable=False),
    sa.Column('target_id', sa.String(length=128), nullable=False),
    sa.Column('payload', sa.Text(), nullable=True),
    sa.Column('timestamp', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['actors.actor_id'], ),
    sa.PrimaryKeyConstraint('log_id')
    )
    _create_index(op.f('ix_audit_logs_workspace_id'), 'audit_logs', ['workspace_id'], unique=False)
    _create_table('boards',
    sa.Column('board_id', sa.String(length=128), nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('template', sa.String(length=64), nullable=True),
    sa.Column('auto_created', sa.Boolean(), nullable=False),
    sa.CheckConstraint("kind IN ('inbox', 'standard')", name='check_board_kind'),
    sa.ForeignKeyConstraint(['created_by'], ['actors.actor_id'], ),
    sa.PrimaryKeyConstraint('board_id')
    )
    _create_index(op.f('ix_boards_workspace_id'), 'boards', ['workspace_id'], unique=False)
    _create_table('board_members',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('board_id', sa.String(length=128), nullable=False),
    sa.Column('actor_id', sa.String(length=128), nullable=False),
    sa.Column('role', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['actors.actor_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['board_id'], ['boards.board_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('board_id', 'actor_id', name='uq_board_actor')
    )
    _create_index(op.f('ix_board_members_actor_id'), 'board_members', ['actor_id'], unique=False)
    _create_index(op.f('ix_board_members_board_id'), 'board_members', ['board_id'], unique=False)
    _create_index(op.f('ix_board_members_workspace_id'), 'board_members', ['workspace_id'], unique=False)
    _create_table('columns',
    sa.Column('column_id', sa.String(length=128), nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('board_id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('wip_limit', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("stage IN ('unclassified', 'backlog', 'in_progress', 'agent_review', 'postmortem_pending', 'human_review', 'done')", name='check_column_stage'),
    sa.ForeignKeyConstraint(['board_id'], ['boards.board_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('column_id')
    )
    _create_index(op.f('ix_columns_workspace_id'), 'columns', ['workspace_id'], unique=False)
    _create_table('tickets',
    sa.Column('ticket_id', sa.String(length=64), nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('board_id', sa.String(length=128), nullable=False),
    sa.Column('column_id', sa.String(length=128), nullable=False),
    sa.Column('parent_ticket_id', sa.String(length=64), nullable=True),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('priority', sa.String(length=16), nullable=False),
    sa.Column('rank', sa.String(length=128), nullable=False),
    sa.Column('labels', sa.Text(), nullable=False),
    sa.Column('assigned_to', sa.String(length=128), nullable=True),
    sa.Column('created_by', sa.String(length=128), nullable=False),
    sa.Column('blocked_by', sa.Text(), nullable=True),
    sa.Column('execution_context', sa.Text(), nullable=True),
    sa.Column('due_date', sa.DateTime(), nullable=True),
    sa.Column('is_archived', sa.Boolean(), nullable=False),
    sa.Column('archived_at', sa.DateTime(), nullable=True),
    sa.Column('original_request', sa.Text(), nullable=True),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('claim_token', sa.String(length=128), nullable=True),
    sa.Column('claimed_by', sa.String(length=128), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
    sa.Column('lease_expirations', sa.Integer(), nullable=False),
    sa.Column('submitted_by', sa.String(length=128), nullable=True),
    sa.Column('blocked_kind', sa.String(length=32), nullable=True),
    sa.Column('blocked_since', sa.DateTime(), nullable=True),
    sa.Column('blocked_actor', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("priority IN ('low', 'medium', 'high', 'urgent')", name='check_ticket_priority'),
    sa.ForeignKeyConstraint(['assigned_to'], ['actors.actor_id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['board_id'], ['boards.board_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['column_id'], ['columns.column_id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['actors.actor_id'], ),
    sa.ForeignKeyConstraint(['parent_ticket_id'], ['tickets.ticket_id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('ticket_id')
    )
    _create_index('idx_tickets_archived', 'tickets', ['is_archived'], unique=False)
    _create_index('idx_tickets_assigned', 'tickets', ['assigned_to'], unique=False)
    _create_index('idx_tickets_board', 'tickets', ['board_id'], unique=False)
    _create_index('idx_tickets_column_rank', 'tickets', ['column_id', 'rank'], unique=False)
    _create_index('idx_tickets_parent', 'tickets', ['parent_ticket_id'], unique=False)
    _create_index(op.f('ix_tickets_workspace_id'), 'tickets', ['workspace_id'], unique=False)
    _create_table('ticket_attachments',
    sa.Column('attachment_id', sa.String(length=64), nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('ticket_id', sa.String(length=64), nullable=False),
    sa.Column('actor_id', sa.String(length=128), nullable=False),
    sa.Column('filename', sa.String(length=256), nullable=False),
    sa.Column('file_size', sa.Integer(), nullable=False),
    sa.Column('content_type', sa.String(length=128), nullable=False),
    sa.Column('storage_path', sa.String(length=512), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['actors.actor_id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.ticket_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('attachment_id')
    )
    _create_index('idx_attachments_ticket', 'ticket_attachments', ['ticket_id'], unique=False)
    _create_index(op.f('ix_ticket_attachments_workspace_id'), 'ticket_attachments', ['workspace_id'], unique=False)
    _create_table('ticket_comments',
    sa.Column('comment_id', sa.String(length=64), nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('ticket_id', sa.String(length=64), nullable=False),
    sa.Column('actor_id', sa.String(length=128), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('is_internal', sa.Boolean(), nullable=False),
    sa.Column('metadata_json', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['actors.actor_id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.ticket_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('comment_id')
    )
    _create_index(op.f('ix_ticket_comments_workspace_id'), 'ticket_comments', ['workspace_id'], unique=False)
    _create_table('ticket_reviews',
    sa.Column('review_id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('ticket_id', sa.String(length=64), nullable=False),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('findings_json', sa.Text(), nullable=False),
    sa.Column('reviewer', sa.String(length=128), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('misrouted', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.ticket_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('review_id')
    )
    _create_index(op.f('ix_ticket_reviews_ticket_id'), 'ticket_reviews', ['ticket_id'], unique=False)
    _create_index(op.f('ix_ticket_reviews_workspace_id'), 'ticket_reviews', ['workspace_id'], unique=False)
    _create_table('ticket_revisions',
    sa.Column('revision_id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('ticket_id', sa.String(length=64), nullable=False),
    sa.Column('spec', sa.Text(), nullable=False),
    sa.Column('acceptance_criteria_json', sa.Text(), nullable=False),
    sa.Column('author', sa.String(length=128), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.ticket_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('revision_id')
    )
    _create_index(op.f('ix_ticket_revisions_ticket_id'), 'ticket_revisions', ['ticket_id'], unique=False)
    _create_index(op.f('ix_ticket_revisions_workspace_id'), 'ticket_revisions', ['workspace_id'], unique=False)
    _create_table('ticket_postmortems',
    sa.Column('postmortem_id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('workspace_id', sa.String(length=128), nullable=False),
    sa.Column('ticket_id', sa.String(length=64), nullable=False),
    sa.Column('what_went_wrong', sa.Text(), nullable=False),
    sa.Column('root_cause', sa.Text(), nullable=False),
    sa.Column('revision_id', sa.Integer(), nullable=True),
    sa.Column('human_review_id', sa.Integer(), nullable=True),
    sa.Column('author', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['human_review_id'], ['ticket_reviews.review_id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['revision_id'], ['ticket_revisions.revision_id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.ticket_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('postmortem_id')
    )
    _create_index(op.f('ix_ticket_postmortems_ticket_id'), 'ticket_postmortems', ['ticket_id'], unique=False)
    _create_index(op.f('ix_ticket_postmortems_workspace_id'), 'ticket_postmortems', ['workspace_id'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_index(op.f('ix_ticket_postmortems_workspace_id'), table_name='ticket_postmortems')
    op.drop_index(op.f('ix_ticket_postmortems_ticket_id'), table_name='ticket_postmortems')
    op.drop_table('ticket_postmortems')
    op.drop_index(op.f('ix_ticket_revisions_workspace_id'), table_name='ticket_revisions')
    op.drop_index(op.f('ix_ticket_revisions_ticket_id'), table_name='ticket_revisions')
    op.drop_table('ticket_revisions')
    op.drop_index(op.f('ix_ticket_reviews_workspace_id'), table_name='ticket_reviews')
    op.drop_index(op.f('ix_ticket_reviews_ticket_id'), table_name='ticket_reviews')
    op.drop_table('ticket_reviews')
    op.drop_index(op.f('ix_ticket_comments_workspace_id'), table_name='ticket_comments')
    op.drop_table('ticket_comments')
    op.drop_index(op.f('ix_ticket_attachments_workspace_id'), table_name='ticket_attachments')
    op.drop_index('idx_attachments_ticket', table_name='ticket_attachments')
    op.drop_table('ticket_attachments')
    op.drop_index(op.f('ix_tickets_workspace_id'), table_name='tickets')
    op.drop_index('idx_tickets_parent', table_name='tickets')
    op.drop_index('idx_tickets_column_rank', table_name='tickets')
    op.drop_index('idx_tickets_board', table_name='tickets')
    op.drop_index('idx_tickets_assigned', table_name='tickets')
    op.drop_index('idx_tickets_archived', table_name='tickets')
    op.drop_table('tickets')
    op.drop_index(op.f('ix_columns_workspace_id'), table_name='columns')
    op.drop_table('columns')
    op.drop_index(op.f('ix_board_members_workspace_id'), table_name='board_members')
    op.drop_index(op.f('ix_board_members_board_id'), table_name='board_members')
    op.drop_index(op.f('ix_board_members_actor_id'), table_name='board_members')
    op.drop_table('board_members')
    op.drop_index(op.f('ix_boards_workspace_id'), table_name='boards')
    op.drop_table('boards')
    op.drop_index(op.f('ix_audit_logs_workspace_id'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_table('workspaces')
    op.drop_index(op.f('ix_workspace_invites_workspace_id'), table_name='workspace_invites')
    op.drop_index(op.f('ix_workspace_invites_token_hash'), table_name='workspace_invites')
    op.drop_index(op.f('ix_workspace_invites_email'), table_name='workspace_invites')
    op.drop_table('workspace_invites')
    op.drop_index(op.f('ix_webauthn_challenges_user_id'), table_name='webauthn_challenges')
    op.drop_table('webauthn_challenges')
    op.drop_table('users')
    op.drop_index(op.f('ix_user_sessions_user_id'), table_name='user_sessions')
    op.drop_table('user_sessions')
    op.drop_index(op.f('ix_user_identities_user_id'), table_name='user_identities')
    op.drop_table('user_identities')
    op.drop_index(op.f('ix_subscriptions_workspace_id'), table_name='subscriptions')
    op.drop_index(op.f('ix_subscriptions_board_id'), table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_index(op.f('ix_passkey_credentials_user_id'), table_name='passkey_credentials')
    op.drop_table('passkey_credentials')
    op.drop_index(op.f('ix_oauth_device_codes_user_code'), table_name='oauth_device_codes')
    op.drop_table('oauth_device_codes')
    op.drop_index(op.f('ix_memberships_workspace_id'), table_name='memberships')
    op.drop_index(op.f('ix_memberships_user_id'), table_name='memberships')
    op.drop_table('memberships')
    op.drop_index(op.f('ix_idempotency_keys_workspace_id'), table_name='idempotency_keys')
    op.drop_index('idx_idempotency_actor_op', table_name='idempotency_keys')
    op.drop_table('idempotency_keys')
    op.drop_index(op.f('ix_events_workspace_id'), table_name='events')
    op.drop_index('idx_events_workspace_seq', table_name='events')
    op.drop_index('idx_events_type', table_name='events')
    op.drop_index('idx_events_created', table_name='events')
    op.drop_index('idx_events_board_seq', table_name='events')
    op.drop_table('events')
    op.drop_table('event_log_meta')
    op.drop_index(op.f('ix_agents_workspace_id'), table_name='agents')
    op.drop_table('agents')
    op.drop_index(op.f('ix_agent_credentials_agent_id'), table_name='agent_credentials')
    op.drop_table('agent_credentials')
    op.drop_table('actors')
    # ### end Alembic commands ###
