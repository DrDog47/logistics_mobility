"""Vehicles routes."""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_babel import gettext as _
from flask_login import login_required

from app.auth.routes import role_required
from app.extensions import db
from app.models.user import Role
from app.vehicles.forms import VehicleForm
from app.vehicles.models import Vehicle

bp = Blueprint("vehicles", __name__)


@bp.route("/")
@login_required
def list_vehicles():
    vehicles = db.session.execute(
        db.select(Vehicle).order_by(Vehicle.vehicle_type, Vehicle.plate)
    ).scalars().all()
    return render_template("vehicles/list.html", vehicles=vehicles)


@bp.route("/new", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def create_vehicle():
    form = VehicleForm()
    if form.validate_on_submit():
        v = Vehicle(
            plate=form.plate.data.upper(),
            vehicle_type=form.vehicle_type.data,
            vin=form.vin.data.upper() if form.vin.data else None,
            make=form.make.data or None,
            model=form.model.data or None,
            year=form.year.data,
            purchase_date=form.purchase_date.data,
        )
        db.session.add(v)
        db.session.commit()
        flash(_("Vehicle %(plate)s added", plate=v.plate), "success")
        return redirect(url_for("vehicles.show_vehicle", vehicle_id=v.id))
    return render_template("vehicles/form.html", form=form, vehicle=None)


@bp.route("/<int:vehicle_id>")
@login_required
def show_vehicle(vehicle_id: int):
    vehicle = db.session.get(Vehicle, vehicle_id) or abort(404)
    return render_template("vehicles/show.html", vehicle=vehicle)


@bp.route("/<int:vehicle_id>/edit", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def edit_vehicle(vehicle_id: int):
    vehicle = db.session.get(Vehicle, vehicle_id) or abort(404)
    form = VehicleForm(obj=vehicle)
    if form.validate_on_submit():
        form.populate_obj(vehicle)
        db.session.commit()
        flash(_("Vehicle updated"), "success")
        return redirect(url_for("vehicles.show_vehicle", vehicle_id=vehicle.id))
    return render_template("vehicles/form.html", form=form, vehicle=vehicle)
