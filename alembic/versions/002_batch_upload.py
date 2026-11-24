"""Batch (folder) upload — audit_jobs.batch_id and audit_jobs.relative_path.

Revision ID: 002
Revises: 001
Create Date: 2026-11-15
"""
from __future__ import annotations

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: the API's startup create_tables() may already have added these
    op.execute("ALTER TABLE audit_jobs ADD COLUMN IF NOT EXISTS batch_id UUID")
    op.execute("ALTER TABLE audit_jobs ADD COLUMN IF NOT EXISTS relative_path VARCHAR(1000)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_jobs_batch_id ON audit_jobs (batch_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_jobs_batch_id")
    op.execute("ALTER TABLE audit_jobs DROP COLUMN IF EXISTS relative_path")
    op.execute("ALTER TABLE audit_jobs DROP COLUMN IF EXISTS batch_id")
