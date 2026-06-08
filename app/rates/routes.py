"""Read-only browsing UI for country rates.

Editing happens through git (YAML files committed via PRs), not through this UI.
The view exists so accountants can quickly check 'what rate is in effect for DE
today' without opening the repository.
"""

from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, render_template, request
from flask_login import login_required

from app.rates.services import get_registry, stale_verifications

bp = Blueprint("rates", __name__)


@bp.route("/")
@login_required
def list_countries():
    registry = get_registry()
    today = date.today()

    rows = []
    for country in registry.iter_countries():
        period = country.period_for(today)
        rows.append(
            {
                "country": country,
                "current_period": period,
                "latest_verification": country.latest_verification(),
                "days_since_verification": (
                    (today - country.latest_verification()).days
                    if country.latest_verification()
                    else None
                ),
            }
        )

    return render_template(
        "rates/list.html",
        rows=rows,
        today=today,
        stale=stale_verifications(threshold_days=90),
    )


@bp.route("/<country_code>")
@login_required
def show_country(country_code: str):
    registry = get_registry()
    country = registry.countries.get(country_code.upper())
    if country is None:
        abort(404)

    today = date.today()
    return render_template(
        "rates/show.html",
        country=country,
        today=today,
        current_period=country.period_for(today),
    )


@bp.route("/lookup")
@login_required
def lookup():
    """Quick form-driven lookup: ?country=DE&rate=driver_default&date=2026-03-15."""
    registry = get_registry()
    country_code = request.args.get("country", "").upper()
    rate_name = request.args.get("rate", "")
    date_str = request.args.get("date", date.today().isoformat())

    result = None
    error = None
    if country_code and rate_name:
        try:
            on_date = date.fromisoformat(date_str)
            rate = registry.lookup(country=country_code, rate_name=rate_name, on_date=on_date)
            country = registry.countries[country_code]
            period = country.period_for(on_date)
            result = {"rate": rate, "country": country, "period": period}
        except (ValueError, Exception) as exc:  # noqa: BLE001
            error = str(exc)

    return render_template(
        "rates/lookup.html",
        countries=list(registry.iter_countries()),
        result=result,
        error=error,
        query={"country": country_code, "rate": rate_name, "date": date_str},
    )
