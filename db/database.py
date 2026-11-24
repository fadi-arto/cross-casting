"""Database engine and session management."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def build_engine(db_url: str) -> Engine:
    return create_engine(
        db_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args={"connect_timeout": 10},
    )


def create_tables(engine: Engine) -> None:
    from db.models import Base
    Base.metadata.create_all(engine)
    # create_all() never alters existing tables — add columns introduced later
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE audit_jobs ADD COLUMN IF NOT EXISTS batch_id UUID"))
        conn.execute(text("ALTER TABLE audit_jobs ADD COLUMN IF NOT EXISTS relative_path VARCHAR(1000)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_audit_jobs_batch_id ON audit_jobs (batch_id)"
        ))


def ping(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


@contextmanager
def get_session(engine: Engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
