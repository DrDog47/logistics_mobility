"""Vacation business logic: working-day counting, balances, calendar building.

All DB-only — Google Calendar orchestration lives in ``routes.py`` calling
``google.py`` so this module stays import-cycle-free and unit-testable offline.
"""

from __future__ import annotations

import calendar as _calendar
import uuid
from datetime import UTC, date, datetime, timedelta

from flask import current_app
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.vacations.holidays import polish_holidays
from app.vacations.models import (
    LIMIT_KINDS,
    GoogleCalendarAccount,
    LeaveEntitlement,
    LeaveEntry,
    LeaveKind,
    kind_unit,
)

DEFAULT_ANNUAL_DAYS = 26  # PL: 26 days at ≥10 years seniority (overridable per driver)


# --- Holidays ---------------------------------------------------------------

def get_holidays(year: int, session: Session, country: str = "PL") -> set[date]:
    """Non-working days for ``year``: company-defined rows, else computed PL set."""
    from app.vacations.models import PublicHoliday

    rows = session.execute(
        select(PublicHoliday.day).where(
            PublicHoliday.country == country,
            PublicHoliday.is_deleted.is_(False),
            PublicHoliday.day >= date(year, 1, 1),
            PublicHoliday.day <= date(year, 12, 31),
        )
    ).scalars().all()
    if rows:
        return set(rows)
    return set(polish_holidays(year).keys())


# --- Day counting (requirement 2) -------------------------------------------

def count_days(start: date, end: date, holidays: set[date], unit: str) -> int:
    """Days in the inclusive [start, end] range.

    ``unit='calendar'`` counts every day; ``unit='working'`` counts Mon–Fri that
    are not in ``holidays``.
    """
    if end < start:
        return 0
    total = 0
    day = start
    while day <= end:
        if unit == "calendar" or (day.weekday() < 5 and day not in holidays):
            total += 1
        day += timedelta(days=1)
    return total


def recompute_counted_days(entry: LeaveEntry, session: Session) -> int:
    """Recompute and store ``entry.counted_days`` per its kind's unit."""
    unit = kind_unit(entry.kind)
    holidays: set[date] = set()
    if unit == "working":
        for yr in range(entry.start_date.year, entry.end_date.year + 1):
            holidays |= get_holidays(yr, session)
    entry.counted_days = count_days(entry.start_date, entry.end_date, holidays, unit)
    return entry.counted_days


# --- Aggregations (requirements 2 & 3) --------------------------------------

def _year_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def used_annual_days(driver_uuid: uuid.UUID, year: int, session: Session) -> int:
    """Working days of annual + on-demand leave consumed in ``year``."""
    start, end = _year_bounds(year)
    return session.scalar(
        select(func.coalesce(func.sum(LeaveEntry.counted_days), 0)).where(
            LeaveEntry.driver_uuid == driver_uuid,
            LeaveEntry.is_deleted.is_(False),
            LeaveEntry.kind.in_(LIMIT_KINDS),
            LeaveEntry.start_date >= start,
            LeaveEntry.start_date <= end,
        )
    ) or 0


def used_sick_days(driver_uuid: uuid.UUID, year: int, session: Session) -> int:
    """Calendar days of sick leave (L4) in ``year`` — informational, uncapped."""
    start, end = _year_bounds(year)
    return session.scalar(
        select(func.coalesce(func.sum(LeaveEntry.counted_days), 0)).where(
            LeaveEntry.driver_uuid == driver_uuid,
            LeaveEntry.is_deleted.is_(False),
            LeaveEntry.kind == LeaveKind.SICK,
            LeaveEntry.start_date >= start,
            LeaveEntry.start_date <= end,
        )
    ) or 0


# --- Entitlement ------------------------------------------------------------

def _default_base_days() -> int:
    return int(current_app.config.get("DEFAULT_ANNUAL_LEAVE_DAYS", DEFAULT_ANNUAL_DAYS))


def get_entitlement(
    driver_uuid: uuid.UUID, year: int, session: Session
) -> LeaveEntitlement | None:
    return session.scalar(
        select(LeaveEntitlement).where(
            LeaveEntitlement.driver_uuid == driver_uuid,
            LeaveEntitlement.year == year,
            LeaveEntitlement.is_deleted.is_(False),
        )
    )


