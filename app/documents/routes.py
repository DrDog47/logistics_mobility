"""Document CRUD routes, nested under a driver or vehicle, plus management of
the document-type catalogue."""

from __future__ import annotations

import uuid
from datetime import date

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_babel import gettext as _
from flask_login import login_required

from app.auth.routes import role_required
from app.documents.constants import ENTITY_DRIVER, ENTITY_VEHICLE
from app.documents.forms import (
    DocumentTypeForm,
    DriverDocumentForm,
    DriverFileForm,
    VehicleDocumentForm,
)
from app.documents.models import (
    DocumentType,
    DriverDocument,
    DriverFile,
    VehicleDocument,
)
from app.documents.persistence import apply_recognized
from app.documents.pipeline import (
    TRIGGER_TYPES,
    RecognizedFile,
    inbox_has_pending_trigger,
    is_trigger,
    list_inbox_files,
    pending_trigger_count,
    recognize_file,
    recognize_inbox,
    sort_triggers_first,
)
from app.documents.recognizer import RecognitionResult
from app.documents.services import (
    document_type_choices,
    parse_file_links,
    resolve_stored_file,
    save_uploads_to_inbox,
    set_driver_document_files,
)
from app.documents.validation import normalize_passport_number, validate_recognition
from app.drivers.models import Driver
from app.extensions import db
from app.models.user import Role
from app.vehicles.models import Vehicle

bp = Blueprint("documents", __name__)

_EDITORS = (Role.ADMIN, Role.FLEET_MANAGER, Role.ACCOUNTANT)


# --- Bulk upload to the inbox (PRD §8.6) -------------------------------------

@bp.route("/documents/upload", methods=["POST"])
@role_required(*_EDITORS)
def upload_documents():
    """Receive a dropped package of files and store it in the inbox.

    Phase one only: files land in ``_Inbox/`` for later recognition/sorting
    (PRD §8). No DB writes here. Returns JSON for the drag-and-drop UI.
    """
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no_files", "saved": [], "rejected": []}), 400

    saved, rejected = save_uploads_to_inbox(files)
    return jsonify(
        {
            "saved": saved,
            "rejected": rejected,
            "saved_count": len(saved),
            "rejected_count": len(rejected),
        }
    )


# --- Inbox recognition preview (PRD §8.2) ------------------------------------

@bp.route("/documents/inbox")
@role_required(*_EDITORS)
def inbox():
    """List files waiting in the inbox; recognition runs on demand.

    Each file is recognised into the confirmation list (which accumulates) and
    saved individually from there (§8.2).
    """
    files = list_inbox_files()
    return render_template(
        "documents/inbox.html",
        files=[{"name": p.name, "size": p.stat().st_size} for p in files],
        type_choices=document_type_choices(ENTITY_DRIVER),
        drivers=_driver_choices(),
        entries=[],
    )


def _driver_choices() -> list[Driver]:
    return db.session.execute(
        db.select(Driver)
        .where(Driver.is_deleted.is_(False))
        .order_by(Driver.last_name, Driver.first_name)
    ).scalars().all()


def _parse_date(value: str | None) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _form_uids(form) -> list[str]:
    """Unique entry ids present in a submitted form (derived from *_filename)."""
    suffix = "_filename"
    return [
        key[1:-len(suffix)]
        for key in form.keys()
        if key.startswith("e") and key.endswith(suffix)
    ]


