"""Database models.

All models must be imported here so that Alembic autogenerate can see them.
"""

from app.documents.models import (  # noqa: F401
    Document,
    DocumentType,
    DriverDocument,
    DriverFile,
    VehicleDocument,
)
from app.drivers.models import Driver  # noqa: F401
from app.models.user import User  # noqa: F401
from app.organisations.models import Organisation  # noqa: F401
from app.rates.models import CountryRateSnapshot  # noqa: F401
from app.services.nbp_models import NbpRate  # noqa: F401
from app.trips.models import Trip, TripSegment  # noqa: F401
from app.vacations.models import (  # noqa: F401
    GoogleCalendarAccount,
    LeaveEntitlement,
    LeaveEntry,
    PublicHoliday,
)
from app.vehicles.models import Vehicle  # noqa: F401

# NOTE: payroll models (PayrollPeriod, PayrollLine) are intentionally NOT
# imported for now — keeping them out of the metadata parks payroll and its
# tables out of the schema/migration while the document system is built.

__all__ = [
    "User",
    "Organisation",
    "Driver",
    "Document",
    "DocumentType",
    "DriverDocument",
    "DriverFile",
    "Vehicle",
    "VehicleDocument",
    "Trip",
    "TripSegment",
    "CountryRateSnapshot",
    "NbpRate",
    "LeaveEntitlement",
    "LeaveEntry",
    "PublicHoliday",
    "GoogleCalendarAccount",
]
