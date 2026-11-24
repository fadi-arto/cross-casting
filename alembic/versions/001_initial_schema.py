"""Initial schema — audit_jobs, extracted_tables, issues, processing_logs.

Revision ID: 001
Revises:
Create Date: 2026-11-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── audit_jobs ─────────────────────────────────────────────────────────────
    # Uses CREATE TABLE IF NOT EXISTS so a fresh create_all() install is safe
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_jobs (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            filename         VARCHAR(255) NOT NULL,
            mime_type        VARCHAR(100) NOT NULL,
            file_size        INTEGER NOT NULL,
            minio_bucket     VARCHAR(100) NOT NULL,
            minio_object_key VARCHAR(500) NOT NULL,
            status           VARCHAR(20)  NOT NULL DEFAULT 'QUEUED',
            stage            VARCHAR(50),
            started_at       TIMESTAMPTZ,
            completed_at     TIMESTAMPTZ,
            error_message    TEXT,
            tables_processed INTEGER,
            total_casting_errors       INTEGER,
            total_cross_casting_errors INTEGER,
            is_clean         BOOLEAN,
            report           JSONB
        )
    """)

    # Add new columns to existing audit_jobs installs (idempotent)
    op.execute("ALTER TABLE audit_jobs ADD COLUMN IF NOT EXISTS stage          VARCHAR(50)")
    op.execute("ALTER TABLE audit_jobs ADD COLUMN IF NOT EXISTS started_at     TIMESTAMPTZ")
    op.execute("ALTER TABLE audit_jobs ADD COLUMN IF NOT EXISTS completed_at   TIMESTAMPTZ")

    # ── extracted_tables ───────────────────────────────────────────────────────
    op.create_table(
        "extracted_tables",
        sa.Column("id",         UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id",     UUID(as_uuid=True),
                  sa.ForeignKey("audit_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("table_name", sa.String(255), nullable=False),
        sa.Column("page",       sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sheet",      sa.String(255), nullable=True),
        sa.Column("row_count",  sa.Integer(), nullable=False, server_default="0"),
        sa.Column("col_count",  sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_data",   JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_extracted_tables_job_id", "extracted_tables", ["job_id"])

    # ── issues ─────────────────────────────────────────────────────────────────
    op.create_table(
        "issues",
        sa.Column("id",              UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id",          UUID(as_uuid=True),
                  sa.ForeignKey("audit_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("table_id",        UUID(as_uuid=True),
                  sa.ForeignKey("extracted_tables.id", ondelete="SET NULL"), nullable=True),
        sa.Column("issue_type",      sa.String(50),  nullable=False),
        sa.Column("severity",        sa.String(20),  nullable=False),
        sa.Column("status",          sa.String(20),  nullable=False, server_default="open"),
        sa.Column("table_name",      sa.String(255), nullable=True),
        sa.Column("description",     sa.Text(),      nullable=False),
        sa.Column("description_ar",  sa.Text(),      nullable=False, server_default=""),
        sa.Column("expected_value",  sa.String(100), nullable=True),
        sa.Column("actual_value",    sa.String(100), nullable=True),
        sa.Column("difference",      sa.String(100), nullable=True),
        sa.Column("source_reference", JSONB,         nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_issues_job_id",       "issues", ["job_id"])
    op.create_index("ix_issues_table_id",     "issues", ["table_id"])
    op.create_index("ix_issues_issue_type",   "issues", ["issue_type"])
    op.create_index("ix_issues_severity",     "issues", ["severity"])
    op.create_index("ix_issues_status",       "issues", ["status"])
    op.create_index("ix_issues_created_at",   "issues", ["created_at"])
    op.create_index("ix_issues_job_type",     "issues", ["job_id", "issue_type"])

    # ── processing_logs ────────────────────────────────────────────────────────
    op.create_table(
        "processing_logs",
        sa.Column("id",        UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id",    UUID(as_uuid=True),
                  sa.ForeignKey("audit_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage",     sa.String(50),  nullable=False),
        sa.Column("status",    sa.String(20),  nullable=False),
        sa.Column("message",   sa.Text(),      nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_processing_logs_job_id",    "processing_logs", ["job_id"])
    op.create_index("ix_processing_logs_timestamp", "processing_logs", ["timestamp"])


def downgrade() -> None:
    op.drop_table("processing_logs")
    op.drop_table("issues")
    op.drop_table("extracted_tables")
    # We do NOT drop audit_jobs on downgrade to protect existing data
