"""Vehicles list page: fleet KPIs, live search, and the HTMX table fragment."""

from __future__ import annotations

from datetime import date, timedelta

from app.documents.models import DocumentType, VehicleDocument
from app.extensions import db
from app.models.user import Role, User
from app.organisations.models import Organisation
from app.vehicles.models import Vehicle, VehicleType
from app.vehicles.routes import _vehicle_stats


def _org() -> Organisation:
    org = Organisation(
        name="ACME", national_id="PL123", country="POL", city="Bialystok", address="Main 1"
    )
    db.session.add(org)
    db.session.flush()
    return org


def _vehicle(org, vtype, vin, plate, brand="Volvo", model="FH") -> Vehicle:
    v = Vehicle(
        vehicle_type=vtype, vin=vin, brand=brand, model=model,
        registration_plate=plate, organisation_uuid=org.uuid,
    )
    db.session.add(v)
    db.session.flush()
    return v


def _doc_type(code: str) -> None:
    db.session.add(DocumentType(type=code, entity_type="vehicle", label=code))


def _login(client, role=Role.FLEET_MANAGER) -> User:
    u = User(login="u", email="u@e.c", full_name="U", role=role)
    u.set_password("password1")
    db.session.add(u)
    db.session.commit()
    with client.session_transaction() as s:
        s["_user_id"] = str(u.id)
    return u


def test_vehicle_stats(app):
    with app.app_context():
        org = _org()
        t1 = _vehicle(org, VehicleType.TRACTOR, "1HGCM82633A004352", "WB1234A")
        _vehicle(org, VehicleType.TRACTOR, "1HGCM82633A004353", "WB1235A", brand="MAN")
        _vehicle(org, VehicleType.TRAILER, "1HGCM82633A004354", "WB9999T", brand="Schmitz")
        _doc_type("tech_passport")
        _doc_type("insurance")
        # t1 is complete (has reg cert) + has an insurance expiring in 10 days.
        db.session.add(VehicleDocument(vehicle_uuid=t1.uuid, document_type="tech_passport"))
        db.session.add(VehicleDocument(
            vehicle_uuid=t1.uuid, document_type="insurance",
            end_date=date.today() + timedelta(days=10),
        ))
        db.session.commit()

        s = _vehicle_stats()
        assert s["total"] == 3
        assert s["tractors"] == 2
        assert s["trailers"] == 1
        assert s["without_tech_passport"] == 2   # only t1 has a registration certificate
        assert s["expiring_docs"] == 1
        assert s["expiring_list"][0]["vehicle_name"] == "WB1234A"


def test_list_page_and_htmx_search(app, client):
    with app.app_context():
        org = _org()
        _vehicle(org, VehicleType.TRACTOR, "1HGCM82633A004352", "WB1234A", brand="Volvo")
        _vehicle(org, VehicleType.TRACTOR, "1HGCM82633A004353", "WB1235A", brand="MAN")
        db.session.commit()
        _login(client)

        # Full page: KPI strip + vehicle-specific labels, no vacations metric.
        r = client.get("/vehicles/?lang=en")
        html = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "Total vehicles" in html
        assert "Tractors" in html and "Trailers" in html
        assert "Without registration certificate" in html
        assert "On leave" not in html  # vehicles are not human — no vacations
        assert 'id="vehicles-table"' in html

        # HTMX request → only the table fragment; live search filters by brand.
        rf = client.get("/vehicles/?q=volvo", headers={"HX-Request": "true"})
        frag = rf.get_data(as_text=True)
        assert rf.status_code == 200
        assert frag.lstrip().startswith("<table")
        assert "WB1234A" in frag and "WB1235A" not in frag
