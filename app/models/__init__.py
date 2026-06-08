"""Database models.

All models must be imported here so that Alembic autogenerate can see them.
"""

from app.documents.models import DocumentType, DriverDocument, VehicleDocument  # noqa: F401
from app.drivers.models import Driver, DriverContract  # noqa: F401
from app.models.user import User  # noqa: F401
from app.organisations.models import Organisation  # noqa: F401
from app.rates.models import CountryRateSnapshot  # noqa: F401
from app.services.nbp_models import NbpRate  # noqa: F401
from app.trips.models import Trip, TripSegment  # noqa: F401
from app.vehicles.models import Vehicle  # noqa: F401

# NOTE: payroll models (PayrollPeriod, PayrollLine) are intentionally NOT
# imported for now — keeping them out of the metadata parks payroll and its
# tables out of the schema/migration while the document system is built.

__all__ = [
    "User",
    "Organisation",
    "Driver",
    "DriverContract",
    "DocumentType",
    "DriverDocument",
    "Vehicle",
    "VehicleDocument",
    "Trip",
    "TripSegment",
    "CountryRateSnapshot",
    "NbpRate",
]
