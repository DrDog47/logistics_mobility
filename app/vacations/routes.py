"""Vacation routes: leave CRUD, entitlement, and Google Calendar sync.

Nested under a driver for the per-driver tab; Google OAuth lives under
``/vacations/google/*``. The app is the source of truth — creating/deleting a
leave best-effort mirrors it to Google; "Sync now" pulls external events back.
"""

from __future__ import annotations

import uuid
from datetime import date

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_babel import gettext as _
from flask_login import login_required
from sqlalchemy import select

from app.auth.routes import role_required
from app.drivers.models import Driver
from app.extensions import db
from app.models.user import Role
from app.vacations import services
from app.vacations.forms import EntitlementForm, FleetLeaveForm, LeaveEntryForm
from app.vacations.models import (
    GoogleCalendarAccount,
    LeaveEntry,
    LeaveKind,
    LeaveSource,
)

bp = Blueprint("vacations", __name__)

_EDITORS = (Role.ADMIN, Role.FLEET_MANAGER, Role.ACCOUNTANT)


def _get_active_driver_or_404(driver_id: uuid.UUID) -> Driver:
    driver = db.session.get(Driver, driver_id)
    if driver is None or driver.is_deleted:
        abort(404)
    return driver


def _back(driver_id: uuid.UUID):
    """Redirect to the driver page, keeping the calendar month if supplied."""
    month = request.form.get("vac_month") or request.args.get("vac_month")
    return redirect(
        url_for(
            "drivers.show_driver",
            driver_id=driver_id,
            vac_month=month or None,
            _anchor="vacations",
        )
    )


# --- Fleet vacation calendar (own-style month timeline) ---------------------

@bp.route("/vacations/")
@login_required
def calendar():
    """Fleet-wide vacation page: who is on leave today + own-style month timeline.

    Month navigation (prev/next/Today) is an HTMX request that re-renders only
    the timeline fragment, so paging months never reloads the page.
    """
    today = date.today()
    year, month = services.parse_month_arg(request.args.get("vac_month"))
    year = year or today.year
    month = month or today.month
    fleet = services.build_fleet_month(db.session, year, month)
    if request.headers.get("HX-Request"):
        return render_template("vacations/_fleet_month.html", fleet=fleet, today=today)
    account = services.get_account(db.session)
    overview = services.vacations_overview(db.session, year)
    leave_form = FleetLeaveForm()
    leave_form.driver_uuid.choices = [
        (d.uuid, d.full_name) for d in overview["drivers"]
    ]
    from app.vacations import google

    return render_template(
        "vacations/calendar.html",
        on_leave=services.drivers_on_leave(db.session, today),
        fleet=fleet,
        today=today,
        overview=overview,
        leave_form=leave_form,
        calendar_month=f"{year:04d}-{month:02d}",
        connected=account is not None and account.token is not None,
        account_email=account.account_email if account else None,
        last_sync_at=account.last_sync_at if account else None,
        google_configured=google.is_configured(),
    )


# --- Fleet-level leave CRUD (vacations page) --------------------------------
#
# These reuse the same service functions and Google sync as the per-driver tab
# (create_leave / update_leave / _try_push / google delete) but operate from the
# fleet page and redirect back to it, with the driver chosen in the form.

def _fleet_back():
    """Redirect to the fleet vacations page, keeping the timeline month."""
    month = request.form.get("vac_month") or request.args.get("vac_month")
    return redirect(url_for("vacations.calendar", vac_month=month or None))


def _flash_form_errors(form) -> None:
    for errors in form.errors.values():
        for err in errors:
            flash(err, "error")


def _active_driver_choices() -> list:
    drivers = db.session.execute(
        select(Driver)
        .where(Driver.is_deleted.is_(False))
        .order_by(Driver.last_name, Driver.first_name)
    ).scalars().all()
    return [(d.uuid, d.full_name) for d in drivers]


