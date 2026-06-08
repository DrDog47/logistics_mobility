"""Vehicles routes."""

from __future__ import annotations

import uuid

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_babel import gettext as _
from flask_login import login_required

from app.auth.routes import role_required
from app.extensions import db
from app.models.user import Role
from app.organisations.models import Organisation
from app.vehicles.forms import VehicleForm
from app.vehicles.models import Vehicle

bp = Blueprint("vehicles", __name__)


def _organisation_choices() -> list[tuple[str, str]]:
    orgs = db.session.execute(
        db.select(Organisation)
        .where(Organisation.is_deleted.is_(False))
        .order_by(Organisation.name)
    ).scalars().all()
    return [(str(o.uuid), o.name) for o in orgs]


def _get_active_vehicle_or_404(vehicle_id: uuid.UUID) -> Vehicle:
    vehicle = db.session.get(Vehicle, vehicle_id)
    if vehicle is None or vehicle.is_deleted:
        abort(404)
    return vehicle


@bp.route("/")
@login_required
def list_vehicles():
    vehicles = db.session.execute(
        db.select(Vehicle)
        .where(Vehicle.is_deleted.is_(False))
        .order_by(Vehicle.vehicle_type, Vehicle.registration_plate)
    ).scalars().all()
    return render_template("vehicles/list.html", vehicles=vehicles)


@bp.route("/new", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def create_vehicle():
    form = VehicleForm()
    form.organisation_uuid.choices = _organisation_choices()
    if form.validate_on_submit():
        v = Vehicle(
            registration_plate=form.registration_plate.data.upper(),
            vehicle_type=form.vehicle_type.data,
            vin=form.vin.data.upper(),
            brand=form.brand.data,
            model=form.model.data,
            organisation_uuid=form.organisation_uuid.data,
            acquisition_date=form.acquisition_date.data,
            manufacture_date=form.manufacture_date.data,
        )
        db.session.add(v)
        db.session.commit()
        flash(_("Vehicle %(plate)s added", plate=v.registration_plate), "success")
        return redirect(url_for("vehicles.show_vehicle", vehicle_id=v.uuid))
    return render_template("vehicles/form.html", form=form, vehicle=None)


@bp.route("/<uuid:vehicle_id>")
@login_required
def show_vehicle(vehicle_id: uuid.UUID):
    vehicle = _get_active_vehicle_or_404(vehicle_id)
    return render_template("vehicles/show.html", vehicle=vehicle)


@bp.route("/<uuid:vehicle_id>/edit", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def edit_vehicle(vehicle_id: uuid.UUID):
    vehicle = _get_active_vehicle_or_404(vehicle_id)
    form = VehicleForm(obj=vehicle)
    form.organisation_uuid.choices = _organisation_choices()
    if form.validate_on_submit():
        form.populate_obj(vehicle)
        db.session.commit()
        flash(_("Vehicle updated"), "success")
        return redirect(url_for("vehicles.show_vehicle", vehicle_id=vehicle.uuid))
    return render_template("vehicles/form.html", form=form, vehicle=vehicle)


@bp.route("/<uuid:vehicle_id>/delete", methods=["POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def delete_vehicle(vehicle_id: uuid.UUID):
    vehicle = _get_active_vehicle_or_404(vehicle_id)
    vehicle.soft_delete()
    db.session.commit()
    flash(_("Vehicle %(plate)s deleted", plate=vehicle.registration_plate), "success")
    return redirect(url_for("vehicles.list_vehicles"))
