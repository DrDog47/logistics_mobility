# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Internal payroll system for a Polish trucking company. It calculates driver salaries under the
EU Mobility Package (Pakiet Mobilności). Flask 3 + SQLAlchemy 2 + Jinja2/HTMX, server-rendered,
no SPA and no JS build step. UI strings are i18n'd in PL / EN / RU via Flask-Babel.

The codebase is being rebuilt around a PRD-defined schema (see `PRD/`). The README is partly stale
(it predates the Postgres switch and the document-system refactor) — trust the code and `PRD/` over it.

## Commands

Tests require `SECRET_KEY` to be set (any value) and run against in-memory SQLite, so no DB is needed:

```bash
SECRET_KEY=test .venv/bin/pytest                       # full suite
SECRET_KEY=test .venv/bin/pytest -x                     # stop on first failure
SECRET_KEY=test .venv/bin/pytest tests/test_payroll_calculator.py::test_name   # single test
SECRET_KEY=test .venv/bin/pytest --ignore=tests/test_nbp.py   # skip NBP tests (they hit the live NBP API)
```

Lint / format (ruff, configured in `pyproject.toml`, line-length 100, py314):

```bash
.venv/bin/ruff check .
.venv/bin/ruff format .
```

Run the dev server (needs a running Postgres — see Docker below — or override `DATABASE_URL`):

```bash
SECRET_KEY=dev .venv/bin/flask --app run run --debug
```

Database & migrations (Flask-Migrate / Alembic; `flask --app run`):

```bash
flask --app run db migrate -m "message"   # autogenerate from models
flask --app run db upgrade                 # apply
flask --app run create-admin --login admin --email a@b.c --name Admin
flask --app run seed-document-types        # populate document_type catalogue
flask --app run seed-org                   # create starter organisation (drivers/vehicles need one)
```

Local Postgres for development:

```bash
docker compose up -d db    # Postgres 16 on localhost:5432, db/user/pass all "mobility"
```

i18n (after adding `_()` / `_l()` strings):

```bash
pybabel extract -F babel.cfg -k _l -o messages.pot .
pybabel update -i messages.pot -d app/translations
pybabel compile -d app/translations
```

## Architecture

**App factory.** `app/__init__.py:create_app()` wires everything. Order matters: extensions, then the
rate registry and Polish tax params are loaded into `app.extensions` / `app.config` at startup (the app
*refuses to boot* if the YAML data files are malformed), then blueprints, locale selector, template
globals, healthcheck, CLI. Config classes live in `app/config.py` and are selected by `FLASK_ENV`
(`development` / `production` / `testing`).

**Database is dual-target.** Production and dev use **PostgreSQL** (`postgresql+psycopg://`, psycopg3);
tests use **in-memory SQLite**. `app/db_types.py` is the single source of truth that makes models work on
both: `UuidType` (native `UUID` on PG, `CHAR(32)` on SQLite), `JsonB` (JSONB vs JSON), and
`PrdStandardMixin`. Never import `app.extensions.db` into `db_types.py` (circular import). Server-side DDL
that only exists on Postgres (e.g. `gen_random_uuid()` defaults) belongs in the Alembic migration, not the
model — it would break SQLite `create_all`.

**PRD schema conventions.** Tables inheriting `PrdStandardMixin` get a `uuid` PK (Python-side
`default=uuid.uuid4`), `created_at`, `deleted_at`, `is_deleted`. **Soft delete is the rule** — rows are kept;
every query must filter `.where(Model.is_deleted.is_(False))`. WTForms SelectFields backed by UUIDs should
use `uuid_or_none` as the `coerce`.

**Models must be registered for Alembic.** `app/models/__init__.py` imports every model so autogenerate can
see them. If you add a model and its table doesn't appear in a migration, it's missing from this file. Note
**payroll is deliberately parked**: `PayrollPeriod` / `PayrollLine` are *not* imported here and the payroll
blueprint is *not* registered in the app factory, while the document system is built out. Re-enable both
together when resuming payroll work.

**Blueprints = domains.** One blueprint per domain under `app/<domain>/` (auth, main, organisations, drivers,
vehicles, documents, trips, rates; payroll parked). Each typically has `routes.py`, `models.py`, `forms.py`,
and `services.py`. **Business logic never lives in route functions** — it goes in `services.py` or, for
payroll, `app/payroll/calculator/`. HTMX fragments are returned only when `request.headers.get("HX-Request")`
is set; otherwise return a full template or `redirect()`.

**Rates & audit.** Country wage rates are YAML in `data/country_rates/*.yaml`, loaded into an immutable
`RateRegistry` at startup. Payroll code must look up rates via
`app.rates.services.resolve_rate_with_snapshot()` — it persists a `CountryRateSnapshot` recording exactly
which rate/verification was used, which is what makes a calculation reproducible/auditable. Don't bypass it.
Polish tax/ZUS/PIT parameters live in `data/tax_rules/pl_<year>.yaml`, loaded per-year at startup.

**Payroll calculators.** `app/payroll/calculator/base.py:calculate()` dispatches on the driver's active
contract type (`UMOWA_O_PRACE` is implemented; `UMOWA_ZLECENIA` and `B2B` raise — slated for Phase 5).
`CALCULATOR_VERSION` is stamped onto every calculated period and **must be bumped whenever calc logic
changes** so historical results stay auditable.

**Documents.** `app/documents/status.py:document_status()` is a pure (no-DB) function registered as a Jinja
global so templates render expiry badges directly; insurance uses a tighter 60/30/15-day scale than the
generic 120/60 scale. The `document_type` catalogue is DB-backed but falls back to
`app/documents/constants.py:BASE_DOCUMENT_TYPES` on a fresh/empty schema.

## Conventions

- `from __future__ import annotations` at the top of every module; type hints everywhere.
- Money is `decimal.Decimal`, never `float`. Datetimes stored in UTC, converted at the presentation layer.
- Currency codes ISO 4217; country codes per the data files (ISO 3166-1 — note rate YAML keys use alpha-2).
- snake_case modules/functions, PascalCase classes.
