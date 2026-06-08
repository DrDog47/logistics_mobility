"""Service-layer helpers for the rates module."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from flask import Flask, current_app

from app.extensions import db
from app.rates.loader import RateRegistry
from app.rates.models import CountryRateSnapshot

if TYPE_CHECKING:
    from app.rates.loader import CountryRates, RateValue


# ---------------------------------------------------------------------------
# App startup wiring
# ---------------------------------------------------------------------------


def init_registry(app: Flask) -> None:
    """Load YAML rates once at startup and attach to the Flask app.

    Subsequent code reaches the registry via `get_registry()`. If YAML is
    malformed, this raises and prevents the app from starting.
    """
    rates_dir = app.config["COUNTRY_RATES_DIR"]
    registry = RateRegistry.load(rates_dir)
    app.extensions["rate_registry"] = registry
    app.logger.info(
        "Rate registry loaded: %d countries from %s",
        len(registry.countries),
        rates_dir,
    )


def get_registry() -> RateRegistry:
    """Access the registry attached to the current app."""
    registry: RateRegistry | None = current_app.extensions.get("rate_registry")
    if registry is None:
        raise RuntimeError(
            "Rate registry not initialized. Did you call init_registry() in the app factory?"
        )
    return registry


# ---------------------------------------------------------------------------
# The main consumer: look up + persist snapshot
# ---------------------------------------------------------------------------


def resolve_rate_with_snapshot(
    *,
    country: str,
    rate_name: str,
    on_date: date,
    persist: bool = True,
) -> tuple[RateValue, CountryRateSnapshot | None]:
    """Look up a rate AND create an audit snapshot of what was used.

    This is the function payroll calculation code should call — never
    bypass it, otherwise we lose reproducibility.

    Args:
        country: ISO 3166-1 alpha-2 code (e.g. "DE", "FR").
        rate_name: Named rate key from the YAML (e.g. "driver_default").
        on_date: Date at which the rate applies (typically the work date).
        persist: If False, returns the rate but doesn't write the snapshot.
                 Used in dry-runs / previews.

    Returns:
        Tuple of (RateValue, snapshot). Snapshot is None if persist=False.
    """
    registry = get_registry()
    country_rates: CountryRates = registry.countries[country.upper()]
    rate = registry.lookup(country=country, rate_name=rate_name, on_date=on_date)
    period = country_rates.period_for(on_date)
    assert period is not None  # lookup() would have raised otherwise

    if not persist:
        return rate, None

    snapshot = CountryRateSnapshot(
        country=country.upper(),
        rate_name=rate_name,
        queried_for_date=on_date,
        hourly=rate.hourly,
        monthly_gross=rate.monthly_gross,
        currency=country_rates.currency,
        period_valid_from=period.valid_from,
        period_valid_to=period.valid_to,
        period_verified_at=period.verified_at,
        period_verified_by=period.verified_by,
    )
    db.session.add(snapshot)
    # Caller commits — we don't want to flush mid-transaction in a payroll run

    return rate, snapshot


# ---------------------------------------------------------------------------
# Verification freshness reporting
# ---------------------------------------------------------------------------


def stale_verifications(threshold_days: int = 90) -> list[tuple[str, date | None, int]]:
    """List countries whose latest verification is older than `threshold_days`.

    Returns:
        List of (country_code, latest_verification_date, days_since).
        Sorted by staleness (oldest first).
    """
    today = date.today()
    rows: list[tuple[str, date | None, int]] = []
    for country in get_registry().iter_countries():
        latest = country.latest_verification()
        if latest is None:
            rows.append((country.country, None, 10_000))
            continue
        days_since = (today - latest).days
        if days_since >= threshold_days:
            rows.append((country.country, latest, days_since))

    rows.sort(key=lambda r: r[2], reverse=True)
    return rows
