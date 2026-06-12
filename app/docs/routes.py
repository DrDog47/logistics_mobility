"""Document CRUD routes, nested under a driver or vehicle, plus management of
the document-type catalogue."""

from __future__ import annotations

import asyncio
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
from app.docs.constants import ENTITY_DRIVER, ENTITY_VEHICLE
from app.docs.forms import (
    DocumentTypeForm,
    DriverDocumentForm,
    DriverFileForm,
    VehicleDocumentForm,
)
from app.docs.models import (
    DocumentType,
    DriverDocument,
    DriverFile,
    VehicleDocument,
)
from app.docs.persistence import (
    apply_recognized,
    entry_bound_driver,
    entry_confirm_errors,
    suggest_existing_document,
)
from app.docs.pipeline import (
    TRIGGER_TYPES,
    RecognizedFile,
    inbox_has_pending_trigger,
    is_confirmable,
    is_known_format,
    is_recognizable_file,
    is_trigger,
    list_all_inbox_files,
    list_inbox_files,
    pending_trigger_count,
    recognize_paths_async,
    sort_triggers_first,
)
from app.docs.recognizer import RecognitionResult, get_recognizer
from app.docs.services import (
    active_driver_documents,
    document_label,
    document_type_choices,
    parse_file_links,
    resolve_stored_file,
    save_uploads_to_inbox,
    set_driver_document_files,
)
from app.docs.validation import normalize_passport_number
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
    return render_template(
        "documents/inbox.html",
        files=_inbox_file_dicts(),
        type_choices=document_type_choices(ENTITY_DRIVER),
        drivers=_driver_choices(),
        entries=[],
    )


def _inbox_file_dicts() -> list[dict]:
    """Inbox files for the awaiting-recognition list. Includes unsupported types
    so they show up flagged (``recognizable=False``) rather than hidden (§8.2)."""
    return [
        {
            "name": p.name,
            "size": p.stat().st_size,
            "recognizable": is_recognizable_file(p),
        }
        for p in list_all_inbox_files()
    ]


def _driver_choices() -> list[Driver]:
    return db.session.execute(
        db.select(Driver)
        .where(Driver.is_deleted.is_(False))
        .order_by(Driver.last_name, Driver.first_name)
    ).scalars().all()


def _entry_ctx() -> dict:
    """Shared template context for rendering confirmation entries: the type and
    driver pickers. The 'Bind to document' options are computed per entry in the
    template (scoped to the entry's driver)."""
    return {
        "type_choices": document_type_choices(ENTITY_DRIVER),
        "drivers": _driver_choices(),
    }


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


