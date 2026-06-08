"""Tests for NBP client.

Uses httpx.MockTransport so no real network calls — runs offline.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.extensions import db
from app.services.nbp import NbpError, NbpRate, NbpRateNotFound, get_rate


def _client_returning(handler) -> httpx.Client:
    """Build an httpx.Client backed by a MockTransport using the given handler."""
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, base_url="http://test")


def _json_rate(currency: str, effective_date: str, mid: float, no: str = "001/A/NBP/2026"):
    return {
        "table": "A",
        "currency": currency,
        "code": currency.upper(),
        "rates": [{"no": no, "effectiveDate": effective_date, "mid": mid}],
    }


# ---------------------------------------------------------------------------
# Happy path: rate exists for requested date
# ---------------------------------------------------------------------------


def test_fetches_and_caches_rate(app):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/exchangerates/rates/A/EUR/2026-03-30" in str(request.url)
        return httpx.Response(200, json=_json_rate("euro", "2026-03-30", 4.3052))

    with app.app_context():
        client = _client_returning(handler)
        rate = get_rate(currency="EUR", on_or_before=date(2026, 3, 30), client=client)

        assert rate.currency == "EUR"
        assert rate.effective_date == date(2026, 3, 30)
        assert rate.rate == Decimal("4.3052")

        # Second call should hit the cache, not the API
        # If it hits the API again, the handler asserts the URL — we'd see it
        cached = get_rate(currency="EUR", on_or_before=date(2026, 3, 30), client=client)
        assert cached.id == rate.id


# ---------------------------------------------------------------------------
# Fallback on 404: walk back to previous working day
# ---------------------------------------------------------------------------


def test_walks_back_on_404_until_finds_rate(app):
    """Saturday 2026-03-28 → 404, Sunday 2026-03-29 → 404, Monday 2026-03-30 → ok.
    But we asked for 2026-03-29 (Sunday), should walk back to Friday 2026-03-27.
    """
    requested_dates: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        # Extract date from path like /api/exchangerates/rates/A/EUR/2026-03-29/
        date_str = path.rstrip("/").split("/")[-1]
        requested_dates.append(date_str)

        # Return 404 for Saturday and Sunday, 200 for Friday
        if date_str in ("2026-03-29", "2026-03-28"):
            return httpx.Response(404, text="Brak danych")
        if date_str == "2026-03-27":
            return httpx.Response(200, json=_json_rate("euro", "2026-03-27", 4.2900))
        return httpx.Response(404)

    with app.app_context():
        client = _client_returning(handler)
        rate = get_rate(currency="EUR", on_or_before=date(2026, 3, 29), client=client)

        assert rate.effective_date == date(2026, 3, 27)
        assert rate.rate == Decimal("4.2900")
        # We walked back over Sun → Sat → Fri (3 calls)
        assert requested_dates == ["2026-03-29", "2026-03-28", "2026-03-27"]


# ---------------------------------------------------------------------------
# Cache hit within window: no API call
# ---------------------------------------------------------------------------


def test_cache_within_window_is_used_without_api_call(app):
    api_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        api_calls.append(str(request.url))
        return httpx.Response(500, text="Should not be called")

    with app.app_context():
        # Pre-seed cache: a rate for 2026-03-27
        cached = NbpRate(
            currency="EUR",
            effective_date=date(2026, 3, 27),
            rate=Decimal("4.2900"),
            nbp_number="seed",
        )
        db.session.add(cached)
        db.session.commit()

        client = _client_returning(handler)
        # Ask for Sunday 2026-03-29 — cache contains Friday 2026-03-27 within window
        rate = get_rate(currency="EUR", on_or_before=date(2026, 3, 29), client=client)

        assert rate.effective_date == date(2026, 3, 27)
        assert rate.rate == Decimal("4.2900")
        assert api_calls == []  # No network calls!


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_all_404_within_window_raises_not_found(app):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with app.app_context():
        client = _client_returning(handler)
        with pytest.raises(NbpRateNotFound):
            get_rate(currency="EUR", on_or_before=date(2026, 3, 29), client=client)


def test_500_raises_nbp_error(app):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal error")

    with app.app_context():
        client = _client_returning(handler)
        with pytest.raises(NbpError, match="500"):
            get_rate(currency="EUR", on_or_before=date(2026, 3, 29), client=client)


def test_network_error_raises_nbp_error(app):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with app.app_context():
        client = _client_returning(handler)
        with pytest.raises(NbpError, match="request failed"):
            get_rate(currency="EUR", on_or_before=date(2026, 3, 29), client=client)
