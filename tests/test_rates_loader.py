"""Tests for the rates module."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.rates.loader import RateRegistry, RateRegistryError


@pytest.fixture
def rates_dir() -> Path:
    """Path to the shipped YAML files at project root."""
    return Path(__file__).resolve().parent.parent / "data" / "country_rates"


@pytest.fixture
def registry(rates_dir: Path) -> RateRegistry:
    return RateRegistry.load(rates_dir)


# ---------------------------------------------------------------------------
# Loader smoke tests
# ---------------------------------------------------------------------------


def test_loads_three_countries(registry: RateRegistry):
    assert set(registry.countries.keys()) == {"DE", "FR", "IT"}


def test_germany_has_three_periods(registry: RateRegistry):
    de = registry.countries["DE"]
    assert len(de.periods) == 3
    assert de.currency == "EUR"
    assert de.country_name_en == "Germany"


def test_periods_are_sorted_chronologically(registry: RateRegistry):
    for country in registry.iter_countries():
        dates = [p.valid_from for p in country.periods]
        assert dates == sorted(dates), f"{country.country} periods not sorted"


# ---------------------------------------------------------------------------
# Lookup tests
# ---------------------------------------------------------------------------


def test_lookup_germany_2026(registry: RateRegistry):
    rate = registry.lookup(country="DE", rate_name="statutory_minimum", on_date=date(2026, 3, 15))
    assert rate.hourly == Decimal("13.90")


def test_lookup_germany_2025(registry: RateRegistry):
    rate = registry.lookup(country="DE", rate_name="statutory_minimum", on_date=date(2025, 6, 1))
    assert rate.hourly == Decimal("12.82")


def test_lookup_france_smic_2026(registry: RateRegistry):
    rate = registry.lookup(country="FR", rate_name="statutory_minimum", on_date=date(2026, 5, 1))
    assert rate.hourly == Decimal("12.02")


def test_lookup_france_coef_150m_2026(registry: RateRegistry):
    rate = registry.lookup(country="FR", rate_name="driver_coef_150m", on_date=date(2026, 5, 1))
    assert rate.hourly == Decimal("12.43")


def test_lookup_italy_b3_2026(registry: RateRegistry):
    rate = registry.lookup(country="IT", rate_name="driver_b3", on_date=date(2026, 5, 1))
    assert rate.hourly == Decimal("11.44")
    assert rate.monthly_gross == Decimal("1922.00")


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_unknown_country_raises(registry: RateRegistry):
    with pytest.raises(RateRegistryError, match="No rates loaded"):
        registry.lookup(country="XX", rate_name="statutory_minimum", on_date=date(2026, 1, 1))


def test_unknown_rate_name_raises(registry: RateRegistry):
    with pytest.raises(RateRegistryError, match="not defined"):
        registry.lookup(country="DE", rate_name="bogus_rate", on_date=date(2026, 1, 1))


def test_date_before_any_period_raises(registry: RateRegistry):
    with pytest.raises(RateRegistryError, match="No rate period"):
        registry.lookup(country="DE", rate_name="statutory_minimum", on_date=date(2020, 1, 1))


# ---------------------------------------------------------------------------
# Schema validation — malformed YAML rejected
# ---------------------------------------------------------------------------


def test_malformed_yaml_rejected(tmp_path: Path):
    bad = tmp_path / "xx.yaml"
    bad.write_text(
        # missing required 'verified' block
        """
country: XX
country_name_en: Nowhere
currency: EUR
default_hours_per_month: 168
official_source:
  primary_url: https://example.com/
periods:
  - valid_from: 2026-01-01
    rates:
      statutory_minimum:
        hourly: 10.00
"""
    )
    with pytest.raises(RateRegistryError):
        RateRegistry.load(tmp_path)


def test_overlapping_periods_rejected(tmp_path: Path):
    bad = tmp_path / "xx.yaml"
    bad.write_text(
        """
country: XX
country_name_en: Nowhere
currency: EUR
default_hours_per_month: 168
official_source:
  primary_url: https://example.com/
periods:
  - valid_from: 2025-01-01
    valid_to: 2025-12-31
    rates:
      r:
        hourly: 10.00
    verified:
      at: 2025-01-01
      by: test
  - valid_from: 2025-06-01
    valid_to: 2026-06-01
    rates:
      r:
        hourly: 11.00
    verified:
      at: 2025-06-01
      by: test
"""
    )
    with pytest.raises(RateRegistryError, match="overlaps"):
        RateRegistry.load(bad.parent)
