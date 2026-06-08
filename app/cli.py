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

    @app.cli.command("seed-document-types")
    def seed_document_types() -> None:
        """Populate the document_type catalogue from the base constant lists."""
        from app.documents.constants import BASE_DOCUMENT_TYPES
        from app.documents.models import DocumentType

        created = 0
        for entity_type, entries in BASE_DOCUMENT_TYPES.items():
            for code, label in entries:
                exists = db.session.execute(
                    db.select(DocumentType).where(
                        DocumentType.type == code,
                        DocumentType.entity_type == entity_type,
                    )
                ).scalar_one_or_none()
                if exists:
                    continue
                db.session.add(
                    DocumentType(type=code, entity_type=entity_type, label=label)
                )
                created += 1
        db.session.commit()
        click.secho(f"Seeded {created} document type(s).", fg="green")

    @app.cli.command("seed-org")
    @click.option("--name", default="Default Sp. z o.o.", help="Company name")
    @click.option("--national-id", default="0000000000", help="NIP / national ID")
    @click.option("--country", default="POL", help="ISO 3166-1 alpha-3")
    @click.option("--city", default="Białystok", help="City")
    @click.option("--address", default="—", help="Legal address")
    def seed_org(name: str, national_id: str, country: str, city: str, address: str) -> None:
        """Create a starter organisation (drivers/vehicles require one)."""
        from app.organisations.models import Organisation

        existing = db.session.execute(
            db.select(Organisation).where(Organisation.national_id == national_id)
        ).scalar_one_or_none()
        if existing:
            click.secho(f"Organisation '{existing.name}' already exists.", fg="yellow")
            return
        org = Organisation(
            name=name, national_id=national_id, country=country, city=city, address=address
        )
        db.session.add(org)
        db.session.commit()
        click.secho(f"Organisation '{org.name}' created ({org.uuid}).", fg="green")
