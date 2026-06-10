"""Phase 1 + Phase 2 calculator tests on synthetic data.

Phase 1 scenarios (gross + foreign wage + equalization):
  1. No posted segments → base salary only, no equalization
  2. Posted but foreign sum < base → no equalization
  3. Posted heavy → equalization triggered
  4. Mixed countries with different rate names

Phase 2 scenarios (virtual diets, sanitariaty, ZUS/PIT/net) live in
test_payroll_phase2.py.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.documents.constants import ENTITY_DRIVER
from app.documents.models import DocumentType, DriverDocument
from app.drivers.models import ContractType, Driver
from app.extensions import db
from app.payroll.calculator import calculate
from app.payroll.calculator.base import CalculatorError
from app.payroll.models import PayrollLineType, PayrollPeriod, PayrollStatus
from app.rates.services import init_registry
from app.tax.polish_params import init_polish_params
from app.trips.models import SegmentType, Trip, TripSegment, TripStatus
from app.vehicles.models import Vehicle, VehicleType


@pytest.fixture(autouse=True)
def _init_runtime_for_tests(app):
    """Load YAML data so the calculator has rates and tax params."""
    init_registry(app)
    init_polish_params(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver(base_salary_pln: Decimal, hire_date: date = date(2024, 1, 1)) -> Driver:
    driver = Driver(
        first_name="Test",
        last_name="Driver",
        nationality="POL",
        hire_date=hire_date,
    )
    db.session.add(driver)
    db.session.flush()

    _add_employment_contract(driver, base_salary_pln, hire_date)
    return driver


def _add_employment_contract(
    driver: Driver, base_salary_pln: Decimal, start: date, end: date | None = None
) -> DriverDocument:
    """A contract is now an ``employment`` document with terms in ``extra``."""
    if not db.session.execute(
        db.select(DocumentType).where(
            DocumentType.type == "employment", DocumentType.entity_type == ENTITY_DRIVER
        )
    ).scalar_one_or_none():
        db.session.add(DocumentType(type="employment", entity_type=ENTITY_DRIVER, label="Employment"))
        db.session.flush()
    doc = DriverDocument(
        driver_uuid=driver.uuid,
        document_type="employment",
        start_date=start,
        end_date=end,
        extra={
            "contract_type": ContractType.UMOWA_O_PRACE.value,
            "base_salary_pln": str(base_salary_pln),
            "hours_norm": 168,
        },
    )
    db.session.add(doc)
    db.session.flush()
    return doc


def _make_truck() -> Vehicle:
    v = Vehicle(plate="TST 12345", vehicle_type=VehicleType.TRUCK)
    db.session.add(v)
    db.session.flush()
    return v


def _make_confirmed_trip(driver: Driver, vehicle: Vehicle, segments_data: list[dict]) -> Trip:
    trip = Trip(
        driver_id=driver.id,
        vehicle_id=vehicle.id,
        trip_number="TEST-001",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 31),
        status=TripStatus.CONFIRMED,
    )
    db.session.add(trip)
    db.session.flush()

    for i, sd in enumerate(segments_data):
        seg = TripSegment(
            trip_id=trip.id,
            sequence=i,
            work_date=sd["work_date"],
            country=sd["country"],
            segment_type=sd["segment_type"],
            work_hours=sd["work_hours"],
            rate_name=sd.get("rate_name", "driver_default"),
        )
        db.session.add(seg)

    db.session.flush()
    return trip


def _make_period(driver_id: int, year: int, month: int, eur_pln: Decimal) -> PayrollPeriod:
    p = PayrollPeriod(
        driver_id=driver_id,
        year=year,
        month=month,
        eur_pln_rate=eur_pln,
        status=PayrollStatus.DRAFT,
    )
    db.session.add(p)
    db.session.flush()
    return p


# ---------------------------------------------------------------------------
# Scenario 1: No posted segments
# ---------------------------------------------------------------------------


def test_no_posted_segments_means_just_base_salary(app):
    with app.app_context():
        driver = _make_driver(base_salary_pln=Decimal("5000.00"))
        truck = _make_truck()
        # All transit/bilateral — no posting
        _make_confirmed_trip(driver, truck, [
            {
                "work_date": date(2026, 3, 5),
                "country": "DE",
                "segment_type": SegmentType.TRANSIT,
                "work_hours": Decimal("8.00"),
            },
            {
                "work_date": date(2026, 3, 6),
                "country": "DE",
                "segment_type": SegmentType.BILATERAL,
                "work_hours": Decimal("9.00"),
            },
        ])

        period = _make_period(driver.id, 2026, 3, eur_pln=Decimal("4.2800"))
        calculate(period)
        db.session.commit()

        assert period.status == PayrollStatus.CALCULATED
        assert period.foreign_wage_pln == Decimal("0.00")
        assert period.equalization_pln == Decimal("0.00")
        assert period.total_gross_pln == Decimal("5000.00")

        # Phase 2: base + ZUS + zdrowotne + PIT lines (always emitted)
        types = [l.line_type for l in period.lines]
        assert PayrollLineType.BASE_SALARY in types
        assert PayrollLineType.ZUS_EMPLOYEE in types
        assert PayrollLineType.ZDROWOTNE in types
        assert PayrollLineType.PIT_ADVANCE in types
        # No posting → no virtual diets, no sanitariaty
        assert PayrollLineType.SANITARIATY not in types
        assert PayrollLineType.VIRTUAL_DIET_ZUS not in types
        assert PayrollLineType.VIRTUAL_DIET_PIT not in types
        assert PayrollLineType.FOREIGN_WAGE not in types


# ---------------------------------------------------------------------------
# Scenario 2: Posted but foreign sum < base salary → no equalization
# ---------------------------------------------------------------------------


def test_low_posted_hours_no_equalization(app):
    """10h cabotage in DE @ 13.90 EUR = 139 EUR ≈ 595 PLN. Base 5000 PLN > foreign."""
    with app.app_context():
        driver = _make_driver(base_salary_pln=Decimal("5000.00"))
        truck = _make_truck()
        _make_confirmed_trip(driver, truck, [
            {
                "work_date": date(2026, 3, 10),
                "country": "DE",
                "segment_type": SegmentType.CABOTAGE,
                "work_hours": Decimal("10.00"),
                "rate_name": "driver_default",
            },
        ])

        period = _make_period(driver.id, 2026, 3, eur_pln=Decimal("4.2800"))
        calculate(period)
        db.session.commit()

        # 10 × 13.90 × 4.28 = 595.04 PLN — well below 5000
        assert period.foreign_wage_pln == Decimal("595.04")
        assert period.equalization_pln == Decimal("0.00")
        assert period.total_gross_pln == Decimal("5000.00")

        types = [l.line_type for l in period.lines]
        assert PayrollLineType.FOREIGN_WAGE in types
        assert PayrollLineType.BASE_SALARY in types
        assert PayrollLineType.EQUALIZATION not in types


# ---------------------------------------------------------------------------
# Scenario 3: Posted heavy → equalization kicks in
# ---------------------------------------------------------------------------


def test_heavy_posted_triggers_equalization(app):
    """160h cabotage in DE @ 13.90 EUR = 2224 EUR × 4.28 = 9518.72 PLN > 3000 base."""
    with app.app_context():
        driver = _make_driver(base_salary_pln=Decimal("3000.00"))
        truck = _make_truck()
        _make_confirmed_trip(driver, truck, [
            {
                "work_date": date(2026, 3, 15),
                "country": "DE",
                "segment_type": SegmentType.CABOTAGE,
                "work_hours": Decimal("160.00"),
                "rate_name": "driver_default",
            },
        ])

        period = _make_period(driver.id, 2026, 3, eur_pln=Decimal("4.2800"))
        calculate(period)
        db.session.commit()

        # 160 × 13.90 × 4.28 = 9518.72
        assert period.foreign_wage_pln == Decimal("9518.72")
        assert period.equalization_pln == Decimal("6518.72")
        assert period.total_gross_pln == Decimal("9518.72")

        types = [l.line_type for l in period.lines]
        assert PayrollLineType.EQUALIZATION in types


# ---------------------------------------------------------------------------
# Scenario 4: Multiple countries, different rate names
# ---------------------------------------------------------------------------


def test_mixed_countries_and_rates(app):
    with app.app_context():
        driver = _make_driver(base_salary_pln=Decimal("4000.00"))
        truck = _make_truck()
        _make_confirmed_trip(driver, truck, [
            {
                "work_date": date(2026, 3, 5),
                "country": "DE",
                "segment_type": SegmentType.CABOTAGE,
                "work_hours": Decimal("20.00"),
                "rate_name": "driver_default",
            },
            {
                "work_date": date(2026, 3, 8),
                "country": "FR",
                "segment_type": SegmentType.CROSS_TRADE,
                "work_hours": Decimal("15.00"),
                "rate_name": "driver_coef_150m",
            },
            {
                "work_date": date(2026, 3, 12),
                "country": "DE",
                "segment_type": SegmentType.BILATERAL,
                "work_hours": Decimal("30.00"),
            },
            {
                "work_date": date(2026, 3, 18),
                "country": "IT",
                "segment_type": SegmentType.CABOTAGE,
                "work_hours": Decimal("5.00"),
                "rate_name": "driver_b3",
            },
        ])

        period = _make_period(driver.id, 2026, 3, eur_pln=Decimal("4.3000"))
        calculate(period)
        db.session.commit()

        # DE: 20 × 13.90 × 4.30 = 1195.40
        # FR: 15 × 12.43 × 4.30 = 801.74 (15*12.43=186.45; *4.30=801.735→801.74)
        # IT: 5 × 11.44 × 4.30 = 245.96
        # Total foreign = 2243.10
        assert period.foreign_wage_pln == Decimal("2243.10")
        assert period.equalization_pln == Decimal("0.00")
        assert period.total_gross_pln == Decimal("4000.00")

        foreign_lines = [l for l in period.lines if l.line_type == PayrollLineType.FOREIGN_WAGE]
        assert len(foreign_lines) == 3
        countries = sorted(l.country for l in foreign_lines)
        assert countries == ["DE", "FR", "IT"]

        for line in foreign_lines:
            assert line.snapshot_id is not None


# ---------------------------------------------------------------------------
# Edge: only DRAFT trips → not included
# ---------------------------------------------------------------------------


def test_draft_trips_excluded(app):
    with app.app_context():
        driver = _make_driver(base_salary_pln=Decimal("5000.00"))
        truck = _make_truck()
        trip = _make_confirmed_trip(driver, truck, [
            {
                "work_date": date(2026, 3, 10),
                "country": "DE",
                "segment_type": SegmentType.CABOTAGE,
                "work_hours": Decimal("100.00"),
            },
        ])
        trip.status = TripStatus.DRAFT
        db.session.flush()

        period = _make_period(driver.id, 2026, 3, eur_pln=Decimal("4.2800"))
        calculate(period)
        db.session.commit()

        assert period.foreign_wage_pln == Decimal("0.00")
        assert period.total_gross_pln == Decimal("5000.00")


# ---------------------------------------------------------------------------
# Edge: zero rate raises clearly
# ---------------------------------------------------------------------------


def test_zero_exchange_rate_raises(app):
    with app.app_context():
        driver = _make_driver(base_salary_pln=Decimal("5000.00"))
        period = _make_period(driver.id, 2026, 3, eur_pln=Decimal("0"))

        with pytest.raises(CalculatorError, match="exchange rate"):
            calculate(period)
