"""NBP (National Bank of Poland) exchange rate client.

The NBP API: https://api.nbp.pl/

For payroll, we need EUR/PLN at "kurs średni NBP z dnia poprzedzającego
dzień powstania przychodu" — typically the working day before the payroll
date. NBP only publishes rates on business days (no weekends, no Polish
holidays), so we walk backwards up to ~10 days if needed to find one.

Lookups cache to the `nbp_rates` table so subsequent calls (and payroll
recalculations) are fast and reproducible.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

import httpx
from flask import current_app

from app.extensions import db
from app.services.nbp_models import NbpRate

logger = logging.getLogger(__name__)

MAX_LOOKBACK_DAYS: Final[int] = 10
DEFAULT_TIMEOUT: Final[float] = 10.0


class NbpError(RuntimeError):
    """Raised when NBP API fails or no rate is available in lookback window."""


# ---------------------------------------------------------------------------
# Cache-aware lookup (the main entry point)
# ---------------------------------------------------------------------------


def get_rate(currency: str, target_date: date, *, client: httpx.Client | None = None) -> NbpRate:
    """Return the NBP rate for `currency` on the working day on/before `target_date`.

    Checks the local cache first. If miss, fetches from NBP API and persists.
    Walks backwards through holidays/weekends until a published rate is found.
    """
    currency = currency.upper()
    if currency == "PLN":
        raise NbpError("Cannot get an NBP rate for PLN against PLN")

    # 1. Cache hit on or before target_date — use most recent
    cached = db.session.execute(
        db.select(NbpRate)
        .where(NbpRate.currency == currency, NbpRate.effective_date <= target_date)
        .order_by(NbpRate.effective_date.desc())
        .limit(1)
    ).scalar_one_or_none()

    # Only use cache if the cached date is "close enough" — avoid using a
    # stale rate from 6 months ago for a current calculation
    if cached and (target_date - cached.effective_date).days <= MAX_LOOKBACK_DAYS:
        return cached

    # 2. Fetch from NBP, walking backwards
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=DEFAULT_TIMEOUT)
    try:
        for offset in range(MAX_LOOKBACK_DAYS + 1):
            attempt_date = target_date - timedelta(days=offset)
            data = _fetch_one(client, currency, attempt_date)
            if data is None:
                continue
            return _persist(currency, data)
    finally:
        if own_client:
            client.close()

    raise NbpError(
        f"No NBP rate found for {currency} within {MAX_LOOKBACK_DAYS} days "
        f"before {target_date.isoformat()}"
    )


def _fetch_one(client: httpx.Client, currency: str, when: date) -> dict | None:
    """One-shot fetch. Returns parsed JSON or None on 404 (no rate that day)."""
    base = current_app.config.get("NBP_API_BASE", "https://api.nbp.pl/api")
    url = f"{base}/exchangerates/rates/A/{currency}/{when.isoformat()}/"

    try:
        response = client.get(url, params={"format": "json"})
    except httpx.HTTPError as exc:
        raise NbpError(f"NBP API request failed: {exc}") from exc

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise NbpError(
            f"NBP API returned {response.status_code} for {currency} {when}: "
            f"{response.text[:200]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise NbpError(f"NBP API returned non-JSON: {response.text[:200]}") from exc


def _persist(currency: str, payload: dict) -> NbpRate:
    """Save the NBP response as an NbpRate row (or return existing on conflict)."""
    try:
        rate_record = payload["rates"][0]
        effective = date.fromisoformat(rate_record["effectiveDate"])
        mid = Decimal(str(rate_record["mid"]))
        table_no = rate_record.get("no")
    except (KeyError, IndexError, ValueError) as exc:
        raise NbpError(f"Unexpected NBP response shape: {payload}") from exc

    # Race-condition safe: check if it already exists
    existing = db.session.execute(
        db.select(NbpRate).where(
            NbpRate.currency == currency,
            NbpRate.effective_date == effective,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    rate = NbpRate(
        currency=currency,
        effective_date=effective,
        rate_pln=mid,
        table_no=table_no,
    )
    db.session.add(rate)
    db.session.flush()
    logger.info("NBP cached: %s %s = %s PLN (table %s)", currency, effective, mid, table_no)
    return rate


# ---------------------------------------------------------------------------
# Convenience: get rate for payroll-period purposes
# ---------------------------------------------------------------------------


def get_eur_pln_for_payroll(year: int, month: int) -> NbpRate:
    """Get the EUR/PLN rate to use when calculating a payroll for given month.

    Convention: use the rate of the last working day of the payroll month.
    Phase 2 simplification — final payroll date specifics can be refined later.
    """
    from calendar import monthrange

    _, last_day = monthrange(year, month)
    target = date(year, month, last_day)
    return get_rate("EUR", target)
