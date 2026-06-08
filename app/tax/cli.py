"""`flask tax ...` CLI commands."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import click
import yaml
from flask import Flask, current_app


def register_tax_cli(app: Flask) -> None:
    @app.cli.group("tax")
    def tax_group() -> None:
        """Inspect Polish tax/contribution parameters."""

    @tax_group.command("verify")
    @click.option(
        "--threshold-days",
        type=int,
        default=120,
        show_default=True,
        help="Highlight verifications older than this many days.",
    )
    def verify(threshold_days: int) -> None:
        """List PL tax YAML files whose verified.at is too old.

        Polish parameters change at most yearly (avg wage in November, min wage
        in September). Default threshold is 120 days, which catches files that
        weren't refreshed during the autumn announcement cycle.
        """
        tax_dir: Path = current_app.config["TAX_RULES_DIR"]
        today = date.today()
        rows: list[tuple[str, date | None, int]] = []

        for path in sorted(tax_dir.glob("pl_*.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                verified_at = raw.get("verified", {}).get("at")
                if isinstance(verified_at, str):
                    verified_at = date.fromisoformat(verified_at)
                if verified_at is None:
                    rows.append((path.name, None, 10_000))
                    continue
                days_since = (today - verified_at).days
                if days_since >= threshold_days:
                    rows.append((path.name, verified_at, days_since))
            except Exception as exc:
                click.secho(f"{path.name}: parse error — {exc}", fg="red", err=True)

        if not rows:
            click.secho(
                f"All Polish tax YAMLs verified within {threshold_days} days. ✓",
                fg="green",
            )
            return

        click.secho(
            f"Polish tax YAMLs with verified.at older than {threshold_days} days:",
            fg="yellow",
            bold=True,
        )
        click.echo(f"  {'File':<20} {'Last verified':<14} {'Days ago':<10}")
        click.echo("  " + "-" * 44)
        for filename, verified_at, days_since in sorted(rows, key=lambda r: r[2], reverse=True):
            label = verified_at.isoformat() if verified_at else "(never)"
            colour = "red" if days_since > 365 else "yellow"
            click.echo(
                f"  {filename:<20} {label:<14} "
                + click.style(f"{days_since:<10}", fg=colour)
            )

        click.echo()
        click.secho(
            "See DATA_FRESHNESS.md for which fields to check and where to "
            "find current values. Quick reminder of autumn cycle:\n"
            "  - September: minimum wage (Dz.U.)\n"
            "  - November: average wage (M.P.)\n"
            "  - December: finalize next year's YAML",
            dim=True,
        )

    @tax_group.command("show")
    @click.argument("year", type=int)
    def show(year: int) -> None:
        """Show all Polish parameters for a given year."""
        from app.tax.polish_params import PolishParamsError, get_polish_params

        try:
            p = get_polish_params(year)
        except PolishParamsError as exc:
            click.secho(str(exc), fg="red", err=True)
            raise click.Abort()

        click.secho(f"Polish payroll parameters — {p.year}", bold=True)
        click.echo()
        click.echo(f"  Average monthly wage     {p.average_wage_monthly:>10} PLN  (przeciętne wynagrodzenie)")
        click.echo(f"  Minimum monthly wage     {p.minimum_wage_monthly:>10} PLN  (minimalne wynagrodzenie)")
        click.echo()
        click.secho("  Virtual diet", bold=True)
        click.echo(f"    ZUS rate              {p.zus_diet_eur_per_day:>10} EUR/day")
        click.echo(f"    ZUS threshold         {p.zus_diet_threshold_monthly:>10} PLN  (applies if gross > this)")
        click.echo(f"    ZUS floor             {p.zus_diet_floor_monthly:>10} PLN  (ZUS base capped at this)")
        click.echo(f"    PIT rate              {p.pit_diet_eur_per_day:>10} EUR/day  (no threshold)")
        click.echo()
        click.secho("  Sanitariaty", bold=True)
        click.echo(f"    Per day               {p.sanitariaty_pln_per_day:>10} PLN  (ZUS+PIT exempt)")
        click.echo()
        click.secho("  Employee ZUS rates", bold=True)
        click.echo(f"    Emerytalne                  {p.zus_emerytalne_pct:>5}%")
        click.echo(f"    Rentowe                     {p.zus_rentowe_pct:>5}%")
        click.echo(f"    Chorobowe                   {p.zus_chorobowe_pct:>5}%")
        click.echo(f"    Total                       {p.zus_employee_total_pct:>5}%")
        click.echo()
        click.secho("  Other deductions", bold=True)
        click.echo(f"    Zdrowotne                   {p.zdrowotne_pct:>5}%  (base = gross − ZUS social)")
        click.echo(f"    PIT bracket 1               {p.pit_bracket_1_rate_pct:>5}%  (up to {p.pit_bracket_1_threshold} PLN annual)")
        click.echo(f"    PIT bracket 2               {p.pit_bracket_2_rate_pct:>5}%  (above)")
        click.echo(f"    Monthly tax reduction       {p.pit_monthly_tax_reduction} PLN")
        click.echo(f"    Monthly employee costs      {p.pit_monthly_employee_costs} PLN")
