"""Main routes: root, dashboard."""

from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.drivers.models import Driver
from app.extensions import db
from app.trips.models import Trip, TripStatus
from app.vehicles.models import Vehicle

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    """Root: redirect to dashboard (auth required) or login."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/dashboard")
@login_required
def dashboard():
    stats = {
        "drivers": db.session.scalar(
            db.select(db.func.count(Driver.uuid)).where(
                Driver.is_active.is_(True), Driver.is_deleted.is_(False)
            )
        ) or 0,
        "vehicles": db.session.scalar(
            db.select(db.func.count(Vehicle.uuid)).where(
                Vehicle.is_active.is_(True), Vehicle.is_deleted.is_(False)
            )
        ) or 0,
        "draft_trips": db.session.scalar(
            db.select(db.func.count(Trip.id)).where(Trip.status == TripStatus.DRAFT)
        ) or 0,
    }
    return render_template("main/dashboard.html", stats=stats)
