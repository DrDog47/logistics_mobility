"""`flask rates ...` CLI commands."""

from __future__ import annotations

from datetime import date

import click
from flask import Flask

from app.rates.services import get_registry, stale_verifications


def register_rates_cli(app: Flask) -> None:
    @app.cli.group("rates")
    def rates_group() -> None:
        """Inspect country rate data loaded from YAML."""

    @rates_group.command("verify")
    @click.option(
        "--threshold-days",
        type=int,
        default=90,
        show_default=True,
        help="Highlight verifications older than this many days.",
    )
    def verify(threshold_days: int) -> None:
        """List countries needing a fresh check against the official source."""
        rows = stale_verifications(threshold_days=threshold_days)
        if not rows:
            click.secho(
                f"All verifications fresher than {threshold_days} days. ✓",
                fg="green",
            )
            return

        click.secho(
            f"Countries with verifications older than {threshold_days} days:",
            fg="yellow",
            bold=True,
        )
        click.echo(f"  {'CC':<4} {'Last verified':<14} {'Days ago':<10}")
        click.echo(f"  {'-' * 4:<4} {'-' * 14:<14} {'-' * 10:<10}")
        for country_code, latest, days_since in rows:
            verified = latest.isoformat() if latest else "(never)"
            colour = "red" if days_since > 180 else "yellow"
            click.echo(
                f"  {country_code:<4} {verified:<14} "
                + click.style(f"{days_since:<10}", fg=colour)
            )
        click.echo()
        click.secho(
            "Update YAML files in data/country_rates/ and bump the 'verified' "
            "block. Commit via git PR with the source URL in the message.",
            dim=True,
        )

    @rates_group.command("show")
    @click.argument("country")
    @click.option(
        "--on",
        type=click.DateTime(formats=["%Y-%m-%d"]),
        default=None,
        help="Date to query (default: today).",
    )
    def show(country: str, on: date | None) -> None:
        """Show all rates for a country, optionally at a specific date."""
        registry = get_registry()
        country_rates = registry.countries.get(country.upper())
        if country_rates is None:
            available = ", ".join(sorted(registry.countries.keys()))
            click.secho(
                f"Country '{country}' not loaded. Available: {available}",
                fg="red",
                err=True,
            )
            raise click.Abort()

        target = on.date() if on else date.today()
        period = country_rates.period_for(target)

        click.secho(
            f"{country_rates.country_name_en} ({country_rates.country}) — "
            f"rates on {target.isoformat()}",
            bold=True,
        )
        click.echo(f"  Currency: {country_rates.currency}")
        click.echo(f"  Default hours/month: {country_rates.default_hours_per_month}")
        click.echo(f"  Source: {country_rates.official_source_url}")
        click.echo()

        if period is None:
            click.secho(
                f"  No rate period covers {target.isoformat()}.",
                fg="red",
            )
            click.echo("  Available periods:")
            for p in country_rates.periods:
                click.echo(f"    {p.valid_from} → {p.valid_to or 'open'}")
            return

        click.echo(
            f"  Active period: {period.valid_from} → "
            f"{period.valid_to or 'open'} "
            f"(verified {period.verified_at} by {period.verified_by})"
        )
        click.echo()
        click.echo(f"  {'Rate name':<35} {'Hourly':>10} {'Monthly':>10}")
        click.echo(f"  {'-' * 35} {'-' * 10} {'-' * 10}")
        for name, rate in sorted(period.rates.items()):
            monthly = f"{rate.monthly_gross}" if rate.monthly_gross else "—"
            click.echo(f"  {name:<35} {rate.hourly:>10} {monthly:>10}")

    @rates_group.command("list")
    def list_countries() -> None:
        """List all loaded countries with their current rate count."""
        registry = get_registry()
        today = date.today()
        click.secho(f"Loaded countries (current rates as of {today.isoformat()}):", bold=True)
        click.echo()
        click.echo(
            f"  {'CC':<4} {'Country':<22} {'Currency':<10} "
            f"{'Periods':>8} {'Current rates':>14} {'Verified':>14}"
        )
        click.echo("  " + "-" * 76)
        for country in registry.iter_countries():
            period = country.period_for(today)
            current_rates = len(period.rates) if period else 0
            latest = country.latest_verification()
            verified = latest.isoformat() if latest else "—"
            click.echo(
                f"  {country.country:<4} {country.country_name_en:<22} "
                f"{country.currency:<10} {len(country.periods):>8} "
                f"{current_rates:>14} {verified:>14}"
            )
