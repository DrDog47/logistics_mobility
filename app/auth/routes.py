"""Authentication routes: login, logout."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import wraps
from typing import Any, Callable
from urllib.parse import urlparse

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user, login_required, login_user, logout_user

from app.auth.forms import LoginForm
from app.extensions import db
from app.models.user import Role, User

bp = Blueprint("auth", __name__)


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def role_required(*roles: Role) -> Callable[..., Any]:
    """Decorator: restrict view to users with one of the given roles."""

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        @login_required
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not current_user.has_role(*roles):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_safe_url(target: str) -> bool:
    """Prevent open-redirect attacks on the ?next= parameter."""
    if not target:
        return False
    ref = urlparse(request.host_url)
    test = urlparse(target)
    return test.scheme in ("http", "https") and ref.netloc == test.netloc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.execute(
            db.select(User).where(User.login == form.login.data)
        ).scalar_one_or_none()

        if user is None or not user.check_password(form.password.data):
            flash(_("Invalid login or password"), "error")
            return render_template("auth/login.html", form=form), 401

        if not user.is_active:
            flash(_("Account is disabled"), "error")
            return render_template("auth/login.html", form=form), 403

        login_user(user, remember=form.remember_me.data)
        user.last_login_at = datetime.now(UTC)
        db.session.commit()

        next_page = request.args.get("next")
        if next_page and _is_safe_url(next_page):
            return redirect(next_page)
        return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html", form=form)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash(_("You have been logged out"), "info")
    return redirect(url_for("auth.login"))