def _entries_from_form(
    form,
) -> tuple[list[RecognizedFile], dict[str, uuid.UUID], dict[str, uuid.UUID]]:
    """Build recognized files + manual-binding maps from a submitted form.

    Returns ``(recognized, forced_drivers, forced_documents)``: ``forced_drivers``
    maps a filename to a driver UUID (bind to driver), ``forced_documents`` maps a
    filename to an existing document UUID (attach the file to that document).

    Entries are keyed by an opaque per-entry id (``e<uid>_…``) rather than a
    positional index, so a form may carry one entry (per-entry save) or many.
    """
    recognized: list[RecognizedFile] = []
    forced: dict[str, uuid.UUID] = {}
    forced_docs: dict[str, uuid.UUID] = {}
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
            # Form-only fields — carried through but not persisted (yet).
            pesel=(form.get(p + "pesel") or None),
            document_nationality=(form.get(p + "document_nationality") or None),
            provider="manual",
        )
        recognized.append(RecognizedFile(filename, 0, "", result))
        raw_uuid = (form.get(p + "driver_uuid") or "").strip()
        if raw_uuid:
            try:
                forced[filename] = uuid.UUID(raw_uuid)
            except ValueError:
                pass
        raw_doc_uuid = (form.get(p + "document_uuid") or "").strip()
        if raw_doc_uuid:
            try:
                forced_docs[filename] = uuid.UUID(raw_doc_uuid)
            except ValueError:
                pass
    return recognized, forced, forced_docs


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

    Files whose format isn't in the PRD catalogue are skipped (§8.2) — they stay
    in the inbox, flagged, rather than becoming a confirmation entry.

    The files are recognised concurrently with ``asyncio.gather`` (real async,
    no threads/subprocesses) — each Claude call awaits independently, capped so
    the provider isn't hammered. We run the batch on its own event loop via
    ``asyncio.run`` since the view itself is a normal (sync) WSGI route.
    """
    recognizer = get_recognizer()
    paths = list_inbox_files()
    results = asyncio.run(recognize_paths_async(recognizer, paths))
    # Every readable file becomes an entry; ones the recognizer couldn't classify
    # are kept and flagged for manual review (yellow) rather than dropped.
    recognized = [i for i in results if is_confirmable(i)]
    entries = _entry_pairs(sort_triggers_first(recognized))
    pending = pending_trigger_count([i for _, i in entries])
    if request.headers.get("HX-Request"):
        return render_template(
            "documents/_inbox_entries.html",
            entries=entries,
            pending_triggers=pending,
            trigger_types=TRIGGER_TYPES,
            **_entry_ctx(),
        )
    return render_template(
        "documents/inbox.html",
        files=_inbox_file_dicts(),
        entries=entries,
        pending_triggers=pending,
        trigger_types=TRIGGER_TYPES,
        **_entry_ctx(),
    )


@bp.route("/documents/inbox/recognize-one", methods=["POST"])
@role_required(*_EDITORS)
def recognize_inbox_one():
    """Recognise a single inbox file and return one confirmation entry.

    Driven by the client one file at a time, so each recognised document appears
    in the confirmation list as soon as its recognition finishes — without
    waiting for the rest of the inbox (§8.2). The response carries an
    ``X-Doc-Format`` header the client uses to decide what to do:

    * ``recognized`` — body is the confirmation entry to append.
    * ``unrecognized`` — empty body; the file's format isn't in the PRD
      catalogue, so it's left in the inbox (flagged red) instead of becoming an
      entry. The client highlights the row.
    """
    filename = (request.form.get("filename") or "").strip()
    # Validate against the actual inbox listing (rejects path traversal / stale).
    match = next((p for p in list_all_inbox_files() if p.name == filename), None)
    if match is None:
        abort(404)
    # Recognise via the async path (real awaited LLM I/O), same as "Recognize all"
    # — just a batch of one. The confirmation entry is built below, only after the
    # result has been fetched. Runs on its own event loop (sync WSGI view).
    recognizer = get_recognizer()
    item = asyncio.run(recognize_paths_async(recognizer, [match]))[0]
    # Unsupported file format — never fed to the recognizer; keep it in the inbox,
    # flagged, and don't make it a confirmation entry (§8.2).
    if not is_confirmable(item):
        response = make_response("")
        response.headers["X-Doc-Format"] = "unrecognized"
        return response
    uid, item = _entry_pairs([item])[0]
    # Lock a non-trigger entry while a passport / technical passport is still
    # waiting in the inbox — it must not be processed first (§8.4).
    locked = not is_trigger(item) and inbox_has_pending_trigger(exclude={item.filename})
    # Auto-fill 'Bind to driver': if exactly one driver matches the recognised
    # data (e.g. first + last name), pre-select them (§8.5). Ambiguous / no match
    # leaves it on '— auto (match by data) —'.
    matched_driver = entry_bound_driver(item.result)
    # Pre-select 'Bind to document': when that driver already has a document of
    # this type, default to attaching to it rather than creating a duplicate.
    suggested = suggest_existing_document(item.result)
    response = make_response(
        render_template(
            "documents/_inbox_entry.html",
            uid=uid,
            item=item,
            locked=locked,
            selected_driver=(matched_driver.uuid if matched_driver else None),
            selected_document=(suggested.uuid if suggested else None),
            **_entry_ctx(),
        )
    )
    response.headers["X-Doc-Format"] = "recognized"
    return response


@bp.route("/documents/inbox/validate", methods=["POST"])
@role_required(*_EDITORS)
def validate_inbox_entry():
    """Re-validate the single edited confirmation entry (§8.2).

    Pure form-level field validation: it checks only the values submitted for this
    one entry (``hx-include="closest form"``) against their format rules and
    re-renders just this entry — invalid fields stay highlighted and the card
    stays red; once every field passes, the card turns green. It does NOT touch
    the other entries or recognise any other inbox file. A locked entry can't be
    edited (its grid has ``pointer-events: none``), so an entry that fires a
    ``change`` is by definition editable — no need to recompute the lock here.
    """
    recognized, forced, forced_docs = _entries_from_form(request.form)
    item = recognized[0] if recognized else None
    uids = _form_uids(request.form)
    uid = uids[0] if uids else uuid.uuid4().hex
    if item is None:
        abort(400)
    # Preselect the bound driver: an explicit choice wins; otherwise fall back to
    # the driver matched on the recognised data (id / passport / first+last name),
    # so editing the name to an existing driver pre-selects them (§8.5).
    matched = entry_bound_driver(item.result, forced.get(item.filename))
    selected_driver = matched.uuid if matched else None
    # Re-run the bind-document preselect after edits: an explicit choice wins,
    # otherwise suggest the driver's existing same-type document (§8.5).
    selected_document = forced_docs.get(item.filename)
    if not selected_document:
        suggested = suggest_existing_document(item.result, selected_driver)
        selected_document = suggested.uuid if suggested else None
    field_errors = entry_confirm_errors(item.result, selected_driver)
    return render_template(
        "documents/_inbox_entry.html",
        uid=uid,
        item=item,
        locked=False,
        field_errors=field_errors,
        validated=True,
        selected_driver=selected_driver,
        selected_document=selected_document,
        **_entry_ctx(),
    )


@bp.route("/documents/inbox/discard", methods=["POST"])
@role_required(*_EDITORS)
def discard_inbox_entry():
    """Drop a recognised document from the confirm list and remove its file from
    the inbox — for documents the operator decides aren't needed (§8.2).

    Returns an empty body so the entry is swapped out, and triggers
    ``inboxChanged`` so the awaiting-files list drops the row too.
    """
    filename = (request.form.get("filename") or "").strip()
    match = next((p for p in list_all_inbox_files() if p.name == filename), None)
    if match is not None:
        match.unlink()
    response = make_response("")
    events = "inboxChanged"
    # Discarding the last passport / technical passport also clears the §8.4 gate.
    if not inbox_has_pending_trigger():
        events += ", triggersCleared"
    response.headers["HX-Trigger"] = events
    return response


@bp.route("/documents/inbox/preview")
@role_required(*_EDITORS)
def preview_inbox_file():
    """Serve an inbox file inline so the operator can view it while reviewing the
    recognised entry and make corrections (§8.2). The filename is validated
    against the inbox listing, which blocks path traversal."""
    filename = (request.args.get("filename") or "").strip()
    match = next((p for p in list_all_inbox_files() if p.name == filename), None)
    if match is None:
        abort(404)
    return send_file(match, download_name=match.name)


def _render_inbox_files():
    """Render the awaiting-files fragment (used after delete / clear / upload)."""
    return render_template(
        "documents/_inbox_files.html",
        files=_inbox_file_dicts(),
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
    match = next((p for p in list_all_inbox_files() if p.name == filename), None)
    if match is not None:
        match.unlink()
    return _render_inbox_files()


@bp.route("/documents/inbox/files/clear", methods=["POST"])
@role_required(*_EDITORS)
def clear_inbox_files():
    """Remove every file from the inbox folder (Remove all)."""
    for path in list_all_inbox_files():
        path.unlink()
    return _render_inbox_files()


@bp.route("/documents/inbox/driver-options")
@role_required(*_EDITORS)
def driver_options():
    """Fresh <option> list for the entry driver picker (refreshed after a save
    creates a new driver)."""
    return render_template("documents/_driver_options.html", drivers=_driver_choices())


@bp.route("/documents/inbox/document-options")
@role_required(*_EDITORS)
def document_options():
    """Fresh <option> list for the entry 'Bind to document' picker, scoped to the
    entry's driver (refreshed via ``documentsChanged`` after a save). The whole
    entry form is sent (``hx-include``) so the driver can be resolved from the
    manual binding or the recognised data, exactly as on render."""
    recognized, forced, forced_docs = _entries_from_form(request.values)
    item = recognized[0] if recognized else None
    result = item.result if item else None
    selected_driver = forced.get(item.filename) if item else None
    driver = entry_bound_driver(result, selected_driver)
    doc_type = result.document_type if result else None
    ready = driver is not None and bool(doc_type)
    documents = (
        [d for d in active_driver_documents(driver.uuid) if d.document_type == doc_type]
        if ready
        else []
    )
    return render_template(
        "documents/_document_options.html",
        documents=documents,
        ready=ready,
        selected_document=forced_docs.get(item.filename) if item else None,
    )


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
    recognized, forced, forced_docs = _entries_from_form(request.form)
    submitted = recognized[0] if recognized else None
    # Preselect the bound driver for re-renders: explicit choice, else the
    # recognition match (id / passport / first+last name).
    _matched = entry_bound_driver(submitted.result, forced.get(submitted.filename)) if submitted else None
    selected_driver = _matched.uuid if _matched else None
    # Preselect the bound document: explicit choice, else the suggested same-type
    # document for that driver.
    selected_document = forced_docs.get(submitted.filename) if submitted else None
    if submitted and not selected_document:
        _suggested = suggest_existing_document(submitted.result, selected_driver)
        selected_document = _suggested.uuid if _suggested else None

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
            selected_document=selected_document,
            selected_driver=selected_driver,
            **_entry_ctx(),
        )

    # Block the save until the type is set, the identifiers match their format,
    # and a non-trigger document has a bound driver (§8 — manual verify; a file
    # can't be filed without a known type, and non-passports need a driver).
    if submitted:
        field_errors = entry_confirm_errors(submitted.result, selected_driver)
        if field_errors:
            uids = _form_uids(request.form)
            return render_template(
                "documents/_inbox_entry.html",
                uid=uids[0] if uids else uuid.uuid4().hex,
                item=submitted,
                field_errors=field_errors,
                selected_document=selected_document,
                selected_driver=selected_driver,
                **_entry_ctx(),
            )

    report = apply_recognized(recognized, forced=forced, forced_docs=forced_docs)
    left = {f["filename"]: f["reason"] for f in report.left_in_inbox}

    # Couldn't place it — re-render the same entry with the reason, keep it.
    if submitted and submitted.filename in left:
        uids = _form_uids(request.form)
        return render_template(
            "documents/_inbox_entry.html",
            uid=uids[0] if uids else uuid.uuid4().hex,
            item=submitted,
            error=left[submitted.filename],
            selected_document=selected_document,
            selected_driver=selected_driver,
            **_entry_ctx(),
        )

    # Saved — remove the entry and tell driver/document pickers to refresh (a
    # passport may have created a driver; any save may have created a document).
    response = make_response(
        render_template(
            "documents/_inbox_saved.html",
            report=report,
            filename=submitted.filename if submitted else "",
        )
    )
    events = "driversChanged, documentsChanged"
    # Once the last passport / technical passport leaves the inbox, the §8.4 gate
    # is over: tell the still-locked entries to unlock (re-render editable).
    if not inbox_has_pending_trigger():
        events += ", triggersCleared"
    response.headers["HX-Trigger"] = events
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
        # Tachograph card → keep the driver's number in step with the document.
        doc.sync_tachograph_number_to_driver(driver)
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
        # Tachograph card → keep the driver's number in step with the document.
        doc.sync_tachograph_number_to_driver(doc.driver)
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
    # Reattach target options: the driver's own active documents. Always include
    # the file's current document, even if it's archived (so the form validates).
    docs = active_driver_documents(driver.uuid)
    if all(d.uuid != file.document_uuid for d in docs):
        docs = [file.document, *docs]
    form.document_uuid.choices = [(str(d.uuid), document_label(d)) for d in docs]
    if not form.is_submitted():
        form.document_uuid.data = file.document_uuid
    if form.validate_on_submit():
        file.file_link = form.file_link.data
        file.document_type = form.document_type.data or None
        file.document_id = form.document_id.data or None
        file.start_date = form.start_date.data
        file.end_date = form.end_date.data
        # Reattach to a different document of the same driver, if changed.
        target_uuid = form.document_uuid.data
        if target_uuid and target_uuid != file.document_uuid:
            target = db.session.get(DriverDocument, target_uuid)
            if target and not target.is_deleted and target.driver_uuid == driver.uuid:
                file.document_uuid = target.uuid
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