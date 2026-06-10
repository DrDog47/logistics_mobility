"""Settings page: integrations (Google Calendar) and configuration overview.

The Google OAuth flow itself lives in the vacations blueprint
(``vacations.google_connect`` / ``google_callback``); this page is just the
admin entry point that shows status and triggers the connect.
"""

from __future__ import annotations

from flask import Blueprint, current_app, render_template

from app.auth.routes import role_required
from app.extensions import db
from app.models.user import Role
from app.vacations import services

bp = Blueprint("settings", __name__)


@bp.route("/settings")
@role_required(Role.ADMIN)
def index():
    account = services.get_account(db.session)
    return render_template(
        "settings/index.html",
        account=account,
        google_configured=bool(
            current_app.config.get("GOOGLE_CLIENT_ID")
            and current_app.config.get("GOOGLE_CLIENT_SECRET")
        ),
        push_calendar_id=current_app.config.get("GOOGLE_CALENDAR_ID", "primary"),
        embed_id=current_app.config.get("GOOGLE_CALENDAR_EMBED_ID") or "",
    )
