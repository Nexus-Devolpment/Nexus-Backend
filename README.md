# Nexus-Backend

FlowImmunity Nexus multi-tenant backend repository (PostgreSQL database layer for `AM-01` / `S1-AM01-DB` / Jira `ND-168`).

## Prerequisites

- Python 3.11+ (tested with Python 3.12 via `uv`)
- PostgreSQL 16 (disposable local instance or Docker container for development/testing)

## Setup

1. Create a virtual environment and install dependencies:
   ```powershell
   uv venv --python 3.12
   uv pip install -e ".[dev]"
   ```
2. Copy `.env.example` to `.env` for local disposable usage (never commit `.env`):
   ```powershell
   Copy-Item .env.example .env
   ```

## Database Migrations (Alembic)

Set `DATABASE_URL` (pointing only to your disposable local database) and run:

```powershell
# Apply migrations forward
.venv\Scripts\alembic upgrade head

# Roll back migrations
.venv\Scripts\alembic downgrade base
```

## Synthetic Development Seed Fixtures

To populate two clearly labelled synthetic test workspaces (`Apex BioLogistics (Synthetic Dev)` and `Beacon ColdChain (Synthetic Dev)`):

```powershell
.venv\Scripts\python scripts\seed_dev_workspaces.py
```

The seed script explicitly blocks execution if `ENVIRONMENT` is `production` or `prod`.

## Database Verification Tests

Run the PostgreSQL verification suite against a disposable `TEST_DATABASE_URL`:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/flowimmunity_test"
.venv\Scripts\pytest -v -s
```

See [docs/db/am01_workspaces_schema.md](docs/db/am01_workspaces_schema.md) for the full data dictionary, ERD, Option A tenant-root rationale, external provenance pattern, and RLS boundary documentation.