@bp.route("/vacations/add", methods=["POST"])
@role_required(*_EDITORS)
def fleet_add_leave():
    form = FleetLeaveForm()
    form.driver_uuid.choices = _active_driver_choices()
    if not form.validate_on_submit():
        _flash_form_errors(form)
        return _fleet_back()

    driver = _get_active_driver_or_404(form.driver_uuid.data)
    entry = services.create_leave(
        driver.uuid,
        db.session,
        kind=LeaveKind(form.kind.data),
        start_date=form.start_date.data,
        end_date=form.end_date.data,
        note=form.note.data or None,
    )
    db.session.commit()
    _try_push(entry)
    flash(_("Leave added (%(days)d day(s))", days=entry.counted_days), "success")
    return _fleet_back()


@bp.route("/vacations/<uuid:entry_id>/edit", methods=["POST"])
@role_required(*_EDITORS)
def fleet_edit_leave(entry_id: uuid.UUID):
    entry = db.session.get(LeaveEntry, entry_id)
    if entry is None or entry.is_deleted:
        abort(404)
    # Driver is fixed by the entry; only type/dates/note are editable here.
    form = LeaveEntryForm()
    if not form.validate_on_submit():
        _flash_form_errors(form)
        return _fleet_back()

    services.update_leave(
        entry,
        db.session,
        kind=LeaveKind(form.kind.data),
        start_date=form.start_date.data,
        end_date=form.end_date.data,
        note=form.note.data or None,
    )
    db.session.commit()
    _try_push(entry)
    flash(_("Leave updated (%(days)d day(s))", days=entry.counted_days), "success")
    return _fleet_back()


@bp.route("/vacations/<uuid:entry_id>/delete", methods=["POST"])
@role_required(*_EDITORS)
def fleet_delete_leave(entry_id: uuid.UUID):
    entry = db.session.get(LeaveEntry, entry_id)
    if entry is None or entry.is_deleted:
        abort(404)
    _try_delete_event(entry)
    entry.soft_delete()
    db.session.commit()
    flash(_("Leave removed."), "success")
    return _fleet_back()


@bp.route("/vacations/sync", methods=["POST"])
@role_required(*_EDITORS)
def fleet_sync():
    """Pull the connected calendar once and upsert events across the roster."""
    account = services.get_account(db.session)
    if account is None or not _google_ready():
        flash(_("Google Calendar is not connected."), "warning")
        return _fleet_back()

    from app.vacations import google

    drivers = db.session.execute(
        select(Driver).where(Driver.is_deleted.is_(False))
    ).scalars().all()
    by_uuid = {str(d.uuid): d for d in drivers}

    year = date.today().year
    try:
        events = google.pull_events(account, date(year - 1, 1, 1), date(year + 1, 12, 31))
    except Exception:  # noqa: BLE001
        flash(_("Could not reach Google Calendar."), "error")
        return _fleet_back()

    imported = 0
    for ev in events:
        driver = _match_event_driver(ev, drivers, by_uuid)
        if driver is None:
            continue
        if _upsert_event(ev, driver, account):
            imported += 1

    account.last_sync_at = services.utcnow()
    db.session.commit()
    flash(_("Synced. %(n)d new event(s) imported.", n=imported), "success")
    return _fleet_back()


# --- Leave entries ----------------------------------------------------------

@bp.route("/drivers/<uuid:driver_id>/vacations/panel")
@login_required
def panel(driver_id: uuid.UUID):
    """Re-render just the driver's vacations panel for a given ``vac_month``.

    Used by the calendar month arrows so navigation swaps the panel in place
    (HTMX) instead of reloading the page and scrolling back to the top.
    """
    driver = _get_active_driver_or_404(driver_id)
    year, month = services.parse_month_arg(request.args.get("vac_month"))
    vac = services.build_panel_context(driver, db.session, year=year, month=month)
    ent = vac["balance"]["entitlement"]
    entitlement_form = EntitlementForm(
        base_days=ent.base_days if ent else vac["balance"]["entitled"],
        carried_over_days=ent.carried_over_days if ent else 0,
        adjustment_days=ent.adjustment_days if ent else 0,
    )
    return render_template(
        "vacations/_panel.html",
        driver=driver,
        vac=vac,
        leave_form=LeaveEntryForm(),
        entitlement_form=entitlement_form,
    )


