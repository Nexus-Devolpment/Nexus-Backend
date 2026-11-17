"""Database engine, session factory, and transaction-scoped tenant context."""

from collections.abc import Iterator
from contextlib import contextmanager
import os
import uuid

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

RESTRICTED_DB_ROLE = "nexus_app_user"
DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/flowimmunity_dev"
DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/flowimmunity_test"


def get_database_url(test: bool = False) -> str:
    """Return the PostgreSQL connection URL from the environment or local defaults."""
    if test:
        return os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def create_db_engine(database_url: str | None = None, *, test: bool = False) -> Engine:
    """Create a SQLAlchemy Engine with pre-ping enabled."""
    url = database_url or get_database_url(test=test)
    return create_engine(url, pool_pre_ping=True, future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a SQLAlchemy Session factory bound to the provided engine."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def set_tenant_context(
    conn_or_session: Connection | Session,
    workspace_id: uuid.UUID | str | None,
    use_restricted_role: bool = False,
) -> None:
    """Bind transaction-local PostgreSQL tenant context and optional restricted role.

    Uses ``set_config(..., is_local=true)`` so the setting automatically resets
    when the current database transaction ends, preventing pool context leakage.
    """
    ws_value = "" if workspace_id is None else str(uuid.UUID(str(workspace_id)))

    if use_restricted_role:
        conn_or_session.execute(text(f"SET LOCAL ROLE {RESTRICTED_DB_ROLE}"))

    conn_or_session.execute(
        text("SELECT set_config('app.current_workspace_id', :ws_id, true)"),
        {"ws_id": ws_value},
    )


@contextmanager
def tenant_transaction(
    session: Session,
    workspace_id: uuid.UUID | str | None,
    use_restricted_role: bool = True,
) -> Iterator[Session]:
    """Run a database transaction with explicit transaction-local tenant scope.

    Raises RuntimeError if ``session`` already has an open transaction to avoid
    inheriting or leaking tenant context across boundaries.
    """
    if session.in_transaction():
        raise RuntimeError(
            "Session already has an active transaction; pass a clean Session to tenant_transaction."
        )

    with session.begin():
        set_tenant_context(
            session,
            workspace_id=workspace_id,
            use_restricted_role=use_restricted_role,
        )
        yield session
