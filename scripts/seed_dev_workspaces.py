"""SYNTHETIC DEVELOPMENT/TEST FIXTURES ONLY — NEVER RUN IN PRODUCTION.

Seeds two clearly labelled synthetic company workspaces with distinct UUIDs
using transaction-scoped RLS onboarding contexts.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import TypedDict
import uuid

from sqlalchemy.orm import Session, sessionmaker

# Ensure repo root is importable when invoked directly as `python scripts/seed_dev_workspaces.py`.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.db.session import create_db_engine, create_session_factory, tenant_transaction
from app.models.workspace import Workspace


class SyntheticWorkspaceSpec(TypedDict):
    """Typed synthetic seed specification for disposable dev/test environments."""

    id: uuid.UUID
    name: str
    slug: str
    created_by: uuid.UUID


SYNTHETIC_WORKSPACES: tuple[SyntheticWorkspaceSpec, ...] = (
    {
        "id": uuid.UUID("11111111-1111-4111-8111-111111111111"),
        "name": "Apex BioLogistics (Synthetic Dev)",
        "slug": "apex-biologistics-dev",
        "created_by": uuid.UUID("aaaa1111-1111-4111-8111-111111111111"),
    },
    {
        "id": uuid.UUID("22222222-2222-4222-8222-222222222222"),
        "name": "Beacon ColdChain (Synthetic Dev)",
        "slug": "beacon-coldchain-dev",
        "created_by": uuid.UUID("bbbb2222-2222-4222-8222-222222222222"),
    },
)


def assert_non_production_environment() -> str:
    """Block fixture seeding whenever ENVIRONMENT indicates production."""
    env = os.getenv("ENVIRONMENT", "development").strip().lower()
    if env in {"production", "prod"}:
        raise RuntimeError(
            "Refusing to run synthetic workspace seed fixtures in a production environment."
        )
    return env


def seed_synthetic_workspaces(session_factory: sessionmaker[Session]) -> list[Workspace]:
    """Idempotently seed synthetic workspaces, each under its own RLS tenant transaction."""
    assert_non_production_environment()
    seeded: list[Workspace] = []

    for spec in SYNTHETIC_WORKSPACES:
        with session_factory() as session:
            with tenant_transaction(session, spec["id"], use_restricted_role=True):
                workspace = session.get(Workspace, spec["id"])
                if workspace is None:
                    workspace = Workspace(
                        id=spec["id"],
                        name=spec["name"],
                        slug=spec["slug"],
                        created_by=spec["created_by"],
                    )
                    session.add(workspace)
                    session.flush()
                seeded.append(workspace)

    return seeded


def main() -> int:
    """CLI entrypoint for seeding a disposable local development or test database."""
    env = assert_non_production_environment()
    use_test_db = env == "test"
    engine = create_db_engine(test=use_test_db)
    try:
        session_factory = create_session_factory(engine)
        workspaces = seed_synthetic_workspaces(session_factory)
        for ws in workspaces:
            print(f"Seeded synthetic workspace: id={ws.id} slug={ws.slug} name={ws.name!r}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
