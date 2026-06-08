"""Database models.

All models must be imported here so that Alembic autogenerate can see them.
"""

from app.drivers.models import Driver, DriverContract  # noqa: F401
from app.models.user import User  # noqa: F401
from app.payroll.models import PayrollLine, PayrollPeriod  # noqa: F401
from app.rates.models import CountryRateSnapshot  # noqa: F401
from app.services.nbp_models import NbpRate  # noqa: F401
from app.trips.models import Trip, TripSegment  # noqa: F401
from app.vehicles.models import Vehicle  # noqa: F401

__all__ = [
    "User",
    "Driver",
    "DriverContract",
    "Vehicle",
    "Trip",
    "TripSegment",
    "PayrollPeriod",
    "PayrollLine",
    "CountryRateSnapshot",
    "NbpRate",
]