def _entries_from_form(form) -> tuple[list[RecognizedFile], dict[str, uuid.UUID]]:
    """Build recognized files + manual-binding map from a submitted form.

    Entries are keyed by an opaque per-entry id (``e<uid>_…``) rather than a
    positional index, so a form may carry one entry (per-entry save) or many.
    """
    recognized: list[RecognizedFile] = []
    forced: dict[str, uuid.UUID] = {}
    for uid in _form_uids(form):
        p = f"e{uid}_"
        if form.get(p + "skip"):
            continue
        filename = form.get(p + "filename")
        if not filename:
            continue
        result = RecognitionResult(
            recognized=True,
            entity_type="driver",
            document_type=(form.get(p + "document_type") or None),
            identification_id=(form.get(p + "identification_id") or None),
            passport_number=normalize_passport_number(form.get(p + "passport_number") or None),
            first_name=(form.get(p + "first_name") or None),
            last_name=(form.get(p + "last_name") or None),
            birth_date=_parse_date(form.get(p + "birth_date")),
            nationality=(form.get(p + "nationality") or None),
            start_date=_parse_date(form.get(p + "start_date")),
            end_date=_parse_date(form.get(p + "end_date")),
            document_id=(form.get(p + "document_id") or None),
            provider="manual",
        )
        recognized.append(RecognizedFile(filename, 0, "", result))
        raw_uuid = (form.get(p + "driver_uuid") or "").strip()
        if raw_uuid:
            try:
                forced[filename] = uuid.UUID(raw_uuid)
            except ValueError:
                pass
    return recognized, forced


def _entry_pairs(recognized: list[RecognizedFile]) -> list[tuple[str, RecognizedFile]]:
    """Tag each recognised file with a fresh per-entry id for the form."""
    return [(uuid.uuid4().hex, item) for item in recognized]


@bp.route("/documents/inbox/recognize", methods=["POST"])
@role_required(*_EDITORS)
def recognize_inbox_files():
    """Recognise every inbox file and render them as confirmation entries.

    Trigger documents — passports (create drivers) and technical passports
    (create vehicles) — are surfaced first; every other entry is locked until
    they are processed, so an entity exists before its documents attach (§8.4).
    """
    entries = _entry_pairs(sort_triggers_first(recognize_inbox()))
    pending = pending_trigger_count([i for _, i in entries])
    if request.headers.get("HX-Request"):
        return render_template(
            "documents/_inbox_entries.html",
            entries=entries,
            pending_triggers=pending,
            trigger_types=TRIGGER_TYPES,
            type_choices=document_type_choices(ENTITY_DRIVER),
            drivers=_driver_choices(),
        )
    return render_template(
        "documents/inbox.html",
        files=[{"name": p.name, "size": p.stat().st_size} for p in list_inbox_files()],
        entries=entries,
        pending_triggers=pending,
        trigger_types=TRIGGER_TYPES,
        type_choices=document_type_choices(ENTITY_DRIVER),
        drivers=_driver_choices(),
    )


@bp.route("/documents/inbox/recognize-one", methods=["POST"])
@role_required(*_EDITORS)
def recognize_inbox_one():
    """Recognise a single inbox file and return one confirmation entry.

    The entry is appended to the confirmation list (HTMX ``beforeend``) so
    recognising files one by one accumulates them instead of replacing.
    """
    filename = (request.form.get("filename") or "").strip()
    # Validate against the actual inbox listing (rejects path traversal / stale).
    match = next((p for p in list_inbox_files() if p.name == filename), None)
    if match is None:
        abort(404)
    uid, item = _entry_pairs([recognize_file(match)])[0]
    # Lock a non-trigger entry while a passport / technical passport is still
    # waiting in the inbox — it must not be processed first (§8.4).
    locked = not is_trigger(item) and inbox_has_pending_trigger(exclude={item.filename})
    if request.headers.get("HX-Request"):
        return render_template(
            "documents/_inbox_entry.html",
            uid=uid,
            item=item,
            locked=locked,
            type_choices=document_type_choices(ENTITY_DRIVER),
            drivers=_driver_choices(),
        )
    return render_template(
        "documents/inbox.html",
        files=[{"name": p.name, "size": p.stat().st_size} for p in list_inbox_files()],
        entries=[(uid, item)],
        pending_triggers=1 if locked else 0,
        trigger_types=TRIGGER_TYPES,
        type_choices=document_type_choices(ENTITY_DRIVER),
        drivers=_driver_choices(),
    )


