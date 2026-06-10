"""Vehicles routes."""

from __future__ import annotations

import uuid
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import login_required

from app.auth.routes import role_required
from app.documents.constants import ENTITY_VEHICLE
from app.documents.models import VehicleDocument
from app.documents.status import CRITICAL, EXPIRED, SOON, URGENT, document_status
from app.extensions import db
from app.models.user import Role
from app.organisations.models import Organisation
from app.vehicles.forms import VehicleForm
from app.vehicles.models import Vehicle, VehicleType

bp = Blueprint("vehicles", __name__)

# Registration certificate — the vehicle "passport" (PRD vehicle §1.2, §8.4). Its
# presence is what makes a vehicle "complete"; it is untracked (no expiry).
_TECH_PASSPORT_TYPE = "tech_passport"

# Document statuses that count as "needs attention" on the fleet overview.
_DOC_ATTENTION_LEVELS = frozenset({EXPIRED, CRITICAL, URGENT, SOON})


def _vehicle_stats() -> dict:
    """Fleet-wide counters for the vehicles list header (whole fleet, not the
    current search filter). Mirrors ``drivers._driver_stats``: there are no
    vacations for vehicles, and "active contracts" is replaced by tractor /
    trailer counts and "without registration certificate"."""
    active = Vehicle.is_deleted.is_(False)

    def _count(*extra) -> int:
        return db.session.scalar(
            db.select(db.func.count()).select_from(Vehicle).where(active, *extra)
        ) or 0

    total = _count()
    tractors = _count(Vehicle.vehicle_type == VehicleType.TRACTOR)
    trailers = _count(Vehicle.vehicle_type == VehicleType.TRAILER)

    # Vehicles that have an active (non-archived) registration certificate.
    with_tech_passport = db.session.scalar(
        db.select(db.func.count(db.func.distinct(VehicleDocument.vehicle_uuid)))
        .join(Vehicle, Vehicle.uuid == VehicleDocument.vehicle_uuid)
        .where(
            Vehicle.is_deleted.is_(False),
            VehicleDocument.is_deleted.is_(False),
            VehicleDocument.archived_at.is_(None),
            VehicleDocument.document_type == _TECH_PASSPORT_TYPE,
        )
    ) or 0

    # Expiry classification needs the type-aware vehicle scale (60/30/15), so
    # load the rows and classify in Python (the fleet is small).
    docs = db.session.execute(
        db.select(
            Vehicle.uuid,
            Vehicle.registration_plate,
            VehicleDocument.document_type,
            VehicleDocument.end_date,
        )
        .join(Vehicle, Vehicle.uuid == VehicleDocument.vehicle_uuid)
        .where(
            Vehicle.is_deleted.is_(False),
            VehicleDocument.is_deleted.is_(False),
            VehicleDocument.archived_at.is_(None),
        )
    ).all()

    expiring = []
    for vehicle_uuid, plate, dtype, end in docs:
        st = document_status(dtype, end, entity_type=ENTITY_VEHICLE)
        if st.level in _DOC_ATTENTION_LEVELS:
            expiring.append(
                {
                    "vehicle_id": vehicle_uuid,
                    "vehicle_name": plate,
                    "document_type": dtype.replace("_", " "),
                    "end_date": end,
                    "level": st.level,
                    "label": st.label,
                    "days_left": st.days_left,
                }
            )
    expiring.sort(key=lambda d: (d["days_left"] is None, d["days_left"]))

    return {
        "total": total,
        "tractors": tractors,
        "trailers": trailers,
        "with_tech_passport": with_tech_passport,
        "without_tech_passport": total - with_tech_passport,
        "expiring_docs": len(expiring),
        "expiring_list": expiring,
    }


def _document_counts(vehicles: list[Vehicle]) -> dict:
    """Per-vehicle active-document counts (total + expiring) for the list table.

    One query for all displayed vehicles — avoids an N+1 lazy load per row.
    """
    counts = {v.uuid: {"total": 0, "expiring": 0} for v in vehicles}
    if not counts:
        return counts

    rows = db.session.execute(
        db.select(
            VehicleDocument.vehicle_uuid,
            VehicleDocument.document_type,
            VehicleDocument.end_date,
        ).where(
            VehicleDocument.vehicle_uuid.in_(counts.keys()),
            VehicleDocument.is_deleted.is_(False),
            VehicleDocument.archived_at.is_(None),
        )
    ).all()
    for vehicle_uuid, dtype, end in rows:
        c = counts[vehicle_uuid]
        c["total"] += 1
        if document_status(dtype, end, entity_type=ENTITY_VEHICLE).level in _DOC_ATTENTION_LEVELS:
            c["expiring"] += 1
    return counts


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
    """List all vehicles. Anyone authenticated can view (mirrors drivers list)."""
    search = request.args.get("q", "").strip()

    query = (
        db.select(Vehicle)
        .where(Vehicle.is_deleted.is_(False))
        .order_by(Vehicle.vehicle_type, Vehicle.registration_plate)
    )
    if search:
        like = f"%{search}%"
        query = query.where(
            db.or_(
                Vehicle.registration_plate.ilike(like),
                Vehicle.vin.ilike(like),
                Vehicle.brand.ilike(like),
                Vehicle.model.ilike(like),
            )
        )

    vehicles = db.session.execute(query).scalars().all()
    doc_counts = _document_counts(vehicles)

    # HTMX request → return just the active table fragment for live search.
    if request.headers.get("HX-Request"):
        return render_template("vehicles/_table.html", vehicles=vehicles, doc_counts=doc_counts)

    deleted_vehicles = db.session.execute(
        db.select(Vehicle)
        .where(Vehicle.is_deleted.is_(True))
        .order_by(Vehicle.deleted_at.desc())
    ).scalars().all()

    return render_template(
        "vehicles/list.html",
        vehicles=vehicles,
        search=search,
        stats=_vehicle_stats(),
        doc_counts=doc_counts,
        deleted_vehicles=deleted_vehicles,
    )


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
            registration_country=form.registration_country.data,
            registration_date=form.registration_date.data,
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


@bp.route("/<uuid:vehicle_id>/restore", methods=["POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def restore_vehicle(vehicle_id: uuid.UUID):
    vehicle = db.session.get(Vehicle, vehicle_id)
    if vehicle is None or not vehicle.is_deleted:
        abort(404)
    vehicle.restore()
    db.session.commit()
    flash(_("Vehicle %(plate)s restored", plate=vehicle.registration_plate), "success")
    return redirect(url_for("vehicles.list_vehicles"))