@bp.route("/drivers/<uuid:driver_id>/vacations/add", methods=["POST"])
@role_required(*_EDITORS)
def add_leave(driver_id: uuid.UUID):
    driver = _get_active_driver_or_404(driver_id)
    form = LeaveEntryForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for err in errors:
                flash(err, "error")
        return _back(driver_id)

    entry = services.create_leave(
        driver.uuid,
        db.session,
        kind=LeaveKind(form.kind.data),
        start_date=form.start_date.data,
        end_date=form.end_date.data,
        note=form.note.data or None,
    )
    db.session.commit()
    _try_push(entry)
    flash(_("Leave added (%(days)d day(s))", days=entry.counted_days), "success")
    return _back(driver_id)


@bp.route("/drivers/<uuid:driver_id>/vacations/<uuid:entry_id>/edit", methods=["POST"])
@role_required(*_EDITORS)
def edit_leave(driver_id: uuid.UUID, entry_id: uuid.UUID):
    driver = _get_active_driver_or_404(driver_id)
    from app.vacations.models import LeaveEntry

    entry = db.session.get(LeaveEntry, entry_id)
    if entry is None or entry.is_deleted or entry.driver_uuid != driver.uuid:
        abort(404)

    form = LeaveEntryForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for err in errors:
                flash(err, "error")
        return _back(driver_id)

    services.update_leave(
        entry,
        db.session,
        kind=LeaveKind(form.kind.data),
        start_date=form.start_date.data,
        end_date=form.end_date.data,
        note=form.note.data or None,
    )
    db.session.commit()
    # Mirror the edit to Google — push_event updates the existing event when the
    # leave already carries a google_event_id (creates it otherwise).
    _try_push(entry)
    flash(_("Leave updated (%(days)d day(s))", days=entry.counted_days), "success")
    return _back(driver_id)


@bp.route("/drivers/<uuid:driver_id>/vacations/<uuid:entry_id>/delete", methods=["POST"])
@role_required(*_EDITORS)
def delete_leave(driver_id: uuid.UUID, entry_id: uuid.UUID):
    driver = _get_active_driver_or_404(driver_id)
    from app.vacations.models import LeaveEntry

    entry = db.session.get(LeaveEntry, entry_id)
    if entry is None or entry.is_deleted or entry.driver_uuid != driver.uuid:
        abort(404)

    _try_delete_event(entry)
    entry.soft_delete()
    db.session.commit()
    flash(_("Leave removed."), "success")
    return _back(driver_id)


@bp.route("/drivers/<uuid:driver_id>/vacations/entitlement", methods=["POST"])
@role_required(*_EDITORS)
def set_entitlement(driver_id: uuid.UUID):
    driver = _get_active_driver_or_404(driver_id)
    form = EntitlementForm()
    year, _m = services.parse_month_arg(request.form.get("vac_month"))
    year = year or date.today().year
    if form.validate_on_submit():
        services.set_entitlement(
            driver.uuid,
            year,
            db.session,
            base_days=form.base_days.data,
            carried_over_days=form.carried_over_days.data or 0,
            adjustment_days=form.adjustment_days.data or 0,
        )
        db.session.commit()
        flash(_("Entitlement updated."), "success")
    else:
        flash(_("Invalid entitlement values."), "error")
    return _back(driver_id)


# --- Google sync ------------------------------------------------------------

