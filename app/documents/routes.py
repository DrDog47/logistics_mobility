"""Document CRUD routes, nested under a driver or vehicle, plus management of
the document-type catalogue."""

from __future__ import annotations

import uuid

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_babel import gettext as _
from flask_login import login_required

from app.auth.routes import role_required
from app.documents.constants import ENTITY_DRIVER, ENTITY_VEHICLE
from app.documents.forms import DocumentTypeForm, DriverDocumentForm, VehicleDocumentForm
from app.documents.models import DocumentType, DriverDocument, VehicleDocument
from app.documents.services import document_type_choices, parse_file_links
from app.drivers.models import Driver
from app.extensions import db
from app.models.user import Role
from app.vehicles.models import Vehicle

bp = Blueprint("documents", __name__)

_EDITORS = (Role.ADMIN, Role.FLEET_MANAGER, Role.ACCOUNTANT)


def _get_active(model, obj_id: uuid.UUID):
    obj = db.session.get(model, obj_id)
    if obj is None or obj.is_deleted:
        abort(404)
    return obj


# --- Driver documents --------------------------------------------------------

@bp.route("/drivers/<uuid:driver_id>/documents/new", methods=["GET", "POST"])
@role_required(*_EDITORS)
def new_driver_document(driver_id: uuid.UUID):
    driver = _get_active(Driver, driver_id)
    form = DriverDocumentForm()
    form.document_type.choices = document_type_choices(ENTITY_DRIVER)
    if form.validate_on_submit():
        doc = DriverDocument(driver_uuid=driver.uuid)
        _apply_driver_form(form, doc)
        db.session.add(doc)
        db.session.commit()
        flash(_("Document added"), "success")
        return redirect(url_for("drivers.show_driver", driver_id=driver.uuid))
    return render_template("documents/driver_form.html", form=form, driver=driver, document=None)


@bp.route("/driver-documents/<uuid:doc_id>/edit", methods=["GET", "POST"])
@role_required(*_EDITORS)
def edit_driver_document(doc_id: uuid.UUID):
    doc = _get_active(DriverDocument, doc_id)
    form = DriverDocumentForm(obj=doc)
    form.document_type.choices = document_type_choices(ENTITY_DRIVER)
    if not form.is_submitted():
        form.file_links.data = "\n".join(doc.file_links or [])
        if doc.extra:
            form.categories.data = doc.extra.get("categories")
    if form.validate_on_submit():
        _apply_driver_form(form, doc)
        db.session.commit()
        flash(_("Document updated"), "success")
        return redirect(url_for("drivers.show_driver", driver_id=doc.driver_uuid))
    return render_template("documents/driver_form.html", form=form, driver=doc.driver, document=doc)


@bp.route("/driver-documents/<uuid:doc_id>/delete", methods=["POST"])
@role_required(*_EDITORS)
def delete_driver_document(doc_id: uuid.UUID):
    doc = _get_active(DriverDocument, doc_id)
    driver_uuid = doc.driver_uuid
    doc.soft_delete()
    db.session.commit()
    flash(_("Document deleted"), "success")
    return redirect(url_for("drivers.show_driver", driver_id=driver_uuid))


def _apply_driver_form(form: DriverDocumentForm, doc: DriverDocument) -> None:
    doc.document_type = form.document_type.data
    doc.document_id = form.document_id.data or None
    doc.start_date = form.start_date.data
    doc.end_date = form.end_date.data
    doc.file_links = parse_file_links(form.file_links.data)
    doc.extra = {"categories": form.categories.data} if form.categories.data else None


# --- Vehicle documents -------------------------------------------------------

@bp.route("/vehicles/<uuid:vehicle_id>/documents/new", methods=["GET", "POST"])
@role_required(*_EDITORS)
def new_vehicle_document(vehicle_id: uuid.UUID):
    vehicle = _get_active(Vehicle, vehicle_id)
    form = VehicleDocumentForm()
    form.document_type.choices = document_type_choices(ENTITY_VEHICLE)
    if form.validate_on_submit():
        doc = VehicleDocument(vehicle_uuid=vehicle.uuid)
        _apply_vehicle_form(form, doc)
        db.session.add(doc)
        db.session.commit()
        flash(_("Document added"), "success")
        return redirect(url_for("vehicles.show_vehicle", vehicle_id=vehicle.uuid))
    return render_template("documents/vehicle_form.html", form=form, vehicle=vehicle, document=None)


