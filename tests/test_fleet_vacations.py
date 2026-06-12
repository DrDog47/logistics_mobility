"""Fleet vacation calendar page + 'on leave today' + Settings page."""

from __future__ import annotations

from datetime import date, timedelta

from app.extensions import db
from app.models.user import Role, User
from app.drivers.models import Driver
from app.vacations import services
from app.vacations.models import LeaveEntry, LeaveKind


def _driver(first="Ivan", last="Ivanov", idn="ID-1") -> Driver:
    d = Driver(first_name=first, last_name=last, identification_id=idn)
    db.session.add(d)
    db.session.commit()
    return d


def _leave(driver, start, end, kind=LeaveKind.ANNUAL) -> LeaveEntry:
    e = LeaveEntry(driver_uuid=driver.uuid, kind=kind, start_date=start, end_date=end)
    db.session.add(e)
    db.session.commit()
    return e


def _login(client, role=Role.ADMIN, login="u", email="u@e.c") -> User:
    u = User(login=login, email=email, full_name="U", role=role)
    u.set_password("password1")
    db.session.add(u)
    db.session.commit()
    with client.session_transaction() as s:
        s["_user_id"] = str(u.id)
    return u


def test_drivers_on_leave_today(app):
    with app.app_context():
        today = date.today()
        d1 = _driver("Ivan", "Ivanov", "ID-1")
        d2 = _driver("Adam", "Nowak", "ID-2")
        d3 = _driver("Piotr", "Kowal", "ID-3")
        _leave(d1, today - timedelta(days=2), today + timedelta(days=3))  # spans today
        _leave(d2, today, today)                                          # today only
        _leave(d3, today + timedelta(days=5), today + timedelta(days=9))  # future

        on_leave = services.drivers_on_leave(db.session, today)
        names = {x["driver"].full_name for x in on_leave}
        assert names == {"Ivan Ivanov", "Adam Nowak"}


def test_fleet_add_edit_delete_leave(app, client):
    with app.app_context():
        d = _driver()
        _login(client, role=Role.FLEET_MANAGER)

        # Add (fleet page form posts driver_uuid + dates) → reuses create_leave.
        r = client.post(
            "/vacations/add",
            data={
                "driver_uuid": str(d.uuid),
                "kind": "annual",
                "start_date": "2026-06-10",
                "end_date": "2026-06-12",
                "note": "trip",
            },
        )
        assert r.status_code == 302
        entries = services.list_entries(d.uuid, db.session)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.note == "trip"

        # Edit (driver fixed by the entry id) → reuses update_leave.
        r = client.post(
            f"/vacations/{entry.uuid}/edit",
            data={
                "kind": "sick",
                "start_date": "2026-06-10",
                "end_date": "2026-06-15",
                "note": "L4",
            },
        )
        assert r.status_code == 302
        db.session.refresh(entry)
        assert entry.kind == LeaveKind.SICK
        assert entry.end_date == date(2026, 6, 15)

        # Delete → soft delete.
        r = client.post(f"/vacations/{entry.uuid}/delete")
        assert r.status_code == 302
        db.session.refresh(entry)
        assert entry.is_deleted
        assert services.list_entries(d.uuid, db.session) == []


def test_driver_vacations_tab_renders(app, client):
    with app.app_context():
        d = _driver()
        _leave(d, date.today() - timedelta(days=1), date.today() + timedelta(days=1))
        _login(client, role=Role.FLEET_MANAGER)
        r = client.get(f"/drivers/{d.uuid}?lang=en")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        # Shared vacations module rendered on the driver tab.
        assert "vac-page" in html
        assert "Add leave" in html
        assert "vmonth-bar" in html          # single-driver Gantt
        assert "vac-type-pill" in html       # leaves table
        # Google connect/sync must NOT appear on the per-driver tab.
        assert "Sync now" not in html
        assert "Not connected" not in html