@bp.route("/drivers/<uuid:driver_id>/vacations/sync", methods=["POST"])
@role_required(*_EDITORS)
def sync_now(driver_id: uuid.UUID):
    driver = _get_active_driver_or_404(driver_id)
    account = services.get_account(db.session)
    if account is None or not _google_ready():
        flash(_("Google Calendar is not connected."), "warning")
        return _back(driver_id)

    from app.vacations import google

    year = date.today().year
    try:
        events = google.pull_events(account, date(year - 1, 1, 1), date(year + 1, 12, 31))
    except Exception:  # noqa: BLE001
        flash(_("Could not reach Google Calendar."), "error")
        return _back(driver_id)

    imported = 0
    for ev in events:
        if not _belongs_to_driver(ev, driver):
            continue
        if _upsert_event(ev, driver, account):
            imported += 1

    account.last_sync_at = services.utcnow()
    db.session.commit()
    flash(_("Synced. %(n)d new event(s) imported.", n=imported), "success")
    return _back(driver_id)


@bp.route("/vacations/google/connect")
@role_required(Role.ADMIN)
def google_connect():
    from app.vacations import google

    if not google.is_configured():
        flash(_("Set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET first."), "error")
        return redirect(url_for("main.dashboard"))

    redirect_uri = url_for("vacations.google_callback", _external=True)
    auth_url, state = google.authorization_url(redirect_uri)
    session["google_oauth_state"] = state
    return redirect(auth_url)


@bp.route("/vacations/google/callback")
@role_required(Role.ADMIN)
def google_callback():
    from app.vacations import google

    if request.args.get("error"):
        flash(_("Google authorisation was cancelled."), "warning")
        return redirect(url_for("main.dashboard"))

    code = request.args.get("code")
    if not code:
        abort(400)

    redirect_uri = url_for("vacations.google_callback", _external=True)
    calendar_id = current_calendar_id()
    token = google.exchange_code(code, redirect_uri, calendar_id)

    account = services.get_account(db.session) or GoogleCalendarAccount(
        calendar_id=calendar_id
    )
    account.token = token
    account.calendar_id = calendar_id
    if account.uuid is None or account not in db.session:
        db.session.add(account)
    db.session.flush()
    account.account_email = google.account_email(account)
    db.session.commit()

    created = _backfill_existing_leaves(account)
    msg = _("Google Calendar connected (%(email)s).", email=account.account_email or "—")
    if created:
        msg += " " + _("%(n)d existing leave(s) added to the calendar.", n=created)
    flash(msg, "success")
    return redirect(url_for("main.dashboard"))


# --- helpers ----------------------------------------------------------------

def current_calendar_id() -> str:
    from flask import current_app

    return current_app.config.get("GOOGLE_CALENDAR_ID", "primary")


def _google_ready() -> bool:
    from app.vacations import google

    return google.is_configured()


def _backfill_existing_leaves(account) -> int:
    """On (re)connect, make sure existing leaves already live in the calendar.

    Pulls the calendar once, links any leave it already finds (matched by the
    ``leave_uuid`` we stamp on our own events), and *creates* the rest that are
    missing. Returns the number of events created. Best-effort: a Google failure
    leaves the local data untouched and the connection succeeds regardless.
    """
    if account is None or account.token is None or not _google_ready():
        return 0
    from app.vacations import google

    leaves = services.list_all_active_leaves(db.session)
    if not leaves:
        return 0

    # Index events already in the calendar by the leave_uuid we tag them with,
    # so a re-connect never duplicates leaves we (or a prior run) already pushed.
    existing: dict[str, dict] = {}
    try:
        span_start = min(e.start_date for e in leaves)
        span_end = max(e.end_date for e in leaves)
        for ev in google.pull_events(account, span_start, span_end):
            priv = (ev.get("raw", {}).get("extendedProperties", {}) or {}).get(
                "private", {}
            ) or {}
            if priv.get("leave_uuid"):
                existing[priv["leave_uuid"]] = ev
    except Exception:  # noqa: BLE001 — can't read the calendar; create below as best-effort
        existing = {}

    created = 0
    for leave in leaves:
        if leave.google_event_id:
            continue  # already linked locally
        match = existing.get(str(leave.uuid))
        if match is not None:
            leave.google_event_id = match["id"]
            leave.google_etag = match["etag"]
            leave.google_calendar_id = account.calendar_id
            leave.synced_at = services.utcnow()
            continue
        try:
            result = google.push_event(account, leave)
            leave.google_event_id = result["id"]
            leave.google_etag = result["etag"]
            leave.google_calendar_id = account.calendar_id
            leave.synced_at = services.utcnow()
            created += 1
        except Exception:  # noqa: BLE001 — skip the one that failed, keep going
            continue

    db.session.commit()
    return created