def set_entitlement(
    driver_uuid: uuid.UUID,
    year: int,
    session: Session,
    *,
    base_days: int,
    carried_over_days: int = 0,
    adjustment_days: int = 0,
) -> LeaveEntitlement:
    """Create or update the (driver, year) allowance. Caller commits."""
    ent = get_entitlement(driver_uuid, year, session)
    if ent is None:
        ent = LeaveEntitlement(driver_uuid=driver_uuid, year=year, base_days=base_days)
        session.add(ent)
    ent.base_days = base_days
    ent.carried_over_days = carried_over_days
    ent.adjustment_days = adjustment_days
    return ent


def leave_balance(driver_uuid: uuid.UUID, year: int, session: Session) -> dict:
    """Entitled / used / remaining annual days + sick stat for ``year``."""
    ent = get_entitlement(driver_uuid, year, session)
    entitled = ent.entitled_days if ent else _default_base_days()
    used = used_annual_days(driver_uuid, year, session)
    return {
        "entitled": entitled,
        "used": used,
        "remaining": entitled - used,
        "sick_used": used_sick_days(driver_uuid, year, session),
        "entitlement": ent,
    }


# --- Leave CRUD (DB only — Google push/pull is orchestrated in routes) ------

def list_entries(
    driver_uuid: uuid.UUID, session: Session, *, year: int | None = None
) -> list[LeaveEntry]:
    stmt = select(LeaveEntry).where(
        LeaveEntry.driver_uuid == driver_uuid,
        LeaveEntry.is_deleted.is_(False),
    )
    if year is not None:
        start, end = _year_bounds(year)
        stmt = stmt.where(LeaveEntry.start_date >= start, LeaveEntry.start_date <= end)
    stmt = stmt.order_by(LeaveEntry.start_date.desc())
    return list(session.execute(stmt).scalars().all())


def create_leave(
    driver_uuid: uuid.UUID,
    session: Session,
    *,
    kind: LeaveKind,
    start_date: date,
    end_date: date,
    note: str | None = None,
    source=None,
    google_event_id: str | None = None,
    google_calendar_id: str | None = None,
    raw: dict | None = None,
) -> LeaveEntry:
    """Build a leave entry, compute its days, stage it. Caller commits."""
    from app.vacations.models import LeaveSource

    entry = LeaveEntry(
        driver_uuid=driver_uuid,
        kind=kind,
        start_date=start_date,
        end_date=end_date,
        note=note,
        source=source or LeaveSource.MANUAL,
        google_event_id=google_event_id,
        google_calendar_id=google_calendar_id,
        raw=raw,
    )
    recompute_counted_days(entry, session)
    session.add(entry)
    return entry


def list_all_active_leaves(session: Session) -> list[LeaveEntry]:
    """Every non-deleted leave across all drivers (used for Google backfill)."""
    return list(
        session.execute(
            select(LeaveEntry)
            .where(LeaveEntry.is_deleted.is_(False))
            .order_by(LeaveEntry.start_date)
        ).scalars().all()
    )


def update_leave(
    entry: LeaveEntry,
    session: Session,
    *,
    kind: LeaveKind,
    start_date: date,
    end_date: date,
    note: str | None = None,
) -> LeaveEntry:
    """Apply edited fields to a leave and recompute its day count. Caller commits.

    Recomputing ``counted_days`` here is what keeps the year's used/remaining
    balance correct after an edit (the balance is a SUM over ``counted_days``).
    """
    entry.kind = kind
    entry.start_date = start_date
    entry.end_date = end_date
    entry.note = note
    recompute_counted_days(entry, session)
    return entry


def find_by_google_event(event_id: str, session: Session) -> LeaveEntry | None:
    return session.scalar(
        select(LeaveEntry).where(LeaveEntry.google_event_id == event_id)
    )


# --- Google account ---------------------------------------------------------

def get_account(session: Session) -> GoogleCalendarAccount | None:
    """The single active Google Calendar account, if connected."""
    return session.scalar(
        select(GoogleCalendarAccount)
        .where(GoogleCalendarAccount.is_deleted.is_(False))
        .order_by(GoogleCalendarAccount.created_at.desc())
    )


# --- Calendar grid + panel context ------------------------------------------

