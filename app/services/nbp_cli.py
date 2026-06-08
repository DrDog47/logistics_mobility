"""`flask nbp ...` CLI commands."""

from __future__ import annotations

from datetime import date

import click
from flask import Flask

from app.extensions import db
from app.services.nbp import NbpError, get_rate
from app.services.nbp_models import NbpRate


def register_nbp_cli(app: Flask) -> None:
    @app.cli.group("nbp")
    def nbp_group() -> None:
        """Fetch and inspect NBP exchange rates."""

    @nbp_group.command("fetch")
    @click.argument("currency", default="EUR")
    @click.option(
        "--on",
        type=click.DateTime(formats=["%Y-%m-%d"]),
        default=None,
        help="Target date (default: today). Walks back to nearest working day.",
    )
    def fetch(currency: str, on: date | None) -> None:
        """Fetch and cache an NBP rate for the given currency and date."""
        target = on.date() if on else date.today()
        currency = currency.upper()
        try:
            rate = get_rate(currency, target)
        except NbpError as exc:
            click.secho(f"NBP fetch failed: {exc}", fg="red", err=True)
            raise click.Abort() from exc

        db.session.commit()
        click.secho(
            f"{currency}/PLN @ {rate.effective_date.isoformat()} = "
            f"{rate.rate_pln} (table {rate.table_no})",
            fg="green",
        )
        if rate.effective_date != target:
            click.echo(
                f"  Note: requested {target}, NBP gave nearest prior working day "
                f"{rate.effective_date}."
            )

    @nbp_group.command("list")
    @click.option(
        "--currency",
        default=None,
        help="Filter by currency (e.g. EUR).",
    )
    @click.option("--limit", type=int, default=30, show_default=True)
    def list_rates(currency: str | None, limit: int) -> None:
        """List cached NBP rates, newest first."""
        query = db.select(NbpRate).order_by(NbpRate.effective_date.desc()).limit(limit)
        if currency:
            query = query.where(NbpRate.currency == currency.upper())
        rates = db.session.execute(query).scalars().all()

        if not rates:
            click.secho("No cached rates yet.", fg="yellow")
            click.echo("Use `flask nbp fetch EUR` to populate.")
            return

        click.secho(f"Cached NBP rates (newest {len(rates)}):", bold=True)
        click.echo(f"  {'Currency':<8} {'Date':<14} {'Rate':>10} {'Table':>14}")
        click.echo("  " + "-" * 50)
        for r in rates:
            click.echo(
                f"  {r.currency:<8} {r.effective_date.isoformat():<14} "
                f"{r.rate_pln:>10} {r.table_no:>14}"
            )