def _try_push(entry) -> None:
    """Best-effort mirror of a freshly-created leave to Google."""
    account = services.get_account(db.session)
    if account is None or not _google_ready():
        return
    from app.vacations import google

    try:
        result = google.push_event(account, entry)
        entry.google_event_id = result["id"]
        entry.google_etag = result["etag"]
        entry.google_calendar_id = account.calendar_id
        entry.synced_at = services.utcnow()
        db.session.commit()
    except Exception:  # noqa: BLE001 — the local record is what matters
        db.session.rollback()
        flash(_("Saved locally, but Google Calendar sync failed."), "warning")


def _belongs_to_driver(ev: dict, driver: Driver) -> bool:
    if ev.get("driver_uuid") == str(driver.uuid):
        return True
    if ev.get("driver_uuid"):
        return False  # tagged for someone else
    summary = (ev.get("summary") or "").lower()
    return bool(driver.last_name and driver.last_name.lower() in summary)


def _match_event_driver(ev: dict, drivers: list, by_uuid: dict) -> Driver | None:
    """Resolve a pulled event to a driver: by our ``leave_uuid`` tag, else by
    last name appearing in the summary (the fleet-sync counterpart of
    :func:`_belongs_to_driver`)."""
    tagged = ev.get("driver_uuid")
    if tagged:
        return by_uuid.get(tagged)
    summary = (ev.get("summary") or "").lower()
    for d in drivers:
        if d.last_name and d.last_name.lower() in summary:
            return d
    return None


def _upsert_event(ev: dict, driver: Driver, account) -> bool:
    """Create or update one pulled Google event for ``driver``. Returns True when
    a new leave was created. Shared by the per-driver and fleet sync routes."""
    existing = services.find_by_google_event(ev["id"], db.session)
    if existing is not None:
        if existing.is_deleted:
            return False
        existing.start_date = ev["start_date"]
        existing.end_date = ev["end_date"]
        existing.kind = ev["kind"]
        existing.google_etag = ev["etag"]
        existing.raw = ev["raw"]
        existing.synced_at = services.utcnow()
        services.recompute_counted_days(existing, db.session)
        return False
    entry = services.create_leave(
        driver.uuid,
        db.session,
        kind=ev["kind"],
        start_date=ev["start_date"],
        end_date=ev["end_date"],
        note=ev["summary"],
        source=LeaveSource.GOOGLE,
        google_event_id=ev["id"],
        google_calendar_id=account.calendar_id,
        raw=ev["raw"],
    )
    entry.google_etag = ev["etag"]
    entry.synced_at = services.utcnow()
    return True


def _try_delete_event(entry) -> None:
    """Best-effort removal of a leave's mirrored Google event before soft-delete.
    Shared by the per-driver and fleet delete routes."""
    account = services.get_account(db.session)
    if account and entry.google_event_id and _google_ready():
        from app.vacations import google

        try:
            google.delete_event(account, entry.google_event_id)
        except Exception:  # noqa: BLE001 — local delete must still proceed
            flash(_("Removed locally, but Google Calendar update failed."), "warning")