def _render_inbox_files():
    """Render the awaiting-files fragment (used after delete / clear / upload)."""
    return render_template(
        "documents/_inbox_files.html",
        files=[{"name": p.name, "size": p.stat().st_size} for p in list_inbox_files()],
    )


@bp.route("/documents/inbox/files")
@role_required(*_EDITORS)
def inbox_files():
    """The awaiting-files fragment as a GET — for inline refresh after an upload
    (e.g. on the inbox or new-driver page, via the ``inboxChanged`` event)."""
    return _render_inbox_files()


@bp.route("/documents/inbox/files/delete", methods=["POST"])
@role_required(*_EDITORS)
def delete_inbox_file():
    """Delete one file from the inbox folder (× on a file row)."""
    filename = (request.form.get("filename") or "").strip()
    match = next((p for p in list_inbox_files() if p.name == filename), None)
    if match is not None:
        match.unlink()
    return _render_inbox_files()


@bp.route("/documents/inbox/files/clear", methods=["POST"])
@role_required(*_EDITORS)
def clear_inbox_files():
    """Remove every file from the inbox folder (Remove all)."""
    for path in list_inbox_files():
        path.unlink()
    return _render_inbox_files()


@bp.route("/documents/inbox/driver-options")
@role_required(*_EDITORS)
def driver_options():
    """Fresh <option> list for the entry driver picker (refreshed after a save
    creates a new driver)."""
    return render_template("documents/_driver_options.html", drivers=_driver_choices())


@bp.route("/documents/inbox/apply", methods=["POST"])
@role_required(*_EDITORS)
def apply_inbox():
    """Persist one confirmed entry (PRD §8.4–8.6).

    Each confirmation entry is its own form, so this saves a single document:
    creates/updates the driver from a passport, attaches the document (honouring
    manual binding), moves the file into the driver folder. On success the entry
    is removed from the list and the driver pickers refresh (a passport may have
    just created a driver). If the file can't be placed it stays, with the reason.
    """
    recognized, forced = _entries_from_form(request.form)
    submitted = recognized[0] if recognized else None

    # Gate (§8.4): a non-trigger document cannot be processed while a passport or
    # technical passport is still waiting in the inbox — those create the entity
    # the other documents attach to, so they must be handled first.
    if (
        submitted
        and not is_trigger(submitted)
        and inbox_has_pending_trigger(exclude={submitted.filename})
    ):
        uids = _form_uids(request.form)
        return render_template(
            "documents/_inbox_entry.html",
            uid=uids[0] if uids else uuid.uuid4().hex,
            item=submitted,
            locked=True,
            error=_("process passports and technical passports first"),
            type_choices=document_type_choices(ENTITY_DRIVER),
            drivers=_driver_choices(),
        )

    # Block the save until identifiers match their format (§8 — manual verify).
    if submitted:
        field_errors = validate_recognition(submitted.result)
        if field_errors:
            uids = _form_uids(request.form)
            return render_template(
                "documents/_inbox_entry.html",
                uid=uids[0] if uids else uuid.uuid4().hex,
                item=submitted,
                field_errors=field_errors,
                type_choices=document_type_choices(ENTITY_DRIVER),
                drivers=_driver_choices(),
            )

    report = apply_recognized(recognized, forced=forced)
    left = {f["filename"]: f["reason"] for f in report.left_in_inbox}

    # Couldn't place it — re-render the same entry with the reason, keep it.
    if submitted and submitted.filename in left:
        uids = _form_uids(request.form)
        return render_template(
            "documents/_inbox_entry.html",
            uid=uids[0] if uids else uuid.uuid4().hex,
            item=submitted,
            error=left[submitted.filename],
            type_choices=document_type_choices(ENTITY_DRIVER),
            drivers=_driver_choices(),
        )

    # Saved — remove the entry and tell driver pickers to refresh.
    response = make_response(
        render_template(
            "documents/_inbox_saved.html",
            report=report,
            filename=submitted.filename if submitted else "",
        )
    )
    response.headers["HX-Trigger"] = "driversChanged"
    return response


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
        form.file_links.data = "\n".join(f.file_link for f in doc.files)
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
    # Deleting a document deletes its files too (soft-delete, mirrors the doc).
    for file in doc.files:
        if not file.is_deleted:
            file.soft_delete()
    doc.soft_delete()
    db.session.commit()
    flash(_("Document and its files deleted"), "success")
    return redirect(url_for("drivers.show_driver", driver_id=driver_uuid))


