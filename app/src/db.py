"""Database engine, session factory, and Base declarative class.

Provides request-scoped session via Flask's g + teardown pattern:
commits on success, rolls back on error.
"""
from contextlib import contextmanager

from flask import g
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

engine = None
SessionLocal = None


def init_db(database_url):
    """Initialise the database engine and session factory."""
    global engine, SessionLocal

    # Dispose previous engine to release connections (important for test isolation)
    if engine is not None:
        try:
            engine.dispose()
        except Exception:
            pass

    if database_url == 'sqlite://':
        # In-memory SQLite: use StaticPool so all sessions share one connection
        from sqlalchemy.pool import StaticPool
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        engine = create_engine(database_url, pool_pre_ping=True)

    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine


def get_session():
    """Get a new database session. Caller must close it."""
    if SessionLocal is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return SessionLocal()


def get_db():
    """Get request-scoped session from Flask g. Creates one if needed."""
    if 'db_session' not in g:
        g.db_session = get_session()
    return g.db_session


def close_db(exception=None):
    """Teardown: commit or rollback, then close session.

    Never re-raises — teardown errors must not kill gunicorn workers.
    """
    session = g.pop('db_session', None)
    if session is not None:
        try:
            if exception:
                session.rollback()
            else:
                try:
                    session.commit()
                except Exception:
                    session.rollback()
        except Exception:
            pass  # Teardown must not crash the worker
        finally:
            try:
                session.close()
            except Exception:
                pass


@contextmanager
def db_session_scope():
    """Context manager for non-request use (scripts, tests)."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all_tables():
    """Create all tables from model metadata. Use on fresh DB only."""
    if engine is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    Base.metadata.create_all(bind=engine)


def drop_all_tables():
    """Drop all tables. Destructive — use with caution."""
    if engine is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    Base.metadata.drop_all(bind=engine)
