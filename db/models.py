"""SQLAlchemy ORM models."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── audit_jobs ─────────────────────────────────────────────────────────────────

class AuditJob(Base):
    __tablename__ = "audit_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Batch (folder upload) — jobs uploaded together share a batch_id
    batch_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    relative_path = Column(String(1000), nullable=True)  # path inside the uploaded folder/zip

    # File metadata
    filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False)

    # MinIO reference
    minio_bucket = Column(String(100), nullable=False)
    minio_object_key = Column(String(500), nullable=False)

    # Job lifecycle — QUEUED → PROCESSING → COMPLETED | FAILED
    status = Column(String(20), nullable=False, default="QUEUED", index=True)
    stage = Column(String(50), nullable=True)   # current processing stage
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    # Denormalized summary stats (populated on COMPLETED)
    tables_processed = Column(Integer, nullable=True)
    total_casting_errors = Column(Integer, nullable=True)
    total_cross_casting_errors = Column(Integer, nullable=True)
    is_clean = Column(Boolean, nullable=True)

    # Full report as JSONB
    report = Column(JSONB, nullable=True)

    # Relationships
    extracted_tables = relationship(
        "ExtractedTable", back_populates="job", cascade="all, delete-orphan",
    )
    issues = relationship(
        "Issue", back_populates="job", cascade="all, delete-orphan",
    )
    processing_logs = relationship(
        "ProcessingLog", back_populates="job", cascade="all, delete-orphan",
        order_by="ProcessingLog.timestamp",
    )


# ── extracted_tables ───────────────────────────────────────────────────────────

class ExtractedTable(Base):
    __tablename__ = "extracted_tables"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("audit_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    table_name = Column(String(255), nullable=False)
    page = Column(Integer, nullable=False, default=1)
    sheet = Column(String(255), nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    col_count = Column(Integer, nullable=False, default=0)
    raw_data = Column(JSONB, nullable=True)   # {title, headers, rows}
    created_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    job = relationship("AuditJob", back_populates="extracted_tables")
    issues = relationship("Issue", back_populates="extracted_table")


# ── issues ─────────────────────────────────────────────────────────────────────

class Issue(Base):
    __tablename__ = "issues"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("audit_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    table_id = Column(
        UUID(as_uuid=True),
        ForeignKey("extracted_tables.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Classification
    # CASTING_ERROR | CROSS_CASTING_ERROR | EXTRACTION_ERROR |
    # OCR_VLM_DISAGREEMENT | TABLE_STRUCTURE_ERROR | HUMAN_REVIEW
    issue_type = Column(String(50), nullable=False, index=True)
    severity = Column(String(20), nullable=False, index=True)  # critical|high|medium|low
    status = Column(String(20), nullable=False, default="open", index=True)  # open|acknowledged|resolved

    # Content
    table_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=False)
    description_ar = Column(Text, nullable=False, default="")

    # Financial values stored as strings to preserve decimal precision
    expected_value = Column(String(100), nullable=True)
    actual_value = Column(String(100), nullable=True)
    difference = Column(String(100), nullable=True)

    # Source location — PDF: {source,page,table_name,row,axis,bbox}
    #                   Excel: {source,sheet,table_name,row,axis}
    source_reference = Column(JSONB, nullable=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    job = relationship("AuditJob", back_populates="issues")
    extracted_table = relationship("ExtractedTable", back_populates="issues")

    __table_args__ = (
        Index("ix_issues_job_type", "job_id", "issue_type"),
        Index("ix_issues_job_severity", "job_id", "severity"),
    )


# ── processing_logs ────────────────────────────────────────────────────────────

class ProcessingLog(Base):
    __tablename__ = "processing_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("audit_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)   # started | completed | failed
    message = Column(Text, nullable=True)
    timestamp = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    job = relationship("AuditJob", back_populates="processing_logs")