def test_driver_panel_add_leave_via_module(app, client):
    with app.app_context():
        d = _driver()
        _login(client, role=Role.FLEET_MANAGER)
        # The per-driver modal posts to the existing add_leave route.
        r = client.post(
            f"/drivers/{d.uuid}/vacations/add",
            data={"kind": "annual", "start_date": "2026-06-10", "end_date": "2026-06-12"},
        )
        assert r.status_code == 302
        assert len(services.list_entries(d.uuid, db.session)) == 1


def test_vacations_overview_balances(app):
    with app.app_context():
        today = date.today()
        d = _driver()
        # one used (past) and one planned (future) annual leave — go through
        # create_leave so counted_days is computed (the raw _leave helper skips it)
        services.create_leave(
            d.uuid, db.session, kind=LeaveKind.ANNUAL,
            start_date=today - timedelta(days=10), end_date=today - timedelta(days=8),
        )
        services.create_leave(
            d.uuid, db.session, kind=LeaveKind.ANNUAL,
            start_date=today + timedelta(days=10), end_date=today + timedelta(days=12),
        )
        db.session.commit()
        ov = services.vacations_overview(db.session, today.year)
        assert len(ov["entries"]) == 2
        bal = next(b for b in ov["balances"] if b["driver"].uuid == d.uuid)
        assert bal["used"] > 0
        assert bal["planned"] > 0
        assert bal["remaining"] == bal["entitled"] - bal["used"] - bal["planned"]


def test_calendar_page_renders(app, client):
    with app.app_context():
        d = _driver()
        _leave(d, date.today() - timedelta(days=1), date.today() + timedelta(days=1))
        _login(client, role=Role.FLEET_MANAGER)
        r = client.get("/vacations/?lang=en")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "On leave today" in html
        assert "Ivan Ivanov" in html
        # own-style month timeline (no Google iframe): driver row + leave bar
        assert "iframe" not in html
        assert "vmonth" in html
        assert "vmonth-bar" in html


def test_fleet_month_shows_full_active_roster(app):
    with app.app_context():
        on = _driver("Ivan", "Ivanov", "ID-1")
        _driver("Adam", "Nowak", "ID-2")  # no leave at all
        _leave(on, date(2026, 6, 10), date(2026, 6, 12))
        fleet = services.build_fleet_month(db.session, 2026, 6)
        # both active drivers are rows, name-ordered, even the one with no leave
        names = [r["driver"].full_name for r in fleet["rows"]]
        assert names == ["Ivan Ivanov", "Adam Nowak"]
        by_name = {r["driver"].full_name: r for r in fleet["rows"]}
        assert by_name["Adam Nowak"]["segments"] == []
        assert len(by_name["Ivan Ivanov"]["segments"]) == 1


def test_fleet_month_timeline_spans_three_months(app):
    with app.app_context():
        d = _driver()
        # Window starts at the selected month (June) and spans 3 months: Jun–Aug.
        # A leave spilling out of June at the start is clipped left; one ending
        # inside the window (Jul 3) is NOT clipped any more.
        _leave(d, date(2026, 5, 28), date(2026, 6, 4))
        _leave(d, date(2026, 6, 20), date(2026, 7, 3))
        fleet = services.build_fleet_month(db.session, 2026, 6)
        assert fleet["num_days"] == 30 + 31 + 31  # Jun + Jul + Aug
        # prev/next still step a single month (sliding window)
        assert fleet["prev_month"] == "2026-05"
        assert fleet["next_month"] == "2026-07"
        # month bands describe each month's column span
        assert [m["span"] for m in fleet["months"]] == [30, 31, 31]
        assert [m["start_col"] for m in fleet["months"]] == [1, 31, 62]
        assert len(fleet["rows"]) == 1
        segs = sorted(fleet["rows"][0]["segments"], key=lambda s: s["start_col"])
        # first leave: clipped left, starts at col 1 (Jun 1), spans Jun 1–4
        assert segs[0]["start_col"] == 1
        assert segs[0]["span"] == 4
        assert segs[0]["clipped_left"] and not segs[0]["clipped_right"]
        # second leave: Jun 20 → Jul 3, fully inside the window, not clipped
        assert segs[1]["start_col"] == 20
        assert segs[1]["span"] == 14
        assert not segs[1]["clipped_left"] and not segs[1]["clipped_right"]


