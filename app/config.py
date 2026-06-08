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


def get_config() -> type[Config]:
    """Resolve config class based on FLASK_ENV."""
    env = os.environ.get("FLASK_ENV", "development").lower()
    return {
        "development": DevelopmentConfig,
        "production": ProductionConfig,
        "testing": TestingConfig,
    }.get(env, DevelopmentConfig)
