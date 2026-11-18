"""Create root workspaces table with integrity checks and RLS (S1-AM01-DB / ND-168).

Revision ID: 0001_s1_am01_workspaces
Revises:
Create Date: 2026-11-18 00:30:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_s1_am01_workspaces"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create workspaces root table, trigger, restricted role, and forced RLS."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.create_table(
        "workspaces",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
        sa.UniqueConstraint("slug", name="uq_workspaces_slug"),
        sa.CheckConstraint(
            "btrim(name) <> '' AND name = btrim(name)",
            name="name_not_blank",
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug) >= 2",
            name="slug_format",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="updated_at_gte_created_at",
        ),
    )

    op.create_index("ix_workspaces_created_by", "workspaces", ["created_by"], unique=False)

    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at_timestamp()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at THEN
                NEW.updated_at = clock_timestamp();
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_workspaces_set_updated_at
        BEFORE UPDATE ON workspaces
        FOR EACH ROW
        EXECUTE FUNCTION set_updated_at_timestamp();
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app_user') THEN
                CREATE ROLE nexus_app_user
                    NOLOGIN
                    NOSUPERUSER
                    NOCREATEDB
                    NOCREATEROLE
                    NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )

    op.execute("GRANT USAGE ON SCHEMA public TO nexus_app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE workspaces TO nexus_app_user;")

    op.execute("ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE workspaces FORCE ROW LEVEL SECURITY;")

    op.execute(
        """
        CREATE POLICY workspaces_tenant_isolation_select ON workspaces
            FOR SELECT
            USING (
                nullif(current_setting('app.current_workspace_id', true), '') IS NOT NULL
                AND id = nullif(current_setting('app.current_workspace_id', true), '')::uuid
            );
        """
    )
    op.execute(
        """
        CREATE POLICY workspaces_tenant_isolation_insert ON workspaces
            FOR INSERT
            WITH CHECK (
                nullif(current_setting('app.current_workspace_id', true), '') IS NOT NULL
                AND id = nullif(current_setting('app.current_workspace_id', true), '')::uuid
            );
        """
    )
    op.execute(
        """
        CREATE POLICY workspaces_tenant_isolation_update ON workspaces
            FOR UPDATE
            USING (
                nullif(current_setting('app.current_workspace_id', true), '') IS NOT NULL
                AND id = nullif(current_setting('app.current_workspace_id', true), '')::uuid
            )
            WITH CHECK (
                nullif(current_setting('app.current_workspace_id', true), '') IS NOT NULL
                AND id = nullif(current_setting('app.current_workspace_id', true), '')::uuid
            );
        """
    )
    op.execute(
        """
        CREATE POLICY workspaces_tenant_isolation_delete ON workspaces
            FOR DELETE
            USING (
                nullif(current_setting('app.current_workspace_id', true), '') IS NOT NULL
                AND id = nullif(current_setting('app.current_workspace_id', true), '')::uuid
            );
        """
    )


def downgrade() -> None:
    """Reverse workspaces RLS policies, trigger, privileges, index, and table."""
    op.execute("DROP POLICY IF EXISTS workspaces_tenant_isolation_delete ON workspaces;")
    op.execute("DROP POLICY IF EXISTS workspaces_tenant_isolation_update ON workspaces;")
    op.execute("DROP POLICY IF EXISTS workspaces_tenant_isolation_insert ON workspaces;")
    op.execute("DROP POLICY IF EXISTS workspaces_tenant_isolation_select ON workspaces;")

    op.execute("ALTER TABLE workspaces NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE workspaces DISABLE ROW LEVEL SECURITY;")

    op.execute("REVOKE ALL PRIVILEGES ON TABLE workspaces FROM nexus_app_user;")

    op.execute("DROP TRIGGER IF EXISTS trg_workspaces_set_updated_at ON workspaces;")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at_timestamp();")

    op.drop_index("ix_workspaces_created_by", table_name="workspaces")
    op.drop_table("workspaces")
