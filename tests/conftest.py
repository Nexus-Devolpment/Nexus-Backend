"""Pytest fixtures for AM-01 PostgreSQL database verification."""

from collections.abc import Iterator
from pathlib import Path

from alembic.config import Config
from alembic import command
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import create_db_engine, create_session_factory, get_database_url

REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI_PATH = REPO_ROOT / "alembic.ini"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Return the disposable local PostgreSQL test database URL."""
    return get_database_url(test=True)


@pytest.fixture(scope="session")
def alembic_cfg(test_database_url: str) -> Config:
    """Build an Alembic Config bound strictly to the disposable test database."""
    cfg = Config(str(ALEMBIC_INI_PATH))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", test_database_url)
    return cfg


@pytest.fixture(scope="session")
def db_engine(test_database_url: str) -> Iterator[Engine]:
    """Create a session-scoped SQLAlchemy Engine for the disposable test DB."""
    engine = create_db_engine(test_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    """Return a SQLAlchemy Session factory for the disposable test DB."""
    return create_session_factory(db_engine)


@pytest.fixture()
def migrated_clean_db(alembic_cfg: Config, db_engine: Engine) -> Iterator[Engine]:
    """Ensure schema is migrated to head and the workspaces table is empty."""
    command.upgrade(alembic_cfg, "head")
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE workspaces RESTART IDENTITY CASCADE"))
    try:
        yield db_engine
    finally:
        with db_engine.begin() as conn:
            conn.execute(text("TRUNCATE TABLE workspaces RESTART IDENTITY CASCADE"))