# --- Driver document files (driver_file rows) --------------------------------

@bp.route("/driver-files/<uuid:file_id>/edit", methods=["GET", "POST"])
@role_required(*_EDITORS)
def edit_driver_file(file_id: uuid.UUID):
    file = _get_active(DriverFile, file_id)
    driver = file.document.driver
    form = DriverFileForm(obj=file)
    if form.validate_on_submit():
        file.file_link = form.file_link.data
        file.document_type = form.document_type.data or None
        file.document_id = form.document_id.data or None
        file.start_date = form.start_date.data
        file.end_date = form.end_date.data
        db.session.commit()
        flash(_("File updated"), "success")
        return redirect(url_for("drivers.show_driver", driver_id=driver.uuid))
    return render_template("documents/driver_file_form.html", form=form, file=file, driver=driver)


@bp.route("/driver-files/<uuid:file_id>/delete", methods=["POST"])
@role_required(*_EDITORS)
def delete_driver_file(file_id: uuid.UUID):
    file = _get_active(DriverFile, file_id)
    driver_uuid = file.document.driver_uuid
    file.soft_delete()
    db.session.commit()
    flash(_("File deleted"), "success")
    return redirect(url_for("drivers.show_driver", driver_id=driver_uuid))


@bp.route("/driver-files/<uuid:file_id>/download")
@login_required
def download_driver_file(file_id: uuid.UUID):
    """Serve a driver file from local storage. Opens in the browser by default;
    ``?dl=1`` forces a download. The path is resolved under DOCUMENTS_DIR and
    guarded against traversal."""
    file = _get_active(DriverFile, file_id)
    path = resolve_stored_file(file.file_link)
    if path is None:
        abort(404)
    as_attachment = request.args.get("dl") in ("1", "true", "yes")
    return send_file(path, as_attachment=as_attachment, download_name=path.name)


@bp.route("/drivers/<uuid:driver_id>/documents/download")
@login_required
def download_driver_documents_zip(driver_id: uuid.UUID):
    """Download all of a driver's document files as one .zip. ``?archived=1``
    bundles the archived documents instead of the current ones."""
    import io
    import zipfile

    driver = _get_active(Driver, driver_id)
    archived = request.args.get("archived") in ("1", "true", "yes")
    docs = driver.archived_documents if archived else driver.active_documents

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in docs:
            for file in doc.files:
                if file.is_deleted:
                    continue
                path = resolve_stored_file(file.file_link)
                if path is None:
                    continue
                # Group by document so duplicate file names don't collide.
                zf.write(path, arcname=f"{doc.document_type}_{doc.uuid.hex[:8]}/{path.name}")
    buffer.seek(0)

    suffix = "archived" if archived else "documents"
    name = f"{driver.first_name}_{driver.last_name}_{suffix}.zip".replace(" ", "_")
    return send_file(buffer, mimetype="application/zip", as_attachment=True, download_name=name)


def _apply_driver_form(form: DriverDocumentForm, doc: DriverDocument) -> None:
    doc.document_type = form.document_type.data
    doc.document_id = form.document_id.data or None
    doc.start_date = form.start_date.data
    doc.end_date = form.end_date.data
    set_driver_document_files(doc, form.file_links.data)
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