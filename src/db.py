"""Database engine and session lifecycle, kept separate from domain logic."""
from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .migrations import apply_migrations


def create_database(url: str | None = None) -> Engine:
    database_url = url or os.getenv('SPENDWISE_DATABASE_URL', 'sqlite:///spendwise.db')
    connect_args = {'check_same_thread': False} if database_url.startswith('sqlite') else {}
    engine = create_engine(database_url, future=True, connect_args=connect_args)
    apply_migrations(engine)
    return engine


def session_factory(engine: Engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


@contextmanager
def session_scope(engine: Engine):
    session = session_factory(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
