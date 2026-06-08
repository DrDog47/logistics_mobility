"""YAML rate file loader.

Loads all `*.yaml` files from `data/country_rates/`, validates against
the Marshmallow schemas, and exposes a lookup API:

    rates = RateRegistry.load(directory)
    rate = rates.lookup(country="DE", rate_name="driver_default", on_date=date(2026, 3, 15))

The registry is built once at app startup and cached. If any YAML is malformed,
the application refuses to start — this is intentional.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterator

import yaml
from marshmallow import ValidationError

from app.rates.schemas import CountryRatesSchema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers (immutable, hashable for caching)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateValue:
    """A single named rate at a moment in time."""

    name: str
    hourly: Decimal
    monthly_gross: Decimal | None
    description_en: str | None
    description_pl: str | None
    description_ru: str | None


@dataclass(frozen=True, slots=True)
class RatePeriod:
    """A validity window holding one or more named rates."""

    valid_from: date
    valid_to: date | None
    rates: dict[str, RateValue]
    verified_at: date
    verified_by: str
    source_urls: tuple[str, ...]

    def contains(self, target: date) -> bool:
        if target < self.valid_from:
            return False
        if self.valid_to is not None and target > self.valid_to:
            return False
        return True


@dataclass(frozen=True, slots=True)
class CountryRates:
    """All known periods and metadata for one country."""

    country: str
    country_name_en: str
    country_name_pl: str | None
    country_name_ru: str | None
    currency: str
    default_hours_per_month: Decimal
    official_source_url: str
    posting_portal_url: str | None
    periods: tuple[RatePeriod, ...]

    def period_for(self, target: date) -> RatePeriod | None:
        for period in self.periods:
            if period.contains(target):
                return period
        return None

    def latest_verification(self) -> date | None:
        if not self.periods:
            return None
        return max(p.verified_at for p in self.periods)


# ---------------------------------------------------------------------------
# Registry — entry point used by services
# ---------------------------------------------------------------------------


class RateRegistryError(RuntimeError):
    """Raised when YAML loading or validation fails."""


@dataclass(frozen=True, slots=True)
class RateRegistry:
    """In-memory registry of all country rates."""

    countries: dict[str, CountryRates] = field(default_factory=dict)

    # ---- Loading ----------------------------------------------------------

    @classmethod
    def load(cls, directory: str | Path) -> RateRegistry:
        directory = Path(directory)
        if not directory.is_dir():
            raise RateRegistryError(f"Rates directory does not exist: {directory}")

        countries: dict[str, CountryRates] = {}
        schema = CountryRatesSchema()
        errors: list[str] = []

        for yaml_path in sorted(directory.glob("*.yaml")):
            try:
                country = cls._load_one(yaml_path, schema)
            except RateRegistryError as exc:
                errors.append(f"{yaml_path.name}: {exc}")
                continue

            if country.country in countries:
                errors.append(
                    f"{yaml_path.name}: duplicate country code "
                    f"'{country.country}' (already loaded)"
                )
                continue

            countries[country.country] = country
            logger.info("Loaded rates for %s (%d periods)", country.country, len(country.periods))

        if errors:
            raise RateRegistryError(
                "Country rate YAML validation failed:\n  - " + "\n  - ".join(errors)
            )

        if not countries:
            raise RateRegistryError(f"No country rate YAML files found in {directory}")

        return cls(countries=countries)

    @staticmethod
    def _load_one(path: Path, schema: CountryRatesSchema) -> CountryRates:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RateRegistryError(f"YAML parse error: {exc}") from exc

        if not isinstance(raw, dict):
            raise RateRegistryError("Root must be a mapping")

        try:
            data = schema.load(raw)
        except ValidationError as exc:
            raise RateRegistryError(f"Schema validation failed: {exc.messages}") from exc

        # Materialize into frozen dataclasses
        periods = tuple(
            RatePeriod(
                valid_from=p["valid_from"],
                valid_to=p.get("valid_to"),
                rates={
                    name: RateValue(
                        name=name,
                        hourly=rv["hourly"],
                        monthly_gross=rv.get("monthly_gross"),
                        description_en=rv.get("description_en"),
                        description_pl=rv.get("description_pl"),
                        description_ru=rv.get("description_ru"),
                    )
                    for name, rv in p["rates"].items()
                },
                verified_at=p["verified"]["at"],
                verified_by=p["verified"]["by"],
                source_urls=tuple(d["url"] for d in p.get("source_documents", [])),
            )
            for p in data["periods"]
        )

        official = data["official_source"]
        return CountryRates(
            country=data["country"],
            country_name_en=data["country_name_en"],
            country_name_pl=data.get("country_name_pl"),
            country_name_ru=data.get("country_name_ru"),
            currency=data["currency"],
            default_hours_per_month=data["default_hours_per_month"],
            official_source_url=official["primary_url"],
            posting_portal_url=official.get("posting_portal"),
            periods=periods,
        )

    # ---- Lookup API -------------------------------------------------------

    def lookup(self, *, country: str, rate_name: str, on_date: date) -> RateValue:
        """Return the rate value for a country + named rate + date.

        Raises:
            RateRegistryError: if the country, period, or named rate is missing.
        """
        country_rates = self.countries.get(country.upper())
        if country_rates is None:
            raise RateRegistryError(f"No rates loaded for country '{country}'")

        period = country_rates.period_for(on_date)
        if period is None:
            raise RateRegistryError(
                f"No rate period covers date {on_date.isoformat()} for {country}"
            )

        rate = period.rates.get(rate_name)
        if rate is None:
            available = ", ".join(sorted(period.rates.keys()))
            raise RateRegistryError(
                f"Rate '{rate_name}' not defined for {country} on "
                f"{on_date.isoformat()}. Available: {available}"
            )

        return rate

    def iter_countries(self) -> Iterator[CountryRates]:
        yield from sorted(self.countries.values(), key=lambda c: c.country)
