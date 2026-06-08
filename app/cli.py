"""Custom Flask CLI commands.

Usage:
    flask create-admin --login admin --email admin@example.com --name "Admin"
    flask seed-demo  # populates demo data for development
"""

from __future__ import annotations

import getpass

import click
from flask import Flask

from app.extensions import db
from app.models.user import Role, User


def register_cli(app: Flask) -> None:
    from app.rates.cli import register_rates_cli
    from app.services.nbp_cli import register_nbp_cli
    from app.tax.cli import register_tax_cli

    register_rates_cli(app)
    register_nbp_cli(app)
    register_tax_cli(app)

    @app.cli.command("create-admin")
    @click.option("--login", required=True, help="Login (username)")
    @click.option("--email", required=True, help="Email address")
    @click.option("--name", required=True, help="Full name")
    @click.option("--password", default=None, help="Password (will prompt if not provided)")
    def create_admin(login: str, email: str, name: str, password: str | None) -> None:
        """Create an admin user account."""
        existing = db.session.execute(
            db.select(User).where(db.or_(User.login == login, User.email == email))
        ).scalar_one_or_none()
        if existing:
            click.secho(f"User '{existing.login}' already exists.", fg="red", err=True)
            raise click.Abort()

        if password is None:
            password = getpass.getpass("Password: ")
            confirm = getpass.getpass("Confirm: ")
            if password != confirm:
                click.secho("Passwords do not match.", fg="red", err=True)
                raise click.Abort()

        user = User(login=login, email=email, full_name=name, role=Role.ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.secho(f"Admin '{user.login}' created.", fg="green")

    @app.cli.command("init-db")
    def init_db() -> None:
        """Create all tables (use only when not using Alembic migrations)."""
        db.create_all()
        click.secho("Database tables created.", fg="green")
