"""Drivers CRUD routes. Demonstrates HTMX usage pattern.

Pattern for HTMX:
- Full page renders return the full template (extends base.html)
- HTMX fragment renders detect HX-Request header and return only the fragment
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import login_required

from app.auth.routes import role_required
from app.drivers.forms import DriverContractForm, DriverForm
from app.drivers.models import Driver, DriverContract
from app.extensions import db
from app.models.user import Role

bp = Blueprint("drivers", __name__)


@bp.route("/")
@login_required
def list_drivers():
    """List all drivers. Anyone authenticated can view."""
    search = request.args.get("q", "").strip()

    query = db.select(Driver).order_by(Driver.last_name, Driver.first_name)
    if search:
        like = f"%{search}%"
        query = query.where(
            db.or_(
                Driver.last_name.ilike(like),
                Driver.first_name.ilike(like),
                Driver.pesel.ilike(like),
                Driver.passport_number.ilike(like),
            )
        )

    drivers = db.session.execute(query).scalars().all()

    # HTMX request → return just the table fragment for live search
    if request.headers.get("HX-Request"):
        return render_template("drivers/_table.html", drivers=drivers)
    return render_template("drivers/list.html", drivers=drivers, search=search)


@bp.route("/new", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def create_driver():
    form = DriverForm()
    if form.validate_on_submit():
        driver = Driver(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            nationality=form.nationality.data,
            pesel=form.pesel.data or None,
            passport_number=form.passport_number.data or None,
            tachograph_card_number=form.tachograph_card_number.data or None,
            hire_date=form.hire_date.data,
        )
        db.session.add(driver)
        db.session.commit()
        flash(_("Driver %(name)s added", name=driver.full_name), "success")
        return redirect(url_for("drivers.show_driver", driver_id=driver.id))

    return render_template("drivers/form.html", form=form, driver=None)


@bp.route("/<int:driver_id>")
@login_required
def show_driver(driver_id: int):
    driver = db.session.get(Driver, driver_id) or abort(404)
    return render_template("drivers/show.html", driver=driver)


@bp.route("/<int:driver_id>/edit", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def edit_driver(driver_id: int):
    driver = db.session.get(Driver, driver_id) or abort(404)
    form = DriverForm(obj=driver)
    if form.validate_on_submit():
        form.populate_obj(driver)
        db.session.commit()
        flash(_("Driver updated"), "success")
        return redirect(url_for("drivers.show_driver", driver_id=driver.id))
    return render_template("drivers/form.html", form=form, driver=driver)


@bp.route("/<int:driver_id>/contracts/new", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.ACCOUNTANT)
def add_contract(driver_id: int):
    driver = db.session.get(Driver, driver_id) or abort(404)
    form = DriverContractForm()
    if form.validate_on_submit():
        contract = DriverContract(
            driver_id=driver.id,
            contract_type=form.contract_type.data,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            base_salary_pln=form.base_salary_pln.data,
            hours_norm=form.hours_norm.data,
        )
        db.session.add(contract)
        db.session.commit()
        flash(_("Contract added"), "success")
        return redirect(url_for("drivers.show_driver", driver_id=driver.id))
    return render_template("drivers/contract_form.html", form=form, driver=driver)
