"""Application configuration.

Three configs: Development, Production, Testing. Selected via FLASK_ENV env var.
Secrets MUST come from environment, never hardcoded.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file if present (no-op in production where vars come from compose)
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Config:
    """Base configuration shared by all environments."""

    # Core
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "")

    # Database — PostgreSQL (psycopg3 driver). Override with DATABASE_URL.
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://mobility:mobility@localhost:5432/mobility",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    SQLALCHEMY_ENGINE_OPTIONS: dict = {
        "pool_pre_ping": True,
    }

    # i18n
    DEFAULT_LANGUAGE: str = os.environ.get("DEFAULT_LANGUAGE", "pl")
    LANGUAGES: list[str] = ["pl", "en", "ru"]
    BABEL_DEFAULT_LOCALE: str = "pl"
    BABEL_TRANSLATION_DIRECTORIES: str = "translations"

    # External APIs
    NBP_API_BASE: str = os.environ.get("NBP_API_BASE", "https://api.nbp.pl/api")

    # Vacations / leave. Default annual-leave cap (PL: 20 or 26 by seniority); set
    # per driver via the entitlement form. Google Calendar sync is optional — when
    # client credentials are unset the feature runs in manual-only mode.
    DEFAULT_ANNUAL_LEAVE_DAYS: int = int(os.environ.get("DEFAULT_ANNUAL_LEAVE_DAYS", "26"))
    GOOGLE_CLIENT_ID: str = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_CALENDAR_ID: str = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
    # Public/shared calendar address embedded as the month view on the fleet
    # vacation page (usually the same calendar leaves are pushed to). Empty =
    # embed not configured.
    GOOGLE_CALENDAR_EMBED_ID: str = os.environ.get("GOOGLE_CALENDAR_EMBED_ID", "")
    GOOGLE_CALENDAR_TZ: str = os.environ.get("GOOGLE_CALENDAR_TZ", "Europe/Warsaw")

    # CSRF — global registered (templates can call csrf_token()), enforcement
    # deferred until AJAX POSTs are wired to send the token.
    WTF_CSRF_ENABLED: bool = False

    # Session
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    PERMANENT_SESSION_LIFETIME: int = 60 * 60 * 8  # 8 hours

    # Data paths
    DATA_DIR: Path = PROJECT_ROOT / "data"
    COUNTRY_RATES_DIR: Path = DATA_DIR / "country_rates"
    TAX_RULES_DIR: Path = DATA_DIR / "tax_rules"
    # Driver document requirements ruleset (PRD §11–12) — git-versioned YAML
    # loaded into an immutable registry at startup; a malformed file refuses boot.
    DOCUMENT_REQUIREMENTS_DIR: Path = DATA_DIR / "document_requirements"
    DRIVER_REQUIREMENTS_FILE: Path = DATA_DIR / "document_requirements" / "driver.yaml"
    VEHICLE_REQUIREMENTS_FILE: Path = DATA_DIR / "document_requirements" / "vehicle.yaml"

    # Document storage (PRD §8.6). Root of the on-disk document tree — in
    # production this is an external folder mounted into the container. Override
    # with DOCUMENTS_DIR. Uploaded packages land in the inbox subfolder first
    # (two-phase: upload now, recognise/sort later).
    DOCUMENTS_DIR: Path = Path(
        os.environ.get("DOCUMENTS_DIR", str(PROJECT_ROOT / "documents"))
    )
    DOCUMENTS_INBOX_DIRNAME: str = os.environ.get("DOCUMENTS_INBOX_DIRNAME", "_Inbox")
    # Accepted upload extensions (lower-case, no dot). Content type is not
    # trusted — only the extension is checked at the upload boundary.
    DOCUMENTS_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
        {"pdf", "jpg", "jpeg", "png"}
    )

    # Document recognition (PRD §8.7). Recognition runs in two swappable stages:
    # (1) identification — classify the document type, on a cheap/fast model;
    # (2) extraction — read the fields for that type, on a stronger model.
    # Each stage picks its provider independently ("fake" parses filenames
    # offline; "claude" uses the Claude API — needs ANTHROPIC_API_KEY and the
    # `anthropic` package). A local model can be added later as another adapter.
    #
    # DOCUMENT_RECOGNIZER is the provider fallback when a stage's own provider is
    # unset, so a single `DOCUMENT_RECOGNIZER=claude` still wires both stages.
    DOCUMENT_RECOGNIZER: str = os.environ.get("DOCUMENT_RECOGNIZER", "fake")
    DOCUMENT_IDENTIFIER: str = os.environ.get("DOCUMENT_IDENTIFIER", "")
    DOCUMENT_IDENTIFIER_MODEL: str = os.environ.get(
        "DOCUMENT_IDENTIFIER_MODEL", "claude-haiku-4-5"
    )
    DOCUMENT_EXTRACTOR: str = os.environ.get("DOCUMENT_EXTRACTOR", "")
    DOCUMENT_EXTRACTOR_MODEL: str = os.environ.get(
        "DOCUMENT_EXTRACTOR_MODEL", "claude-sonnet-4-6"
    )
    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")


class DevelopmentConfig(Config):
    DEBUG: bool = True
    TEMPLATES_AUTO_RELOAD: bool = True
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-only-do-not-use-in-prod")


class ProductionConfig(Config):
    DEBUG: bool = False
    SESSION_COOKIE_SECURE: bool = True

    def __init__(self) -> None:
        if not self.SECRET_KEY:
            raise RuntimeError(
                "SECRET_KEY environment variable is required in production. "
                "Generate one with: python -c 'import secrets; print(secrets.token_hex(32))'"
            )


class TestingConfig(Config):
    TESTING: bool = True
    SQLALCHEMY_DATABASE_URI: str = "sqlite:///:memory:"
    WTF_CSRF_ENABLED: bool = False
    SECRET_KEY: str = "test-secret"
    # Tests must be deterministic and offline — never use a network recognizer,
    # regardless of what DOCUMENT_RECOGNIZER is set to in the environment / .env.
    DOCUMENT_RECOGNIZER: str = "fake"


def get_config() -> type[Config]:
    """Resolve config class based on FLASK_ENV."""
    env = os.environ.get("FLASK_ENV", "development").lower()
    return {
        "development": DevelopmentConfig,
        "production": ProductionConfig,
        "testing": TestingConfig,
    }.get(env, DevelopmentConfig)