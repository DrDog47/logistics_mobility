# Mobility Payroll

Internal payroll system for a Polish trucking company.
Calculates driver salaries under the EU Mobility Package (Pakiet Mobilności)
based on tachograph + GPS data.

**Status:** Phase 0 (skeleton). See `docs/ROADMAP.md` (TBD) for upcoming phases.

## Stack

- **Backend:** Flask 3 + SQLAlchemy 2 + Marshmallow
- **Frontend:** Jinja2 templates + HTMX (no SPA, no build step)
- **DB:** SQLite (file-based)
- **Auth:** Flask-Login with bcrypt
- **i18n:** Flask-Babel (PL, EN, RU)
- **Deployment:** Docker + gunicorn

## Quick start

### Local development

```bash
# 1. Clone, create venv
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Env
cp .env.example .env
# edit .env: set SECRET_KEY (generate with: python -c "import secrets; print(secrets.token_hex(32))")

# 3. Init DB
flask --app run db init
flask --app run db migrate -m "initial"
flask --app run db upgrade

# 4. Create first admin
flask --app run create-admin --login admin --email admin@example.local --name "Administrator"

# 5. Run dev server
flask --app run run --debug
```

Open http://localhost:5000.

### Docker

```bash
# Generate SECRET_KEY first
echo "SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')" > .env

# Build and start
docker compose up -d --build

# Initialize DB (first run only)
docker compose exec app flask --app run db upgrade
docker compose exec app flask --app run create-admin --login admin --email admin@example.local --name Admin
```

Open http://localhost:8000.

## Project layout

```
mobility_payroll/
├── app/
│   ├── __init__.py          # Application factory
│   ├── config.py            # Dev/Prod/Test configs (env-driven)
│   ├── extensions.py        # Singleton extension instances
│   ├── cli.py               # Flask CLI commands
│   ├── auth/                # Login, logout, role decorators
│   ├── main/                # Dashboard, landing
│   ├── drivers/             # Driver + contract CRUD (reference module)
│   ├── models/              # Centralized model imports for Alembic
│   ├── static/css/          # Stylesheet
│   ├── templates/           # Jinja2 templates (+ HTMX fragments)
│   └── translations/        # Compiled .mo files (per locale)
├── data/                    # YAML rate files (added in Phase 1)
├── migrations/              # Alembic
├── tests/                   # pytest
├── instance/                # SQLite file lives here (mounted volume in Docker)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── babel.cfg
├── pyproject.toml
└── run.py
```

## Coding conventions

- Type hints everywhere; `from __future__ import annotations` at the top
- Snake_case modules and functions; PascalCase classes
- Routes return rendered templates or `redirect()`; HTMX fragments returned only when
  `request.headers.get("HX-Request")` is truthy
- One blueprint per major domain: `auth`, `drivers`, `vehicles` (later), `trips`, `payroll`, `tachograph`, `gps`, `reports`
- Business logic NEVER inside route functions; lives in `app/<blueprint>/services.py` or `app/<blueprint>/calculator/`
- All datetimes stored in UTC; conversion happens at the presentation layer
- All money as `decimal.Decimal`, never `float`
- All currency codes as ISO 4217; country codes as ISO 3166-1 alpha-3

## i18n workflow

```bash
# Extract translatable strings
pybabel extract -F babel.cfg -k _l -o messages.pot .

# Initialize a new locale (once per language)
pybabel init -i messages.pot -d app/translations -l pl
pybabel init -i messages.pot -d app/translations -l en
pybabel init -i messages.pot -d app/translations -l ru

# After adding strings: update existing translations
pybabel update -i messages.pot -d app/translations

# Compile (also runs in Dockerfile)
pybabel compile -d app/translations
```

## Tests

```bash
pytest
```

## Roadmap (high-level)

- [x] **Phase 0** — skeleton, auth, basic CRUD
- [x] **Phase 1** — vehicles, trips, manual segment entry, country rates YAML, basic PM calc (umowa o pracę)
- [x] **Phase 2** — virtual diet, ZUS/PIT, NBP integration, sanitariaty
- [ ] **Phase 3** — `.DDD` tachograph parser (EU 165/2014)
- [ ] **Phase 4** — GPS classification with manual override
- [ ] **Phase 5** — umowa zlecenia + B2B calculation paths
- [ ] **Phase 6** — overtime, night work, leave, sick pay
- [ ] **Phase 7** — PDF payslips, Excel exports, IMI CSV export
- [ ] **Phase 8** — audit log, notifications, polish

## License

Internal use only — not licensed for redistribution.
