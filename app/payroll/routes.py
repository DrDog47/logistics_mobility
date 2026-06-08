"""Payroll routes: list, create+calculate, view, NBP-fetch helper."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import login_required
from sqlalchemy.exc import IntegrityError

from app.auth.routes import role_required
from app.drivers.models import Driver
from app.extensions import db
from app.models.user import Role
from app.payroll.calculator import calculate
from app.payroll.calculator.base import CalculatorError
from app.payroll.forms import PayrollPeriodForm
from app.payroll.models import PayrollPeriod, PayrollStatus
from app.rates.loader import RateRegistryError
from app.services.nbp import NbpError, get_eur_pln_for_payroll, get_rate
from app.tax.polish_params import PolishParamsError

bp = Blueprint("payroll", __name__)


@bp.route("/")
@login_required
def list_periods():
    periods = db.session.execute(
        db.select(PayrollPeriod)
        .order_by(PayrollPeriod.year.desc(), PayrollPeriod.month.desc(), PayrollPeriod.id.desc())
        .limit(200)
    ).scalars().all()
    return render_template("payroll/list.html", periods=periods)


@bp.route("/new", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.ACCOUNTANT)
def create_period():
    form = PayrollPeriodForm()
    drivers = db.session.execute(
        db.select(Driver).where(Driver.is_active.is_(True))
        .order_by(Driver.last_name, Driver.first_name)
    ).scalars().all()
    form.driver_id.choices = [(d.id, d.full_name) for d in drivers]

    if form.validate_on_submit():
        # ---- Determine EUR/PLN rate -------------------------------------
        nbp_rate = None
        rate_decimal = form.eur_pln_rate.data or Decimal("0")
        if form.auto_fetch_nbp.data or rate_decimal == 0:
            try:
                nbp_rate = get_eur_pln_for_payroll(form.year.data, form.month.data)
                rate_decimal = nbp_rate.rate_pln
                db.session.commit()  # persist the NBP cache row
            except NbpError as exc:
                flash(
                    _("NBP fetch failed: %(err)s. Enter rate manually.", err=str(exc)),
                    "error",
                )
                return render_template("payroll/new.html", form=form)
        if rate_decimal <= 0:
            flash(_("EUR/PLN rate must be > 0"), "error")
            return render_template("payroll/new.html", form=form)

        # ---- Create period ----------------------------------------------
        period = PayrollPeriod(
            driver_id=form.driver_id.data,
            year=form.year.data,
            month=form.month.data,
            eur_pln_rate=rate_decimal,
            nbp_rate_id=nbp_rate.id if nbp_rate else None,
            days_abroad_override=form.days_abroad_override.data,
            status=PayrollStatus.DRAFT,
        )
        db.session.add(period)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            flash(_("Period already exists for this driver and month"), "error")
            return render_template("payroll/new.html", form=form)

        # ---- Calculate --------------------------------------------------
        try:
            calculate(period)
            db.session.commit()
        except (CalculatorError, RateRegistryError, PolishParamsError) as exc:
            db.session.rollback()
            flash(_("Calculation failed: %(err)s", err=str(exc)), "error")
            return render_template("payroll/new.html", form=form)

        flash(_("Payroll calculated"), "success")
        return redirect(url_for("payroll.show_period", period_id=period.id))

    return render_template("payroll/new.html", form=form)


@bp.route("/<int:period_id>")
@login_required
def show_period(period_id: int):
    period = db.session.get(PayrollPeriod, period_id) or abort(404)
    return render_template("payroll/show.html", period=period)


@bp.route("/<int:period_id>/recalculate", methods=["POST"])
@role_required(Role.ADMIN, Role.ACCOUNTANT)
def recalculate(period_id: int):
    period = db.session.get(PayrollPeriod, period_id) or abort(404)
    if period.status == PayrollStatus.APPROVED:
        flash(_("Cannot recalculate an approved period"), "error")
        return redirect(url_for("payroll.show_period", period_id=period.id))

    try:
        calculate(period)
        db.session.commit()
        flash(_("Recalculated"), "success")
    except (CalculatorError, RateRegistryError, PolishParamsError) as exc:
        db.session.rollback()
        flash(_("Recalculation failed: %(err)s", err=str(exc)), "error")
    return redirect(url_for("payroll.show_period", period_id=period.id))


@bp.route("/<int:period_id>/approve", methods=["POST"])
@role_required(Role.ADMIN, Role.ACCOUNTANT)
def approve(period_id: int):
    period = db.session.get(PayrollPeriod, period_id) or abort(404)
    if period.status != PayrollStatus.CALCULATED:
        flash(_("Only calculated periods can be approved"), "error")
        return redirect(url_for("payroll.show_period", period_id=period.id))
    period.status = PayrollStatus.APPROVED
    db.session.commit()
    flash(_("Period approved and frozen"), "success")
    return redirect(url_for("payroll.show_period", period_id=period.id))


@bp.route("/nbp/eur-pln", methods=["GET"])
@login_required
def nbp_eur_pln_lookup():
    """JSON endpoint used by HTMX in the new-period form for live preview."""
    date_str = request.args.get("date", date.today().isoformat())
    try:
        when = date.fromisoformat(date_str)
        rate = get_rate("EUR", when)
        db.session.commit()
        return jsonify(
            currency="EUR",
            effective_date=rate.effective_date.isoformat(),
            rate_pln=str(rate.rate_pln),
            table_no=rate.table_no,
        )
    except (ValueError, NbpError) as exc:
        return jsonify(error=str(exc)), 400
