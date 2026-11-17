"""Database-level verification suite for S1-AM01-DB / Jira ND-168."""

from __future__ import annotations

import time
import uuid

from alembic import command
from alembic.config import Config
import psycopg.errors
import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import RESTRICTED_DB_ROLE, set_tenant_context, tenant_transaction
from app.models.workspace import Workspace
from scripts.seed_dev_workspaces import (
    SYNTHETIC_WORKSPACES,
    assert_non_production_environment,
    seed_synthetic_workspaces,
)


def test_a_migration_up_inspect_down_up(alembic_cfg: Config, db_engine: Engine) -> None:
    """Verification A & E: migrate up -> inspect constraints/indexes/RLS -> down -> up."""
    command.downgrade(alembic_cfg, "base")
    with db_engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE IF NOT EXISTS _preexisting_sentinel (id INTEGER PRIMARY KEY)")
        )
        conn.execute(
            text("INSERT INTO _preexisting_sentinel (id) VALUES (42) ON CONFLICT DO NOTHING")
        )
    try:
        command.upgrade(alembic_cfg, "head")

        with db_engine.connect() as conn:
            columns = {
                row.column_name: (
                    row.data_type,
                    row.character_maximum_length,
                    row.is_nullable,
                    row.column_default,
                )
                for row in conn.execute(
                    text(
                        """
                        SELECT column_name, data_type, character_maximum_length,
                               is_nullable, column_default
                        FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'workspaces'
                        """
                    )
                )
            }
            assert set(columns.keys()) == {
                "id",
                "name",
                "slug",
                "created_at",
                "updated_at",
                "created_by",
            }
            assert columns["id"][0] == "uuid"
            assert columns["id"][2] == "NO"
            assert "gen_random_uuid()" in (columns["id"][3] or "")
            assert columns["name"][:3] == ("character varying", 255, "NO")
            assert columns["slug"][:3] == ("character varying", 63, "NO")
            assert columns["created_at"][0] == "timestamp with time zone"
            assert columns["created_at"][2] == "NO"
            assert columns["updated_at"][0] == "timestamp with time zone"
            assert columns["updated_at"][2] == "NO"
            assert columns["created_by"][0] == "uuid"
            assert columns["created_by"][2] == "NO"

            constraint_names = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT conname
                        FROM pg_constraint
                        WHERE conrelid = 'public.workspaces'::regclass
                        """
                    )
                )
            }
            assert {
                "pk_workspaces",
                "uq_workspaces_slug",
                "ck_workspaces_name_not_blank",
                "ck_workspaces_slug_format",
                "ck_workspaces_updated_at_gte_created_at",
            }.issubset(constraint_names)

            index_names = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT indexname
                        FROM pg_indexes
                        WHERE schemaname = 'public' AND tablename = 'workspaces'
                        """
                    )
                )
            }
            assert index_names == {
                "pk_workspaces",
                "uq_workspaces_slug",
                "ix_workspaces_created_by",
            }

            rls_flags = conn.execute(
                text(
                    """
                    SELECT relrowsecurity, relforcerowsecurity
                    FROM pg_class
                    WHERE oid = 'public.workspaces'::regclass
                    """
                )
            ).one()
            assert rls_flags.relrowsecurity is True
            assert rls_flags.relforcerowsecurity is True

            policy_names = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT policyname
                        FROM pg_policies
                        WHERE schemaname = 'public' AND tablename = 'workspaces'
                        """
                    )
                )
            }
            assert policy_names == {
                "workspaces_tenant_isolation_select",
                "workspaces_tenant_isolation_insert",
                "workspaces_tenant_isolation_update",
                "workspaces_tenant_isolation_delete",
            }

            role_flags = conn.execute(
                text(
                    """
                    SELECT rolsuper, rolbypassrls, rolcanlogin
                    FROM pg_roles
                    WHERE rolname = :role_name
                    """
                ),
                {"role_name": RESTRICTED_DB_ROLE},
            ).one()
            assert role_flags.rolsuper is False
            assert role_flags.rolbypassrls is False
            assert role_flags.rolcanlogin is False

        # Verify reversible downgrade cleanly removes objects without damaging preexisting schema.
        command.downgrade(alembic_cfg, "base")
        with db_engine.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('public.workspaces')")).scalar_one() is None
            assert (
                conn.execute(text("SELECT to_regprocedure('set_updated_at_timestamp()')")).scalar_one()
                is None
            )
            assert (
                conn.execute(text("SELECT id FROM _preexisting_sentinel")).scalar_one() == 42
            )

        # Verify re-upgrade restores table and forced RLS.
        command.upgrade(alembic_cfg, "head")
        with db_engine.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('public.workspaces')")).scalar_one() is not None
            rls_flags_after = conn.execute(
                text(
                    """
                    SELECT relrowsecurity, relforcerowsecurity
                    FROM pg_class
                    WHERE oid = 'public.workspaces'::regclass
                    """
                )
            ).one()
            assert rls_flags_after.relrowsecurity is True
            assert rls_flags_after.relforcerowsecurity is True
    finally:
        with db_engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS _preexisting_sentinel"))


def test_b_valid_workspace_storage_and_synthetic_seeds(
    migrated_clean_db: Engine,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verification B: store valid records, synthetic seeds, non-unique display names, and trigger."""
    seeded = seed_synthetic_workspaces(session_factory)
    assert len(seeded) == 2
    assert seeded[0].id != seeded[1].id
    assert {w.id for w in seeded} == {spec["id"] for spec in SYNTHETIC_WORKSPACES}
    for ws in seeded:
        assert ws.created_at.tzinfo is not None
        assert ws.updated_at.tzinfo is not None
        assert ws.updated_at >= ws.created_at

    # Idempotent re-run succeeds without duplicating rows.
    re_seeded = seed_synthetic_workspaces(session_factory)
    assert [w.id for w in re_seeded] == [w.id for w in seeded]

    # Production safeguard blocks synthetic seed fixtures.
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="production"):
        assert_non_production_environment()
    monkeypatch.setenv("ENVIRONMENT", "test")

    # Two distinct companies can share the same display name as long as id/slug differ.
    shared_display_name = "Acme Supply Partners"
    ws1_id = uuid.uuid4()
    ws2_id = uuid.uuid4()
    admin_id = uuid.uuid4()

    with session_factory() as session:
        with tenant_transaction(session, ws1_id, use_restricted_role=True):
            ws1 = Workspace(
                id=ws1_id,
                name=shared_display_name,
                slug="acme-supply-north",
                created_by=admin_id,
            )
            session.add(ws1)
            session.flush()
            initial_updated_at = ws1.updated_at

    with session_factory() as session:
        with tenant_transaction(session, ws2_id, use_restricted_role=True):
            ws2 = Workspace(
                id=ws2_id,
                name=shared_display_name,
                slug="acme-supply-south",
                created_by=admin_id,
            )
            session.add(ws2)

    time.sleep(0.02)

    with session_factory() as session:
        with tenant_transaction(session, ws1_id, use_restricted_role=True):
            loaded = session.get(Workspace, ws1_id)
            assert loaded is not None
            loaded.name = "Acme Supply Partners North"
            session.flush()
            assert loaded.updated_at > initial_updated_at


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "   ",
        "\t\n ",
        " Leading Space",
        "Trailing Space ",
    ],
)
def test_c_rejects_blank_or_untrimmed_name(
    migrated_clean_db: Engine,
    session_factory: sessionmaker[Session],
    bad_name: str,
) -> None:
    """Verification C1: blank or untrimmed company names violate PostgreSQL check constraint."""
    ws_id = uuid.uuid4()
    with pytest.raises(IntegrityError) as exc_info:
        with session_factory() as session:
            with tenant_transaction(session, ws_id, use_restricted_role=True):
                session.add(
                    Workspace(
                        id=ws_id,
                        name=bad_name,
                        slug=f"slug-{ws_id.hex[:8]}",
                        created_by=uuid.uuid4(),
                    )
                )
    assert isinstance(exc_info.value.orig, psycopg.errors.CheckViolation)