@bp.route("/vehicle-documents/<uuid:doc_id>/edit", methods=["GET", "POST"])
@role_required(*_EDITORS)
def edit_vehicle_document(doc_id: uuid.UUID):
    doc = _get_active(VehicleDocument, doc_id)
    form = VehicleDocumentForm(obj=doc)
    form.document_type.choices = document_type_choices(ENTITY_VEHICLE)
    if not form.is_submitted():
        form.file_links.data = "\n".join(doc.file_links or [])
        if doc.extra:
            form.insurance_company.data = doc.extra.get("insurance_company")
    if form.validate_on_submit():
        _apply_vehicle_form(form, doc)
        db.session.commit()
        flash(_("Document updated"), "success")
        return redirect(url_for("vehicles.show_vehicle", vehicle_id=doc.vehicle_uuid))
    return render_template("documents/vehicle_form.html", form=form, vehicle=doc.vehicle, document=doc)


@bp.route("/vehicle-documents/<uuid:doc_id>/delete", methods=["POST"])
@role_required(*_EDITORS)
def delete_vehicle_document(doc_id: uuid.UUID):
    doc = _get_active(VehicleDocument, doc_id)
    vehicle_uuid = doc.vehicle_uuid
    doc.soft_delete()
    db.session.commit()
    flash(_("Document deleted"), "success")
    return redirect(url_for("vehicles.show_vehicle", vehicle_id=vehicle_uuid))


def _apply_vehicle_form(form: VehicleDocumentForm, doc: VehicleDocument) -> None:
    doc.document_type = form.document_type.data
    doc.document_id = form.document_id.data or None
    doc.start_date = form.start_date.data
    doc.end_date = form.end_date.data
    doc.file_links = parse_file_links(form.file_links.data)
    doc.extra = (
        {"insurance_company": form.insurance_company.data}
        if form.insurance_company.data
        else None
    )


# --- Document-type catalogue (operator-managed) ------------------------------

def _get_active_type(type_id: uuid.UUID) -> DocumentType:
    dt = db.session.get(DocumentType, type_id)
    if dt is None or dt.is_deleted:
        abort(404)
    return dt


@bp.route("/document-types/")
@login_required
def list_document_types():
    types = db.session.execute(
        db.select(DocumentType)
        .where(DocumentType.is_deleted.is_(False))
        .order_by(DocumentType.entity_type, DocumentType.type)
    ).scalars().all()
    return render_template("documents/types_list.html", document_types=types)


@bp.route("/document-types/new", methods=["GET", "POST"])
@role_required(*_EDITORS)
def new_document_type():
    form = DocumentTypeForm()
    if form.validate_on_submit():
        if _type_exists(form.type.data, form.entity_type.data):
            flash(_("This type already exists for that entity"), "error")
        else:
            dt = DocumentType(
                type=form.type.data,
                entity_type=form.entity_type.data,
                label=form.label.data or None,
            )
            db.session.add(dt)
            db.session.commit()
            flash(_("Document type added"), "success")
            return redirect(url_for("documents.list_document_types"))
    return render_template("documents/type_form.html", form=form, document_type=None)


@bp.route("/document-types/<uuid:type_id>/edit", methods=["GET", "POST"])
@role_required(*_EDITORS)
def edit_document_type(type_id: uuid.UUID):
    dt = _get_active_type(type_id)
    form = DocumentTypeForm(obj=dt)
    if form.validate_on_submit():
        if _type_exists(form.type.data, form.entity_type.data, exclude=dt.uuid):
            flash(_("This type already exists for that entity"), "error")
        else:
            dt.type = form.type.data
            dt.entity_type = form.entity_type.data
            dt.label = form.label.data or None
            db.session.commit()
            flash(_("Document type updated"), "success")
            return redirect(url_for("documents.list_document_types"))
    return render_template("documents/type_form.html", form=form, document_type=dt)


@bp.route("/document-types/<uuid:type_id>/delete", methods=["POST"])
@role_required(*_EDITORS)
def delete_document_type(type_id: uuid.UUID):
    dt = _get_active_type(type_id)
    dt.soft_delete()
    db.session.commit()
    flash(_("Document type deleted"), "success")
    return redirect(url_for("documents.list_document_types"))


def _type_exists(type_code: str, entity_type: str, exclude: uuid.UUID | None = None) -> bool:
    query = db.select(DocumentType).where(
        DocumentType.type == type_code,
        DocumentType.entity_type == entity_type,
        DocumentType.is_deleted.is_(False),
    )
    if exclude is not None:
        query = query.where(DocumentType.uuid != exclude)
    return db.session.execute(query).first() is not None