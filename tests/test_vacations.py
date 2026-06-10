"""Tests for driver vacation tracking: holidays, working-day counts, balances."""

from datetime import date

import pytest

from app.extensions import db
from app.vacations import services
from app.vacations.holidays import easter_sunday, polish_holidays
from app.vacations.models import LeaveEntry, LeaveKind, PublicHoliday


def _make_driver(id_="VAC0001"):
    from app.drivers.models import Driver

    driver = Driver(
        first_name="Test",
        last_name="Kowalski",
        nationality="POL",
        identification_id=id_,
        hire_date=date(2020, 1, 1),
    )
    db.session.add(driver)
    db.session.flush()
    return driver


# --- Holidays ---------------------------------------------------------------

def test_easter_sunday_known_years():
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2024) == date(2024, 3, 31)


def test_polish_holidays_movable_and_fixed():
    h = polish_holidays(2026)
    assert date(2026, 1, 1) in h          # New Year
    assert date(2026, 4, 6) in h          # Easter Monday
    assert date(2026, 6, 4) in h          # Corpus Christi (Easter + 60)
    assert date(2026, 12, 26) in h        # 2nd day of Christmas


# --- count_days -------------------------------------------------------------

def test_count_days_working_vs_calendar():
    mon, sun = date(2026, 6, 8), date(2026, 6, 14)  # full Mon–Sun week
    assert services.count_days(mon, sun, set(), "calendar") == 7
    assert services.count_days(mon, sun, set(), "working") == 5


def test_count_days_excludes_holidays_for_working_unit():
    mon, fri = date(2026, 6, 8), date(2026, 6, 12)
    holiday = {date(2026, 6, 10)}  # a Wednesday
    assert services.count_days(mon, fri, holiday, "working") == 4
    assert services.count_days(mon, fri, holiday, "calendar") == 5  # holidays ignored


def test_count_days_reversed_range_is_zero():
    assert services.count_days(date(2026, 6, 10), date(2026, 6, 8), set(), "working") == 0


def test_update_leave_recomputes_days_and_balance(app):
    with app.app_context():
        driver = _make_driver()
        services.set_entitlement(driver.uuid, 2026, db.session, base_days=26)
        # Jun 8–12 2026 is a clean Mon–Fri week (no holiday) → 5 working days.
        entry = services.create_leave(
            driver.uuid, db.session, kind=LeaveKind.ANNUAL,
            start_date=date(2026, 6, 8), end_date=date(2026, 6, 12),
        )
        db.session.commit()
        assert entry.counted_days == 5
        assert services.leave_balance(driver.uuid, 2026, db.session)["remaining"] == 21

        # Shrink to a single day → recompute drops used, raises remaining.
        services.update_leave(
            entry, db.session, kind=LeaveKind.ANNUAL,
            start_date=date(2026, 6, 8), end_date=date(2026, 6, 8), note="shortened",
        )
        db.session.commit()
        assert entry.counted_days == 1
        assert entry.note == "shortened"
        bal = services.leave_balance(driver.uuid, 2026, db.session)
        assert bal["used"] == 1 and bal["remaining"] == 25


# --- Holidays source (DB override + fallback) -------------------------------

def test_get_holidays_falls_back_to_computed(app):
    with app.app_context():
        result = services.get_holidays(2026, db.session)
        assert date(2026, 4, 6) in result  # computed Easter Monday


def test_get_holidays_uses_db_rows_when_present(app):
    with app.app_context():
        db.session.add(PublicHoliday(country="PL", day=date(2026, 7, 1), name="Company day"))
        db.session.commit()
        result = services.get_holidays(2026, db.session)
        assert result == {date(2026, 7, 1)}  # DB rows win, computed set ignored


# --- Balances (requirements 2 & 3) ------------------------------------------

def test_annual_leave_counts_working_days_and_reduces_remaining(app):
    with app.app_context():
        driver = _make_driver()
        services.create_leave(
            driver.uuid, db.session,
            kind=LeaveKind.ANNUAL,
            start_date=date(2026, 6, 8), end_date=date(2026, 6, 12),  # Mon–Fri
        )
        db.session.commit()

        bal = services.leave_balance(driver.uuid, 2026, db.session)
        assert bal["entitled"] == 26       # config default
        assert bal["used"] == 5
        assert bal["remaining"] == 21


def test_sick_leave_is_separate_and_uncapped(app):
    with app.app_context():
        driver = _make_driver("VAC0002")
        services.create_leave(
            driver.uuid, db.session,
            kind=LeaveKind.SICK,
            start_date=date(2026, 6, 15), end_date=date(2026, 6, 21),  # Mon–Sun
        )
        db.session.commit()

        bal = services.leave_balance(driver.uuid, 2026, db.session)
        assert bal["used"] == 0            # sick does NOT touch the annual cap
        assert bal["remaining"] == 26
        assert bal["sick_used"] == 7       # counted in calendar days


def test_entitlement_override_changes_remaining(app):
    with app.app_context():
        driver = _make_driver("VAC0003")
        services.set_entitlement(
            driver.uuid, 2026, db.session,
            base_days=20, carried_over_days=4, adjustment_days=0,
        )
        db.session.commit()

        bal = services.leave_balance(driver.uuid, 2026, db.session)
        assert bal["entitled"] == 24
        assert bal["remaining"] == 24


def test_counted_days_cached_on_entry(app):
    with app.app_context():
        driver = _make_driver("VAC0004")
        entry = services.create_leave(
            driver.uuid, db.session,
            kind=LeaveKind.ANNUAL,
            start_date=date(2026, 6, 8), end_date=date(2026, 6, 12),
        )
        db.session.commit()
        assert entry.counted_days == 5
        stored = db.session.get(LeaveEntry, entry.uuid)
        assert stored.counted_days == 5


# --- Calendar grid ----------------------------------------------------------

def test_build_calendar_marks_vacation_holiday_and_weekend(app):
    with app.app_context():
        driver = _make_driver("VAC0005")
        services.create_leave(
            driver.uuid, db.session,
            kind=LeaveKind.ANNUAL,
            start_date=date(2026, 6, 15), end_date=date(2026, 6, 19),
        )
        db.session.commit()

        weeks = services.build_calendar(driver.uuid, 2026, 6, db.session)
        days = {d["date"]: d for week in weeks for d in week}

        assert days[date(2026, 6, 15)]["kind"] == "annual"
        assert days[date(2026, 6, 4)]["holiday"] is True       # Corpus Christi
        assert days[date(2026, 6, 13)]["weekend"] is True      # Saturday
        # spill-over days from adjacent months are flagged out-of-month
        assert any(not d["in_month"] for d in days.values())


# --- Misc -------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-06", (2026, 6)),
        ("2026-13", (None, None)),
        ("garbage", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_month_arg(value, expected):
    assert services.parse_month_arg(value) == expected
