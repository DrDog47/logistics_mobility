"""Application factory."""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, request, session

from app.config import Config, get_config
from app.extensions import babel, csrf, db, login_manager, migrate


def create_app(config: type[Config] | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        config: Optional config class override (useful for tests).

    Returns:
        Configured Flask application instance.
    """
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config or get_config())

    # Ensure instance dir exists (for SQLite)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    _init_logging(app)
    _init_extensions(app)
    _init_rates_registry(app)
    _init_polish_params(app)
    _register_blueprints(app)
    _register_locale_selector(app)
    _register_template_globals(app)
    _register_healthcheck(app)
    _register_cli(app)

    return app


def _register_template_globals(app: Flask) -> None:
    """Expose helpers used directly inside Jinja templates."""
    from app.documents.status import document_status

    app.jinja_env.globals["document_status"] = document_status


def _init_rates_registry(app: Flask) -> None:
    """Load country rate YAML files at startup."""
    from app.rates.services import init_registry

    init_registry(app)


def _init_polish_params(app: Flask) -> None:
    """Load Polish tax/contribution parameters per year."""
    from app.tax.polish_params import init_polish_params

    init_polish_params(app)


def _init_logging(app: Flask) -> None:
    """Basic logging setup. Gunicorn handles request logs separately."""
    level = logging.DEBUG if app.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "warning"
    babel.init_app(app, locale_selector=_select_locale)
    csrf.init_app(app)


def _register_blueprints(app: Flask) -> None:
    from app.auth.routes import bp as auth_bp
    from app.documents.routes import bp as documents_bp
    from app.drivers.routes import bp as drivers_bp
    from app.main.routes import bp as main_bp
    from app.organisations.routes import bp as organisations_bp
    from app.rates.routes import bp as rates_bp
    from app.trips.routes import bp as trips_bp
    from app.vehicles.routes import bp as vehicles_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(organisations_bp, url_prefix="/organisations")
    app.register_blueprint(drivers_bp, url_prefix="/drivers")
    app.register_blueprint(vehicles_bp, url_prefix="/vehicles")
    app.register_blueprint(documents_bp)
    app.register_blueprint(trips_bp, url_prefix="/trips")
    app.register_blueprint(rates_bp, url_prefix="/rates")
    # NOTE: payroll blueprint is intentionally not registered for now —
    # the payroll module is parked while the document system is built out.


def _select_locale() -> str:
    """Pick locale: explicit ?lang=, session-stored, or default."""
    # Explicit override via query param (also persisted to session)
    lang = request.args.get("lang")
    if lang in ("pl", "en", "ru"):
        session["language"] = lang
        return lang

    # Previously chosen
    if (lang := session.get("language")) in ("pl", "en", "ru"):
        return lang

    # Fall back to config default (pl)
    from flask import current_app

    return current_app.config["DEFAULT_LANGUAGE"]


def _register_locale_selector(app: Flask) -> None:
    """Expose current locale to all templates."""

    @app.context_processor
    def inject_locale() -> dict[str, str]:
        return {"current_locale": _select_locale()}


def _register_healthcheck(app: Flask) -> None:
    """Liveness endpoint for Docker / load balancer."""

    @app.route("/health")
    def health() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200


def _register_cli(app: Flask) -> None:
    """Custom CLI commands."""
    from app.cli import register_cli

    register_cli(app)