def _entries_overlapping(
    driver_uuid: uuid.UUID, first: date, last: date, session: Session
) -> list[LeaveEntry]:
    return list(
        session.execute(
            select(LeaveEntry).where(
                LeaveEntry.driver_uuid == driver_uuid,
                LeaveEntry.is_deleted.is_(False),
                LeaveEntry.start_date <= last,
                LeaveEntry.end_date >= first,
            )
        ).scalars().all()
    )


def build_calendar(
    driver_uuid: uuid.UUID, year: int, month: int, session: Session
) -> list[list[dict]]:
    """Month grid (weeks of 7 day-dicts) with weekend/holiday/today/leave flags."""
    cal = _calendar.Calendar(firstweekday=0)  # Monday-first
    weeks = cal.monthdatescalendar(year, month)
    first, last = weeks[0][0], weeks[-1][-1]

    holidays = get_holidays(year, session)
    if first.year != year:
        holidays |= get_holidays(first.year, session)
    if last.year != year:
        holidays |= get_holidays(last.year, session)

    # date -> leave kind (annual/on-demand win the highlight over others)
    kind_by_day: dict[date, LeaveKind] = {}
    for e in _entries_overlapping(driver_uuid, first, last, session):
        d = max(e.start_date, first)
        while d <= min(e.end_date, last):
            existing = kind_by_day.get(d)
            if existing is None or (
                existing not in LIMIT_KINDS and e.kind in LIMIT_KINDS
            ):
                kind_by_day[d] = e.kind
            d += timedelta(days=1)

    today = date.today()
    grid: list[list[dict]] = []
    for week in weeks:
        row: list[dict] = []
        for d in week:
            kind = kind_by_day.get(d)
            row.append(
                {
                    "date": d,
                    "day": d.day,
                    "in_month": d.month == month,
                    "weekend": d.weekday() >= 5,
                    "holiday": d in holidays,
                    "today": d == today,
                    "kind": kind.value if kind else None,
                }
            )
        grid.append(row)
    return grid


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def build_panel_context(
    driver,
    session: Session,
    *,
    year: int | None = None,
    month: int | None = None,
) -> dict:
    """Everything the per-driver Vacations tab needs — the same data the fleet
    page shows, scoped to one driver: the year's leaves (with status), the
    annual-leave balance (used vs. planned split) + sick tally, and the
    single-driver Gantt timeline (reusing ``build_fleet_month``).
    """
    today = date.today()
    year = year or today.year
    month = month or today.month
    start, end = _year_bounds(year)

    rows = session.execute(
        select(LeaveEntry)
        .where(
            LeaveEntry.driver_uuid == driver.uuid,
            LeaveEntry.is_deleted.is_(False),
            LeaveEntry.start_date >= start,
            LeaveEntry.start_date <= end,
        )
        .order_by(LeaveEntry.start_date.desc())
    ).scalars().all()

    used = planned = sick = 0
    entries: list[dict] = []
    for e in rows:
        entries.append({"entry": e, "driver": driver, "status": entry_status(e, today)})
        if e.kind in LIMIT_KINDS:
            if e.start_date <= today:
                used += e.counted_days
            else:
                planned += e.counted_days
        elif e.kind == LeaveKind.SICK:
            sick += e.counted_days

    ent = get_entitlement(driver.uuid, year, session)
    entitled = ent.entitled_days if ent else _default_base_days()
    balance = {
        "entitled": entitled,
        "used": used,
        "planned": planned,
        "remaining": entitled - used - planned,
        "sick": sick,
        "entitlement": ent,
    }

    prev_y, prev_m = _shift_month(year, month, -1)
    next_y, next_m = _shift_month(year, month, +1)

    return {
        "driver": driver,
        "year": year,
        "month": month,
        "month_date": date(year, month, 1),
        "prev_month": f"{prev_y:04d}-{prev_m:02d}",
        "next_month": f"{next_y:04d}-{next_m:02d}",
        "balance": balance,
        "entries": entries,
        "fleet": build_fleet_month(session, year, month, driver=driver),
    }


def entry_status(entry: LeaveEntry, today: date | None = None) -> str:
    """Lifecycle status of a leave: 'completed' | 'active' | 'planned'."""
    today = today or date.today()
    if entry.end_date < today:
        return "completed"
    if entry.start_date <= today:
        return "active"
    return "planned"


