"""Drivers CRUD routes. Demonstrates HTMX usage pattern.

Pattern for HTMX:
- Full page renders return the full template (extends base.html)
- HTMX fragment renders detect HX-Request header and return only the fragment
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import login_required

from app.auth.routes import role_required
from app.docs.models import DriverDocument
from app.docs.status import CRITICAL, EXPIRED, SOON, URGENT, document_status
from app.drivers.contracts import EMPLOYMENT_DOC_TYPE
from app.drivers.forms import DriverContractForm, DriverForm
from app.drivers.models import Driver
from app.extensions import db
from app.models.user import Role
from app.organisations.models import Organisation
from app.vacations.models import LeaveEntry

bp = Blueprint("drivers", __name__)

# Document statuses that count as "needs attention" on the fleet overview.
_DOC_ATTENTION_LEVELS = frozenset({EXPIRED, CRITICAL, URGENT, SOON})


def _driver_stats() -> dict:
    """Fleet-wide counters for the drivers list header (whole fleet, not the
    current search filter)."""
    today = date.today()

    total = db.session.scalar(
        db.select(db.func.count())
        .select_from(Driver)
        .where(Driver.is_deleted.is_(False))
    ) or 0

    with_contract = db.session.scalar(
        db.select(db.func.count(db.func.distinct(DriverDocument.driver_uuid)))
        .join(Driver, Driver.uuid == DriverDocument.driver_uuid)
        .where(
            Driver.is_deleted.is_(False),
            DriverDocument.is_deleted.is_(False),
            DriverDocument.archived_at.is_(None),
            DriverDocument.document_type == EMPLOYMENT_DOC_TYPE,
            DriverDocument.start_date <= today,
            db.or_(
                DriverDocument.end_date.is_(None),
                DriverDocument.end_date >= today,
            ),
        )
    ) or 0

    leave_rows = db.session.execute(
        db.select(
            Driver.uuid,
            Driver.first_name,
            Driver.last_name,
            LeaveEntry.kind,
            LeaveEntry.end_date,
        )
        .join(Driver, Driver.uuid == LeaveEntry.driver_uuid)
        .where(
            Driver.is_deleted.is_(False),
            LeaveEntry.is_deleted.is_(False),
            LeaveEntry.start_date <= today,
            LeaveEntry.end_date >= today,
        )
        .order_by(LeaveEntry.end_date)
    ).all()
    # One row per driver (a driver may have overlapping entries); keep the first,
    # which — given the ordering — is the soonest-ending leave.
    on_leave_list: dict = {}
    for driver_uuid, first_name, last_name, kind, end in leave_rows:
        if driver_uuid in on_leave_list:
            continue
        on_leave_list[driver_uuid] = {
            "driver_id": driver_uuid,
            "driver_name": f"{first_name} {last_name}",
            "kind": kind.value.replace("_", " "),
            "end_date": end,
        }
    on_leave = list(on_leave_list.values())

    # Expiry status depends on document_type (insurance vs generic) and untracked
    # types, so classify in Python — the fleet is small enough that loading the
    # rows is cheap.
    docs = db.session.execute(
        db.select(
            Driver.uuid,
            Driver.first_name,
            Driver.last_name,
            DriverDocument.document_type,
            DriverDocument.end_date,
        )
        .join(Driver, Driver.uuid == DriverDocument.driver_uuid)
        .where(
            Driver.is_deleted.is_(False),
            DriverDocument.is_deleted.is_(False),
            DriverDocument.archived_at.is_(None),
        )
    ).all()

    expiring = []
    for driver_uuid, first_name, last_name, dtype, end in docs:
        st = document_status(dtype, end)
        if st.level in _DOC_ATTENTION_LEVELS:
            expiring.append(
                {
                    "driver_id": driver_uuid,
                    "driver_name": f"{first_name} {last_name}",
                    "document_type": dtype.replace("_", " "),
                    "end_date": end,
                    "level": st.level,
                    "label": st.label,
                    "days_left": st.days_left,
                }
            )
    # Most urgent first (already-expired have negative days_left, so they lead).
    expiring.sort(key=lambda d: (d["days_left"] is None, d["days_left"]))

    return {
        "total": total,
        "with_contract": with_contract,
        "without_contract": total - with_contract,
        "on_leave": len(on_leave),
        "on_leave_list": on_leave,
        "expiring_docs": len(expiring),
        "expiring_list": expiring,
    }


def _document_counts(drivers: list[Driver]) -> dict:
    """Per-driver active-document counts (total + expiring) for the list table.

    One query for all displayed drivers — avoids an N+1 lazy load per row.
    Keyed by driver uuid; expiring uses the same type-aware classification as the
    fleet header.
    """
    counts = {d.uuid: {"total": 0, "expiring": 0} for d in drivers}
    if not counts:
        return counts

    rows = db.session.execute(
        db.select(
            DriverDocument.driver_uuid,
            DriverDocument.document_type,
            DriverDocument.end_date,
        ).where(
            DriverDocument.driver_uuid.in_(counts.keys()),
            DriverDocument.is_deleted.is_(False),
            DriverDocument.archived_at.is_(None),
        )
    ).all()
    for driver_uuid, dtype, end in rows:
        c = counts[driver_uuid]
        c["total"] += 1
        if document_status(dtype, end).level in _DOC_ATTENTION_LEVELS:
            c["expiring"] += 1
    return counts


def _organisation_choices() -> list[tuple[str, str]]:
    orgs = db.session.execute(
        db.select(Organisation)
        .where(Organisation.is_deleted.is_(False))
        .order_by(Organisation.name)
    ).scalars().all()
    return [(str(o.uuid), o.name) for o in orgs]


def _get_active_driver_or_404(driver_id: uuid.UUID) -> Driver:
    driver = db.session.get(Driver, driver_id)
    if driver is None or driver.is_deleted:
        abort(404)
    return driver


@bp.route("/")
@login_required
def list_drivers():
    """List all drivers. Anyone authenticated can view."""
    search = request.args.get("q", "").strip()

    query = (
        db.select(Driver)
        .where(Driver.is_deleted.is_(False))
        .order_by(Driver.last_name, Driver.first_name)
    )
    if search:
        like = f"%{search}%"
        query = query.where(
            db.or_(
                Driver.last_name.ilike(like),
                Driver.first_name.ilike(like),
                Driver.pesel.ilike(like),
                Driver.passport_number.ilike(like),
                Driver.identification_id.ilike(like),
            )
        )

    drivers = db.session.execute(query).scalars().all()
    doc_counts = _document_counts(drivers)

    # HTMX request → return just the active table fragment for live search
    if request.headers.get("HX-Request"):
        return render_template("drivers/_table.html", drivers=drivers, doc_counts=doc_counts)

    # Soft-deleted drivers — shown in a separate archive table below (full page
    # only). Expiry tracking is irrelevant for deleted drivers, so it's omitted.
    deleted_drivers = db.session.execute(
        db.select(Driver)
        .where(Driver.is_deleted.is_(True))
        .order_by(Driver.deleted_at.desc())
    ).scalars().all()

    return render_template(
        "drivers/list.html",
        drivers=drivers,
        search=search,
        stats=_driver_stats(),
        doc_counts=doc_counts,
        deleted_drivers=deleted_drivers,
    )


@bp.route("/new", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def create_driver():
    form = DriverForm()
    form.organisation_uuid.choices = _organisation_choices()
    if form.validate_on_submit():
        driver = Driver(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            birth_date=form.birth_date.data,
            nationality=form.nationality.data,
            organisation_uuid=form.organisation_uuid.data,
            identification_id=form.identification_id.data,
            pesel=form.pesel.data or None,
            passport_number=form.passport_number.data or None,
            tachograph_card_number=form.tachograph_card_number.data or None,
            phone=form.phone.data or None,
            notes=form.notes.data or None,
            hire_date=form.hire_date.data,
        )
        db.session.add(driver)
        db.session.commit()
        flash(_("Driver %(name)s added", name=driver.full_name), "success")
        return redirect(url_for("drivers.show_driver", driver_id=driver.uuid))

    return render_template("drivers/form.html", form=form, driver=None)


@bp.route("/<uuid:driver_id>")
@login_required
def show_driver(driver_id: uuid.UUID):
    driver = _get_active_driver_or_404(driver_id)

    # Vacations tab context (read-only build — safe on GET).
    from app.vacations import services as vac_services
    from app.vacations.forms import EntitlementForm, LeaveEntryForm

    year, month = vac_services.parse_month_arg(request.args.get("vac_month"))
    vac = vac_services.build_panel_context(driver, db.session, year=year, month=month)
    ent = vac["balance"]["entitlement"]
    entitlement_form = EntitlementForm(
        base_days=ent.base_days if ent else vac["balance"]["entitled"],
        carried_over_days=ent.carried_over_days if ent else 0,
        adjustment_days=ent.adjustment_days if ent else 0,
    )
    return render_template(
        "drivers/show.html",
        driver=driver,
        vac=vac,
        leave_form=LeaveEntryForm(),
        entitlement_form=entitlement_form,
    )


@bp.route("/<uuid:driver_id>/edit", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def edit_driver(driver_id: uuid.UUID):
    driver = _get_active_driver_or_404(driver_id)
    form = DriverForm(obj=driver)
    form.organisation_uuid.choices = _organisation_choices()
    if form.validate_on_submit():
        form.populate_obj(driver)
        # Rule: editing the driver's tachograph card number updates the matching
        # tachograph-card document so the two stay in step.
        synced = driver.sync_tachograph_card_number_to_documents()
        db.session.commit()
        flash(_("Driver updated"), "success")
        if synced:
            flash(_("Tachograph card document number updated to match"), "success")
        return redirect(url_for("drivers.show_driver", driver_id=driver.uuid))
    return render_template("drivers/form.html", form=form, driver=driver)


@bp.route("/<uuid:driver_id>/delete", methods=["POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def delete_driver(driver_id: uuid.UUID):
    driver = _get_active_driver_or_404(driver_id)
    driver.soft_delete()
    db.session.commit()
    flash(_("Driver %(name)s deleted", name=driver.full_name), "success")
    return redirect(url_for("drivers.list_drivers"))


@bp.route("/<uuid:driver_id>/restore", methods=["POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def restore_driver(driver_id: uuid.UUID):
    driver = db.session.get(Driver, driver_id)
    if driver is None or not driver.is_deleted:
        abort(404)
    driver.restore()
    db.session.commit()
    flash(_("Driver %(name)s restored", name=driver.full_name), "success")
    return redirect(url_for("drivers.list_drivers"))


def _get_contract_or_404(contract_id: uuid.UUID) -> DriverDocument:
    """An active employment document (= a contract), or 404."""
    doc = db.session.get(DriverDocument, contract_id)
    if doc is None or doc.is_deleted or doc.document_type != EMPLOYMENT_DOC_TYPE:
        abort(404)
    return doc


def _apply_contract_form(form: DriverContractForm, doc: DriverDocument) -> None:
    """Write the contract form onto an employment document (columns + ``extra``)."""
    doc.document_type = EMPLOYMENT_DOC_TYPE
    doc.document_id = form.number.data
    doc.start_date = form.start_date.data
    doc.end_date = form.end_date.data
    doc.extra = {
        "contract_type": form.contract_type.data,
        "base_salary_pln": str(form.base_salary_pln.data),
        "hours_norm": form.hours_norm.data,
    }


@bp.route("/<uuid:driver_id>/contracts/new", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.ACCOUNTANT)
def add_contract(driver_id: uuid.UUID):
    driver = _get_active_driver_or_404(driver_id)
    form = DriverContractForm()
    if form.validate_on_submit():
        doc = DriverDocument(driver_uuid=driver.uuid)
        _apply_contract_form(form, doc)
        db.session.add(doc)
        db.session.commit()
        flash(_("Contract added"), "success")
        return redirect(url_for("drivers.show_driver", driver_id=driver.uuid))
    return render_template("drivers/contract_form.html", form=form, driver=driver, contract=None)


@bp.route("/contracts/<uuid:contract_id>/edit", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.ACCOUNTANT)
def edit_contract(contract_id: uuid.UUID):
    doc = _get_contract_or_404(contract_id)
    driver = doc.driver
    form = DriverContractForm()
    if request.method == "GET":
        extra = doc.extra or {}
        form.contract_type.data = extra.get("contract_type")
        form.number.data = doc.document_id
        form.start_date.data = doc.start_date
        form.end_date.data = doc.end_date
        form.base_salary_pln.data = Decimal(str(extra.get("base_salary_pln") or "0"))
        form.hours_norm.data = int(extra.get("hours_norm") or 168)
    if form.validate_on_submit():
        _apply_contract_form(form, doc)
        db.session.commit()
        flash(_("Contract updated"), "success")
        return redirect(url_for("drivers.show_driver", driver_id=driver.uuid))
    return render_template("drivers/contract_form.html", form=form, driver=driver, contract=doc)


@bp.route("/contracts/<uuid:contract_id>/delete", methods=["POST"])
@role_required(Role.ADMIN, Role.ACCOUNTANT)
def delete_contract(contract_id: uuid.UUID):
    doc = _get_contract_or_404(contract_id)
    driver_uuid = doc.driver_uuid
    doc.soft_delete()
    db.session.commit()
    flash(_("Contract deleted"), "success")
    return redirect(url_for("drivers.show_driver", driver_id=driver_uuid))
