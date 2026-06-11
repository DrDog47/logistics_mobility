"""Contracts-as-documents: a contract is a DriverDocument of type ``employment``
whose terms live in ``extra`` (see app.drivers.contracts)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.docs.constants import ENTITY_DRIVER
from app.docs.models import DocumentType, DriverDocument
from app.docs.status import document_status
from app.drivers.contracts import contract_documents, contract_terms, current_contract_doc
from app.drivers.models import Driver
from app.drivers.routes import _driver_stats
from app.extensions import db
from app.models.user import Role, User


def _seed(app) -> Driver:
    """Seed the employment document type + a driver, return the driver."""
    db.session.add(DocumentType(type="employment", entity_type=ENTITY_DRIVER, label="Employment"))
    db.session.add(DocumentType(type="visa", entity_type=ENTITY_DRIVER, label="Visa"))
    driver = Driver(first_name="Ivan", last_name="Ivanov", identification_id="ID-1")
    db.session.add(driver)
    db.session.commit()
    return driver


def _admin_login(app, client) -> None:
    user = User(login="admin", email="a@b.c", full_name="Admin", role=Role.ADMIN)
    user.set_password("password1")
    db.session.add(user)
    db.session.commit()
    with client.session_transaction() as s:
        s["_user_id"] = str(user.id)


def _mk_contract(driver: Driver, *, start, end, **extra) -> DriverDocument:
    doc = DriverDocument(
        driver_uuid=driver.uuid,
        document_type="employment",
        document_id=extra.pop("number", None),
        start_date=start,
        end_date=end,
        extra={
            "contract_type": extra.get("contract_type", "umowa_o_prace"),
            "base_salary_pln": extra.get("base_salary_pln", "8500.00"),
            "hours_norm": extra.get("hours_norm", 168),
        },
    )
    db.session.add(doc)
    db.session.commit()
    return doc


def test_contract_terms_and_helpers(app):
    with app.app_context():
        driver = _seed(app)
        doc = _mk_contract(driver, start=date(2024, 1, 1), end=date(2030, 1, 1), number="UoP-1")

        terms = contract_terms(doc)
        assert terms.contract_type.value == "umowa_o_prace"
        assert terms.base_salary_pln == Decimal("8500.00")
        assert terms.hours_norm == 168
        assert terms.number == "UoP-1"

        assert current_contract_doc(driver) is doc
        assert contract_documents(driver) == [doc]
        # Driver convenience properties
        assert driver.current_contract is doc
        assert driver.contract_documents == [doc]


def test_contract_excluded_from_documents_table(app):
    with app.app_context():
        driver = _seed(app)
        _mk_contract(driver, start=date(2024, 1, 1), end=date(2030, 1, 1))
        # A non-contract document
        db.session.add(
            DriverDocument(driver_uuid=driver.uuid, document_type="visa", end_date=date(2030, 1, 1))
        )
        db.session.commit()

        types = {d.document_type for d in driver.non_contract_documents}
        assert types == {"visa"}
        # but it still counts as an active document overall
        assert len(driver.active_documents) == 2


def test_driver_stats_counts_active_contract(app):
    with app.app_context():
        driver = _seed(app)
        stats = _driver_stats()
        assert stats["with_contract"] == 0

        _mk_contract(driver, start=date(2024, 1, 1), end=date(2030, 1, 1))
        stats = _driver_stats()
        assert stats["with_contract"] == 1
        assert stats["without_contract"] == 0


def test_contract_expiry_tracked_when_end_set(app):
    with app.app_context():
        driver = _seed(app)
        soon = date.today() + timedelta(days=30)
        doc = _mk_contract(driver, start=date(2024, 1, 1), end=soon)
        st = document_status(doc.document_type, doc.end_date)
        assert st.level != "not_tracked"
        assert st.days_left == 30

        # Open-ended contract → no expiry, not "not tracked"
        open_doc = _mk_contract(driver, start=date(2024, 1, 1), end=None)
        assert document_status(open_doc.document_type, open_doc.end_date).level == "no_date"


def test_add_edit_delete_contract_route(app, client):
    with app.app_context():
        driver = _seed(app)
        _admin_login(app, client)
        driver_id = driver.uuid

        # Add
        resp = client.post(
            f"/drivers/{driver_id}/contracts/new",
            data={
                "contract_type": "umowa_o_prace",
                "number": "UoP-2024-1",
                "start_date": "2024-03-15",
                "end_date": "2027-03-14",
                "base_salary_pln": "8500.00",
                "hours_norm": "168",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        doc = db.session.execute(
            db.select(DriverDocument).where(DriverDocument.document_type == "employment")
        ).scalar_one()
        assert doc.document_id == "UoP-2024-1"
        assert doc.extra["contract_type"] == "umowa_o_prace"
        assert doc.extra["base_salary_pln"] == "8500.00"
        assert doc.extra["hours_norm"] == 168
        contract_id = doc.uuid

        # Edit — change salary
        resp = client.post(
            f"/drivers/contracts/{contract_id}/edit",
            data={
                "contract_type": "umowa_o_prace",
                "number": "UoP-2024-1",
                "start_date": "2024-03-15",
                "end_date": "2027-03-14",
                "base_salary_pln": "9000.00",
                "hours_norm": "160",
            },
        )
        assert resp.status_code == 302
        db.session.expire_all()
        doc = db.session.get(DriverDocument, contract_id)
        assert doc.extra["base_salary_pln"] == "9000.00"
        assert doc.extra["hours_norm"] == 160

        # Delete — soft delete
        resp = client.post(f"/drivers/contracts/{contract_id}/delete")
        assert resp.status_code == 302
        db.session.expire_all()
        doc = db.session.get(DriverDocument, contract_id)
        assert doc.is_deleted is True
        assert db.session.get(Driver, driver_id).current_contract is None