def test_fleet_month_timeline_clips_to_window(app):
    with app.app_context():
        d = _driver()
        # A leave running past the end of the 3-month window (Aug 31) is clipped.
        _leave(d, date(2026, 8, 25), date(2026, 9, 10))
        fleet = services.build_fleet_month(db.session, 2026, 6)
        seg = fleet["rows"][0]["segments"][0]
        assert seg["clipped_right"] and not seg["clipped_left"]
        # Aug 25 is the 62 + 24 = 86th column; spans to Aug 31 (end of window)
        assert seg["start_col"] == 86
        assert seg["span"] == 7


def test_connect_backfills_missing_leaves(app, monkeypatch):
    """On connect: link leaves already in the calendar, create the missing ones."""
    from app.vacations import google
    from app.vacations.models import GoogleCalendarAccount
    from app.vacations.routes import _backfill_existing_leaves

    with app.app_context():
        d = _driver()
        already = _leave(d, date(2026, 6, 1), date(2026, 6, 3))   # already on calendar
        already.google_event_id = "evt-existing"
        in_cal = _leave(d, date(2026, 6, 10), date(2026, 6, 12))  # present but unlinked
        missing = _leave(d, date(2026, 6, 20), date(2026, 6, 22))  # not on calendar yet
        db.session.commit()

        account = GoogleCalendarAccount(calendar_id="primary", token={"t": 1})
        db.session.add(account)
        db.session.commit()

        # Calendar already contains an event tagged with in_cal's leave_uuid.
        def fake_pull(acc, start, end):
            return [
                {
                    "id": "evt-incal",
                    "etag": "etag-incal",
                    "raw": {"extendedProperties": {"private": {"leave_uuid": str(in_cal.uuid)}}},
                }
            ]

        pushed = []

        def fake_push(acc, leave):
            pushed.append(leave.uuid)
            return {"id": f"evt-new-{leave.uuid}", "etag": "etag-new"}

        monkeypatch.setattr(google, "is_configured", lambda: True)
        monkeypatch.setattr(google, "pull_events", fake_pull)
        monkeypatch.setattr(google, "push_event", fake_push)

        created = _backfill_existing_leaves(account)

        assert created == 1                       # only the genuinely missing one
        assert pushed == [missing.uuid]           # already-linked + in-calendar not re-pushed
        assert already.google_event_id == "evt-existing"            # untouched
        assert in_cal.google_event_id == "evt-incal"               # linked, not created
        assert missing.google_event_id == f"evt-new-{missing.uuid}"  # created


def test_fleet_strings_localised():
    """The new fleet/settings strings are present in the PL & RU catalogs."""
    import gettext

    pl = gettext.translation("messages", "app/translations", languages=["pl"])
    ru = gettext.translation("messages", "app/translations", languages=["ru"])
    assert pl.gettext("On leave today") == "Dziś na urlopie"
    assert ru.gettext("On leave today") == "Сегодня в отпуске"
    assert pl.gettext("Settings") == "Ustawienia"
    assert ru.gettext("Vacation calendar") == "Календарь отпусков"


def test_settings_admin_only(app, client):
    with app.app_context():
        _login(client, role=Role.FLEET_MANAGER)
        assert client.get("/settings").status_code in (302, 403)

    with app.app_context():
        _login(client, role=Role.ADMIN, login="admin", email="a@e.c")
        r = client.get("/settings?lang=en")
        assert r.status_code == 200
        assert "Google Calendar" in r.get_data(as_text=True)
