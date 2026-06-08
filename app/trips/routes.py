"""Trip and segment CRUD routes."""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_babel import gettext as _
from flask_login import login_required

from app.auth.routes import role_required
from app.drivers.models import Driver
from app.extensions import db
from app.models.user import Role
from app.trips.forms import TripForm, TripSegmentForm
from app.trips.models import Trip, TripSegment, TripStatus
from app.vehicles.models import Vehicle, VehicleType

bp = Blueprint("trips", __name__)


def _populate_driver_and_vehicle_choices(form: TripForm) -> None:
    drivers = db.session.execute(
        db.select(Driver)
        .where(Driver.is_active.is_(True), Driver.is_deleted.is_(False))
        .order_by(Driver.last_name, Driver.first_name)
    ).scalars().all()
    form.driver_id.choices = [(str(d.uuid), d.full_name) for d in drivers]

    tractors = db.session.execute(
        db.select(Vehicle).where(
            Vehicle.is_active.is_(True),
            Vehicle.is_deleted.is_(False),
            Vehicle.vehicle_type == VehicleType.TRACTOR,
        ).order_by(Vehicle.registration_plate)
    ).scalars().all()
    form.vehicle_id.choices = [("", "—")] + [
        (str(v.uuid), v.registration_plate) for v in tractors
    ]


@bp.route("/")
@login_required
def list_trips():
    trips = db.session.execute(
        db.select(Trip).order_by(Trip.start_date.desc(), Trip.id.desc()).limit(100)
    ).scalars().all()
    return render_template("trips/list.html", trips=trips)


@bp.route("/new", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER, Role.ACCOUNTANT)
def create_trip():
    form = TripForm()
    _populate_driver_and_vehicle_choices(form)
    if form.validate_on_submit():
        trip = Trip(
            driver_id=form.driver_id.data,
            vehicle_id=form.vehicle_id.data if form.vehicle_id.data else None,
            trip_number=form.trip_number.data,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            notes=form.notes.data or None,
        )
        db.session.add(trip)
        db.session.commit()
        flash(_("Trip %(num)s created", num=trip.trip_number), "success")
        return redirect(url_for("trips.show_trip", trip_id=trip.id))
    return render_template("trips/form.html", form=form, trip=None)


@bp.route("/<int:trip_id>")
@login_required
def show_trip(trip_id: int):
    trip = db.session.get(Trip, trip_id) or abort(404)
    return render_template("trips/show.html", trip=trip)


@bp.route("/<int:trip_id>/confirm", methods=["POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER, Role.ACCOUNTANT)
def confirm_trip(trip_id: int):
    trip = db.session.get(Trip, trip_id) or abort(404)
    if not trip.segments:
        flash(_("Cannot confirm trip without segments"), "error")
        return redirect(url_for("trips.show_trip", trip_id=trip.id))
    trip.status = TripStatus.CONFIRMED
    db.session.commit()
    flash(_("Trip confirmed and locked"), "success")
    return redirect(url_for("trips.show_trip", trip_id=trip.id))


@bp.route("/<int:trip_id>/segments/new", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER, Role.ACCOUNTANT)
def add_segment(trip_id: int):
    trip = db.session.get(Trip, trip_id) or abort(404)
    if trip.status == TripStatus.CONFIRMED:
        flash(_("Cannot add segments to a confirmed trip"), "error")
        return redirect(url_for("trips.show_trip", trip_id=trip.id))

    form = TripSegmentForm()
    if not form.sequence.data:
        form.sequence.data = len(trip.segments)

    if form.validate_on_submit():
        seg = TripSegment(
            trip_id=trip.id,
            sequence=form.sequence.data,
            work_date=form.work_date.data,
            country=form.country.data.upper(),
            segment_type=form.segment_type.data,
            work_hours=form.work_hours.data,
            rate_name=form.rate_name.data,
            notes=form.notes.data or None,
        )
        db.session.add(seg)
        db.session.commit()
        flash(_("Segment added"), "success")
        return redirect(url_for("trips.show_trip", trip_id=trip.id))

    return render_template("trips/segment_form.html", form=form, trip=trip)


@bp.route("/<int:trip_id>/segments/<int:segment_id>/delete", methods=["POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER, Role.ACCOUNTANT)
def delete_segment(trip_id: int, segment_id: int):
    trip = db.session.get(Trip, trip_id) or abort(404)
    if trip.status == TripStatus.CONFIRMED:
        flash(_("Cannot delete segments from a confirmed trip"), "error")
        return redirect(url_for("trips.show_trip", trip_id=trip.id))
    seg = db.session.get(TripSegment, segment_id) or abort(404)
    if seg.trip_id != trip.id:
        abort(404)
    db.session.delete(seg)
    db.session.commit()
    flash(_("Segment deleted"), "success")
    return redirect(url_for("trips.show_trip", trip_id=trip.id))