@pytest.mark.parametrize(
    "bad_slug",
    [
        "",
        "a",
        "   ",
        "Uppercase-Slug",
        "under_score",
        "-leading-hyphen",
        "trailing-hyphen-",
        "double--hyphen",
    ],
)
def test_c_rejects_invalid_slug_format(
    migrated_clean_db: Engine,
    session_factory: sessionmaker[Session],
    bad_slug: str,
) -> None:
    """Verification C2: malformed machine slug violates PostgreSQL check constraint."""
    ws_id = uuid.uuid4()
    with pytest.raises(IntegrityError) as exc_info:
        with session_factory() as session:
            with tenant_transaction(session, ws_id, use_restricted_role=True):
                session.add(
                    Workspace(
                        id=ws_id,
                        name="Valid Company Name",
                        slug=bad_slug,
                        created_by=uuid.uuid4(),
                    )
                )
    assert isinstance(exc_info.value.orig, psycopg.errors.CheckViolation)


def test_c_rejects_nulls_duplicates_bad_timestamps_and_bad_child_fk(
    migrated_clean_db: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    """Verification C3: NULL fields, duplicate slug, updated_at < created_at, and bad FK fail."""
    # NULL required columns
    for col in ("name", "slug", "created_by"):
        ws_id = uuid.uuid4()
        values = {
            "id": ws_id,
            "name": "Null Test Org",
            "slug": f"null-test-{ws_id.hex[:6]}",
            "created_by": uuid.uuid4(),
        }
        values[col] = None  # type: ignore[assignment]

        with pytest.raises(IntegrityError) as exc_info:
            with session_factory() as session:
                with tenant_transaction(session, ws_id, use_restricted_role=True):
                    session.execute(
                        text(
                            """
                            INSERT INTO workspaces (id, name, slug, created_by)
                            VALUES (:id, :name, :slug, :created_by)
                            """
                        ),
                        values,
                    )
        assert isinstance(exc_info.value.orig, psycopg.errors.NotNullViolation)

    # Duplicate slug
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    with session_factory() as session:
        with tenant_transaction(session, first_id, use_restricted_role=True):
            session.add(
                Workspace(
                    id=first_id,
                    name="Org One",
                    slug="shared-machine-slug",
                    created_by=uuid.uuid4(),
                )
            )

    with pytest.raises(IntegrityError) as exc_info:
        with session_factory() as session:
            with tenant_transaction(session, second_id, use_restricted_role=True):
                session.add(
                    Workspace(
                        id=second_id,
                        name="Org Two",
                        slug="shared-machine-slug",
                        created_by=uuid.uuid4(),
                    )
                )
    assert isinstance(exc_info.value.orig, psycopg.errors.UniqueViolation)

    # updated_at < created_at
    bad_ts_id = uuid.uuid4()
    with pytest.raises(IntegrityError) as exc_info:
        with session_factory() as session:
            with tenant_transaction(session, bad_ts_id, use_restricted_role=True):
                session.execute(
                    text(
                        """
                        INSERT INTO workspaces (id, name, slug, created_at, updated_at, created_by)
                        VALUES (
                            :id,
                            'Timestamp Check Org',
                            'ts-check-org',
                            '2026-11-18T12:00:00Z',
                            '2026-11-18T11:59:59Z',
                            :created_by
                        )
                        """
                    ),
                    {"id": bad_ts_id, "created_by": uuid.uuid4()},
                )
    assert isinstance(exc_info.value.orig, psycopg.errors.CheckViolation)

    # Nonexistent workspace foreign key on a child table (permanent table required for FK in PG)
    with migrated_clean_db.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE _test_child_entity (
                    id UUID PRIMARY KEY,
                    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT
                )
                """
            )
        )
    try:
        with migrated_clean_db.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO _test_child_entity (id, workspace_id)
                    VALUES (:id, :valid_ws_id)
                    """
                ),
                {"id": uuid.uuid4(), "valid_ws_id": first_id},
            )

        with pytest.raises(IntegrityError) as exc_info:
            with migrated_clean_db.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO _test_child_entity (id, workspace_id)
                        VALUES (:id, :missing_ws_id)
                        """
                    ),
                    {"id": uuid.uuid4(), "missing_ws_id": uuid.uuid4()},
                )
        assert isinstance(exc_info.value.orig, psycopg.errors.ForeignKeyViolation)
    finally:
        with migrated_clean_db.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS _test_child_entity"))


def test_d_rls_tenant_isolation_and_uncontextualized_denial(
    migrated_clean_db: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    """Verification D: forced RLS enforces cross-tenant and no-context isolation."""
    tenant_a_id = uuid.uuid4()
    tenant_b_id = uuid.uuid4()
    admin_a_id = uuid.uuid4()
    admin_b_id = uuid.uuid4()

    # Onboard Tenant A and Tenant B in separate server-assigned onboarding contexts.
    with session_factory() as session:
        with tenant_transaction(session, tenant_a_id, use_restricted_role=True):
            session.add(
                Workspace(
                    id=tenant_a_id,
                    name="Tenant Alpha Bio",
                    slug="tenant-alpha-bio",
                    created_by=admin_a_id,
                )
            )

    with session_factory() as session:
        with tenant_transaction(session, tenant_b_id, use_restricted_role=True):
            session.add(
                Workspace(
                    id=tenant_b_id,
                    name="Tenant Beta Med",
                    slug="tenant-beta-med",
                    created_by=admin_b_id,
                )
            )

    # Tenant A context cannot see, update, delete, or insert for Tenant B.
    with session_factory() as session:
        with tenant_transaction(session, tenant_a_id, use_restricted_role=True):
            visible_ids = list(session.scalars(select(Workspace.id)))
            assert visible_ids == [tenant_a_id]
            assert session.get(Workspace, tenant_b_id) is None

            update_res = session.execute(
                text("UPDATE workspaces SET name = 'Tampered By A' WHERE id = :ws_b"),
                {"ws_b": tenant_b_id},
            )
            assert update_res.rowcount == 0

            delete_res = session.execute(
                text("DELETE FROM workspaces WHERE id = :ws_b"),
                {"ws_b": tenant_b_id},
            )
            assert delete_res.rowcount == 0

    with pytest.raises(ProgrammingError) as exc_info:
        with session_factory() as session:
            with tenant_transaction(session, tenant_a_id, use_restricted_role=True):
                session.add(
                    Workspace(
                        id=uuid.uuid4(),
                        name="Spoofed Cross Tenant",
                        slug="spoofed-cross-tenant",
                        created_by=admin_a_id,
                    )
                )
    assert isinstance(exc_info.value.orig, psycopg.errors.InsufficientPrivilege)

    # Tenant B context sees only Tenant B and confirms Tenant A could not mutate it.
    with session_factory() as session:
        with tenant_transaction(session, tenant_b_id, use_restricted_role=True):
            rows = list(session.scalars(select(Workspace)))
            assert len(rows) == 1
            assert rows[0].id == tenant_b_id
            assert rows[0].name == "Tenant Beta Med"
            assert session.get(Workspace, tenant_a_id) is None

    # Empty tenant context reveals zero rows and blocks writes.
    with session_factory() as session:
        with tenant_transaction(session, None, use_restricted_role=True):
            assert list(session.scalars(select(Workspace))) == []
            assert (
                session.execute(
                    text("UPDATE workspaces SET name = 'No Context' WHERE id = :ws_a"),
                    {"ws_a": tenant_a_id},
                ).rowcount
                == 0
            )
            assert (
                session.execute(
                    text("DELETE FROM workspaces WHERE id = :ws_a"),
                    {"ws_a": tenant_a_id},
                ).rowcount
                == 0
            )

    with pytest.raises(ProgrammingError) as exc_info:
        with session_factory() as session:
            with tenant_transaction(session, None, use_restricted_role=True):
                session.add(
                    Workspace(
                        id=uuid.uuid4(),
                        name="Uncontextualized Workspace",
                        slug="uncontextualized-ws",
                        created_by=uuid.uuid4(),
                    )
                )
    assert isinstance(exc_info.value.orig, psycopg.errors.InsufficientPrivilege)

    # Completely unset GUC (never initialized in transaction) also reveals zero rows.
    with session_factory() as session:
        with session.begin():
            session.execute(text(f"SET LOCAL ROLE {RESTRICTED_DB_ROLE}"))
            assert list(session.scalars(select(Workspace))) == []

    # Invalid UUID strings are rejected in Python helper AND fail closed in SQL.
    with pytest.raises(ValueError):
        with session_factory() as session:
            with session.begin():
                set_tenant_context(session, "not-a-uuid", use_restricted_role=True)

    with pytest.raises(DataError) as exc_info:
        with session_factory() as session:
            with session.begin():
                session.execute(text(f"SET LOCAL ROLE {RESTRICTED_DB_ROLE}"))
                session.execute(
                    text("SELECT set_config('app.current_workspace_id', 'not-a-uuid', true)")
                )
                list(session.scalars(select(Workspace)))
    assert isinstance(exc_info.value.orig, psycopg.errors.InvalidTextRepresentation)