def vacations_overview(session: Session, year: int) -> dict:
    """Everything the fleet vacations page lists below the timeline.

    Returns the full leave list for ``year`` (each row with its driver and
    lifecycle status), the active roster, and a per-driver annual-leave balance
    split into already-used vs. still-planned days plus the sick (L4) tally.
    """
    from app.drivers.models import Driver

    today = date.today()
    start, end = _year_bounds(year)

    rows = session.execute(
        select(LeaveEntry, Driver)
        .join(Driver, Driver.uuid == LeaveEntry.driver_uuid)
        .where(
            LeaveEntry.is_deleted.is_(False),
            Driver.is_deleted.is_(False),
            LeaveEntry.start_date >= start,
            LeaveEntry.start_date <= end,
        )
        .order_by(LeaveEntry.start_date.desc())
    ).all()

    drivers = session.execute(
        select(Driver)
        .where(Driver.is_deleted.is_(False))
        .order_by(Driver.last_name, Driver.first_name)
    ).scalars().all()

    entitlements = {
        e.driver_uuid: e
        for e in session.execute(
            select(LeaveEntitlement).where(
                LeaveEntitlement.year == year,
                LeaveEntitlement.is_deleted.is_(False),
            )
        ).scalars().all()
    }

    # Aggregate annual (used vs. planned) and sick days per driver in one pass.
    agg: dict = {d.uuid: {"used": 0, "planned": 0, "sick": 0} for d in drivers}
    entries: list[dict] = []
    for entry, driver in rows:
        entries.append(
            {"entry": entry, "driver": driver, "status": entry_status(entry, today)}
        )
        bucket = agg.get(driver.uuid)
        if bucket is None:
            continue
        if entry.kind in LIMIT_KINDS:
            key = "used" if entry.start_date <= today else "planned"
            bucket[key] += entry.counted_days
        elif entry.kind == LeaveKind.SICK:
            bucket["sick"] += entry.counted_days

    default_days = _default_base_days()
    balances: list[dict] = []
    for d in drivers:
        ent = entitlements.get(d.uuid)
        entitled = ent.entitled_days if ent else default_days
        a = agg[d.uuid]
        balances.append(
            {
                "driver": d,
                "entitled": entitled,
                "used": a["used"],
                "planned": a["planned"],
                "remaining": entitled - a["used"] - a["planned"],
                "sick": a["sick"],
            }
        )

    return {"year": year, "entries": entries, "balances": balances, "drivers": drivers}


def drivers_on_leave(session: Session, on_date: date | None = None) -> list[dict]:
    """All active drivers whose leave spans ``on_date`` (today by default).

    One row per driver (the soonest-ending overlapping leave), with the driver,
    leave kind and end date — used by the fleet vacation page's "on leave today".
    """
    from app.drivers.models import Driver

    on_date = on_date or date.today()
    rows = session.execute(
        select(LeaveEntry, Driver)
        .join(Driver, Driver.uuid == LeaveEntry.driver_uuid)
        .where(
            Driver.is_deleted.is_(False),
            LeaveEntry.is_deleted.is_(False),
            LeaveEntry.start_date <= on_date,
            LeaveEntry.end_date >= on_date,
        )
        .order_by(LeaveEntry.end_date)
    ).all()
    seen: dict = {}
    for entry, driver in rows:
        if driver.uuid in seen:
            continue
        seen[driver.uuid] = {"driver": driver, "kind": entry.kind, "end_date": entry.end_date}
    return list(seen.values())


