"""Marshmallow schemas for validating country rate YAML files.

A YAML file is invalid if it doesn't match these schemas. The loader will
refuse to start the app rather than silently use bad data — better to fail
loud at startup than miscalculate someone's salary for 6 months.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from marshmallow import Schema, ValidationError, fields, validate, validates_schema


class SourceDocumentSchema(Schema):
    """A pointer to where a rate value came from."""

    url = fields.Url(required=True)
    type = fields.String(
        required=True,
        validate=validate.OneOf(["legislation", "official", "industry", "court_ruling"]),
    )
    title = fields.String(required=True, validate=validate.Length(min=3, max=300))


class RateValueSchema(Schema):
    """A single named rate (e.g. 'statutory_minimum', 'driver_coef_150m')."""

    hourly = fields.Decimal(required=True, places=4, as_string=False)
    monthly_gross = fields.Decimal(required=False, places=2, as_string=False)
    description_en = fields.String(required=False, validate=validate.Length(max=200))
    description_pl = fields.String(required=False, validate=validate.Length(max=200))
    description_ru = fields.String(required=False, validate=validate.Length(max=200))


class VerificationSchema(Schema):
    """Who checked this period and when."""

    at = fields.Date(required=True)
    by = fields.String(required=True, validate=validate.Length(min=1, max=64))
    notes = fields.String(required=False, validate=validate.Length(max=2000))


class RatePeriodSchema(Schema):
    """A single validity window with its rate set."""

    valid_from = fields.Date(required=True)
    valid_to = fields.Date(required=False, allow_none=True)
    rates = fields.Dict(
        keys=fields.String(validate=validate.Regexp(r"^[a-z][a-z0-9_]*$")),
        values=fields.Nested(RateValueSchema),
        required=True,
    )
    source_documents = fields.List(
        fields.Nested(SourceDocumentSchema),
        required=False,
        load_default=list,
    )
    verified = fields.Nested(VerificationSchema, required=True)

    @validates_schema
    def _check_date_order(self, data: dict, **kwargs: object) -> None:
        valid_from: date = data["valid_from"]
        valid_to: date | None = data.get("valid_to")
        if valid_to is not None and valid_to < valid_from:
            raise ValidationError("valid_to must be on or after valid_from", "valid_to")

    @validates_schema
    def _check_at_least_one_rate(self, data: dict, **kwargs: object) -> None:
        if not data.get("rates"):
            raise ValidationError("period must define at least one rate", "rates")


class OfficialSourceSchema(Schema):
    """The country's authoritative source links."""

    primary_url = fields.Url(required=True)
    posting_portal = fields.Url(required=False)
    ccn_text = fields.Url(required=False)
    cnel_archive = fields.Url(required=False)
    notes = fields.String(required=False, validate=validate.Length(max=4000))


class CountryRatesSchema(Schema):
    """Root schema for a single country YAML file."""

    country = fields.String(
        required=True,
        validate=validate.Regexp(r"^[A-Z]{2}$", error="Must be ISO 3166-1 alpha-2"),
    )
    country_name_en = fields.String(required=True)
    country_name_pl = fields.String(required=False)
    country_name_ru = fields.String(required=False)
    currency = fields.String(
        required=True,
        validate=validate.Regexp(r"^[A-Z]{3}$", error="Must be ISO 4217 code"),
    )
    default_hours_per_month = fields.Decimal(required=True, places=2, as_string=False)
    official_source = fields.Nested(OfficialSourceSchema, required=True)
    periods = fields.List(
        fields.Nested(RatePeriodSchema),
        required=True,
        validate=validate.Length(min=1, error="At least one period required"),
    )

    @validates_schema
    def _check_periods_ordered_and_non_overlapping(
        self, data: dict, **kwargs: object
    ) -> None:
        periods: list[dict] = data.get("periods", [])
        if len(periods) < 2:
            return

        sorted_periods = sorted(periods, key=lambda p: p["valid_from"])
        for i, period in enumerate(sorted_periods):
            if period != periods[i]:
                raise ValidationError(
                    "periods must be sorted by valid_from ascending in the YAML",
                    "periods",
                )

        for prev, curr in zip(sorted_periods, sorted_periods[1:], strict=False):
            prev_to = prev.get("valid_to")
            if prev_to is None:
                raise ValidationError(
                    f"Period starting {prev['valid_from']} has open valid_to "
                    f"but a later period starts on {curr['valid_from']}",
                    "periods",
                )
            if prev_to >= curr["valid_from"]:
                raise ValidationError(
                    f"Period {prev['valid_from']}..{prev_to} overlaps with "
                    f"next period starting {curr['valid_from']}",
                    "periods",
                )
