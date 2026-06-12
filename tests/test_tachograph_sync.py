"""Tachograph card number stays in step between the driver and its document.

Rules:
* a confirmed/edited tachograph-card document writes its number to the driver
  (DriverDocument.sync_tachograph_number_to_driver);
* editing the driver's number writes it back to the active document
  (Driver.sync_tachograph_card_number_to_documents);
* the two must always match (Driver.tachograph_mismatch).
"""

from __future__ import annotations

from app.docs.constants import ENTITY_DRIVER, TACHOGRAPH_DOC_TYPE
from app.docs.models import DocumentType, DriverDocument
from app.drivers.models import Driver
from app.extensions import db
from app.models.user import Role, User
from app.organisations.models import Organisation

VALID = "A1B2C3D4E5F6G7H8"          # exactly 16 latin alnum
OTHER = "Z9Y8X7W6V5U4T3S2"          # another valid 16-char number


def _seed_tacho_type() -> None:
    db.session.add(
        DocumentType(type=TACHOGRAPH_DOC_TYPE, entity_type=ENTITY_DRIVER, label="Tachograph")
    )
    db.session.commit()


def _driver(identification_id: str = "ID-1", **kw) -> Driver:
    driver = Driver(
        first_name="Ivan", last_name="Ivanov", identification_id=identification_id, **kw
    )
    db.session.add(driver)
    db.session.commit()
    return driver


def _tacho_doc(driver: Driver, number: str | None) -> DriverDocument:
    doc = DriverDocument(
        driver_uuid=driver.uuid, document_type=TACHOGRAPH_DOC_TYPE, document_id=number
    )
    db.session.add(doc)
    db.session.commit()
    return doc


def _admin_login(client) -> None:
    user = User(login="admin", email="a@b.c", full_name="Admin", role=Role.ADMIN)
    user.set_password("password1")
    db.session.add(user)
    db.session.commit()
    with client.session_transaction() as s:
        s["_user_id"] = str(user.id)


# --- Rule 1: document → driver ----------------------------------------------

def test_document_number_syncs_to_driver(app):
    with app.app_context():
        _seed_tacho_type()
        driver = _driver()
        doc = _tacho_doc(driver, VALID)
        assert doc.sync_tachograph_number_to_driver(driver) is True
        assert driver.tachograph_card_number == VALID
        # Idempotent — already in step.
        assert doc.sync_tachograph_number_to_driver(driver) is False


def test_invalid_document_number_does_not_sync(app):
    with app.app_context():
        _seed_tacho_type()
        driver = _driver()
        doc = _tacho_doc(driver, "TOOSHORT")  # not 16 chars
        assert doc.sync_tachograph_number_to_driver(driver) is False
        assert driver.tachograph_card_number is None


def test_non_tacho_document_does_not_sync(app):
    with app.app_context():
        db.session.add(DocumentType(type="visa", entity_type=ENTITY_DRIVER, label="Visa"))
        db.session.commit()
        driver = _driver()
        doc = DriverDocument(
            driver_uuid=driver.uuid, document_type="visa", document_id=VALID
        )
        db.session.add(doc)
        db.session.commit()
        assert doc.sync_tachograph_number_to_driver(driver) is False
        assert driver.tachograph_card_number is None


# --- Rule 3: driver → document ----------------------------------------------

def test_driver_number_syncs_to_documents(app):
    with app.app_context():
        _seed_tacho_type()
        driver = _driver(tachograph_card_number=VALID)
        doc = _tacho_doc(driver, OTHER)
        updated = driver.sync_tachograph_card_number_to_documents()
        assert doc in updated
        assert doc.document_id == VALID


def test_edit_driver_route_updates_document(app, client):
    with app.app_context():
        _seed_tacho_type()
        _admin_login(client)
        org = Organisation(
            national_id="NIP1", name="ACME", country="POL", city="Warsaw", address="x"
        )
        db.session.add(org)
        db.session.commit()
        driver = _driver(identification_id="ID1", organisation_uuid=org.uuid, birth_date=None)
        doc = _tacho_doc(driver, OTHER)
        doc_id = doc.uuid

        resp = client.post(
            f"/drivers/{driver.uuid}/edit",
            data={
                "first_name": "Ivan",
                "last_name": "Ivanov",
                "birth_date": "1990-01-01",
                "nationality": "POL",
                "organisation_uuid": str(org.uuid),
                "identification_id": "ID1",
                "tachograph_card_number": VALID,
                "hire_date": "2024-01-01",
            },
        )
        assert resp.status_code in (302, 303)
        db.session.expire_all()
        assert db.session.get(DriverDocument, doc_id).document_id == VALID
        assert db.session.get(Driver, driver.uuid).tachograph_card_number == VALID


# --- Rule 4: consistency check ----------------------------------------------

def test_tachograph_mismatch_detected_and_resolved(app):
    with app.app_context():
        _seed_tacho_type()
        driver = _driver(tachograph_card_number=VALID)
        _tacho_doc(driver, OTHER)
        assert driver.tachograph_mismatch() is not None  # numbers differ
        driver.sync_tachograph_card_number_to_documents()
        assert driver.tachograph_mismatch() is None       # back in step


def test_edit_file_reattaches_to_another_document(app, client):
    """The file edit page can move a file to a different document of the driver."""
    from app.docs.models import DriverDocument, DriverFile

    with app.app_context():
        db.session.add(DocumentType(type="passport", entity_type=ENTITY_DRIVER, label="P"))
        db.session.add(DocumentType(type="visa", entity_type=ENTITY_DRIVER, label="V"))
        db.session.commit()
        _admin_login(client)
        driver = _driver()
        doc_a = DriverDocument(driver_uuid=driver.uuid, document_type="passport")
        doc_b = DriverDocument(driver_uuid=driver.uuid, document_type="visa")
        db.session.add_all([doc_a, doc_b])
        db.session.commit()
        file = DriverFile(document_uuid=doc_a.uuid, file_link="Drivers/x/scan.pdf")
        db.session.add(file)
        db.session.commit()
        file_id, doc_b_id = file.uuid, doc_b.uuid

        resp = client.post(
            f"/driver-files/{file_id}/edit",
            data={"document_uuid": str(doc_b_id), "file_link": "Drivers/x/scan.pdf"},
        )
        assert resp.status_code in (302, 303)
        db.session.expire_all()
        assert db.session.get(DriverFile, file_id).document_uuid == doc_b_id


def test_no_mismatch_without_tacho_document(app):
    with app.app_context():
        _seed_tacho_type()
        driver = _driver(tachograph_card_number=VALID)
        assert driver.tachograph_mismatch() is None