def build_fleet_month(
    session: Session, year: int, month: int, span_months: int = 3, driver=None
) -> dict:
    """Fleet-wide timeline (own-style replacement for the Google embed).

    Spans ``span_months`` calendar months starting at (year, month) — three by
    default, so the diagram shows the selected month plus the next two. Prev/next
    still step a single month, sliding the window.

    One row per active (non-deleted) driver — the full roster shows by default,
    even drivers with no leave in the window, so the date scale always renders.
    Each row carries the bar *segments* (start column / span within the window).
    Days carry weekend/holiday/today/month-start flags for column shading, and
    ``months`` describes the month bands (label + column span) above the scale.

    Pass ``driver`` to restrict the timeline to a single driver's row — used by
    the per-driver Vacations tab, which reuses this exact component.

    Data is the synced ``LeaveEntry`` set — manual leaves plus the ones pulled
    from the connected Google Calendar — so the view reflects the calendar
    without embedding it.
    """
    from app.drivers.models import Driver

    span_months = max(1, span_months)
    first = date(year, month, 1)
    end_y, end_m = _shift_month(year, month, span_months - 1)
    last = date(end_y, end_m, _calendar.monthrange(end_y, end_m)[1])

    holidays: set[date] = set()
    for yr in range(first.year, last.year + 1):
        holidays |= get_holidays(yr, session)
    today = date.today()

    days: list[dict] = []
    d = first
    while d <= last:
        days.append(
            {
                "date": d,
                "day": d.day,
                "dow": d.weekday(),  # 0=Mon .. 6=Sun
                "weekend": d.weekday() >= 5,
                "holiday": d in holidays,
                "today": d == today,
                "month_start": d.day == 1,
            }
        )
        d += timedelta(days=1)

    # Month bands above the day scale: each spans its days as grid columns
    # (1-based from the window start), so a label sits over its block.
    months: list[dict] = []
    band_y, band_m, col = year, month, 1
    for _ in range(span_months):
        ndays = _calendar.monthrange(band_y, band_m)[1]
        months.append(
            {"month_date": date(band_y, band_m, 1), "start_col": col, "span": ndays}
        )
        col += ndays
        band_y, band_m = _shift_month(band_y, band_m, 1)

    # Full roster as rows, name-ordered (mirrors the drivers list); the dict keeps
    # insertion order so the rows stay sorted without a second pass. When a single
    # ``driver`` is given, the timeline shows just that one row.
    if driver is not None:
        drivers = [driver]
    else:
        drivers = session.execute(
            select(Driver)
            .where(Driver.is_deleted.is_(False))
            .order_by(Driver.last_name, Driver.first_name)
        ).scalars().all()
    by_driver: dict = {
        drv.uuid: {"driver": drv, "segments": []} for drv in drivers
    }

    entry_stmt = (
        select(LeaveEntry)
        .where(
            LeaveEntry.is_deleted.is_(False),
            LeaveEntry.start_date <= last,
            LeaveEntry.end_date >= first,
        )
        .order_by(LeaveEntry.start_date)
    )
    if driver is not None:
        entry_stmt = entry_stmt.where(LeaveEntry.driver_uuid == driver.uuid)
    entries = session.execute(entry_stmt).scalars().all()

    for entry in entries:
        bucket = by_driver.get(entry.driver_uuid)
        if bucket is None:
            continue  # leave belongs to a soft-deleted driver — not shown
        seg_start = max(entry.start_date, first)
        seg_end = min(entry.end_date, last)
        bucket["segments"].append(
            {
                "kind": entry.kind.value,
                "start_col": (seg_start - first).days + 1,  # 1-based grid column
                "span": (seg_end - seg_start).days + 1,
                "note": entry.note,
                "start_date": entry.start_date,
                "end_date": entry.end_date,
                "counted_days": entry.counted_days,
                "clipped_left": entry.start_date < first,
                "clipped_right": entry.end_date > last,
            }
        )

    driver_rows = list(by_driver.values())

    prev_y, prev_m = _shift_month(year, month, -1)
    next_y, next_m = _shift_month(year, month, +1)
    return {
        "year": year,
        "month": month,
        "month_date": first,
        "prev_month": f"{prev_y:04d}-{prev_m:02d}",
        "next_month": f"{next_y:04d}-{next_m:02d}",
        "this_month": f"{today.year:04d}-{today.month:02d}",
        "days": days,
        "num_days": len(days),
        "months": months,
        "rows": driver_rows,
    }


def parse_month_arg(value: str | None) -> tuple[int | None, int | None]:
    """Parse a ``YYYY-MM`` query arg → (year, month) or (None, None)."""
    if not value:
        return None, None
    try:
        y, m = value.split("-")
        year, month = int(y), int(m)
        if 1 <= month <= 12:
            return year, month
    except (ValueError, AttributeError):
        pass
    return None, None


def utcnow() -> datetime:
    return datetime.now(UTC)
