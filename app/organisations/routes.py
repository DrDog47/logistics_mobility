"""Organisation CRUD routes."""

from __future__ import annotations

import uuid

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_babel import gettext as _
from flask_login import login_required

from app.auth.routes import role_required
from app.extensions import db
from app.models.user import Role
from app.organisations.forms import OrganisationForm
from app.organisations.models import Organisation

bp = Blueprint("organisations", __name__)


def _get_active_or_404(org_id: uuid.UUID) -> Organisation:
    org = db.session.get(Organisation, org_id)
    if org is None or org.is_deleted:
        abort(404)
    return org


@bp.route("/")
@login_required
def list_organisations():
    orgs = db.session.execute(
        db.select(Organisation)
        .where(Organisation.is_deleted.is_(False))
        .order_by(Organisation.name)
    ).scalars().all()
    return render_template("organisations/list.html", organisations=orgs)


@bp.route("/new", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def create_organisation():
    form = OrganisationForm()
    if form.validate_on_submit():
        org = Organisation(
            name=form.name.data,
            national_id=form.national_id.data,
            country=form.country.data,
            city=form.city.data,
            address=form.address.data,
        )
        db.session.add(org)
        db.session.commit()
        flash(_("Organisation %(name)s added", name=org.name), "success")
        return redirect(url_for("organisations.show_organisation", org_id=org.uuid))
    return render_template("organisations/form.html", form=form, organisation=None)


@bp.route("/<uuid:org_id>")
@login_required
def show_organisation(org_id: uuid.UUID):
    org = _get_active_or_404(org_id)
    return render_template("organisations/show.html", organisation=org)


@bp.route("/<uuid:org_id>/edit", methods=["GET", "POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def edit_organisation(org_id: uuid.UUID):
    org = _get_active_or_404(org_id)
    form = OrganisationForm(obj=org)
    if form.validate_on_submit():
        form.populate_obj(org)
        db.session.commit()
        flash(_("Organisation updated"), "success")
        return redirect(url_for("organisations.show_organisation", org_id=org.uuid))
    return render_template("organisations/form.html", form=form, organisation=org)


@bp.route("/<uuid:org_id>/delete", methods=["POST"])
@role_required(Role.ADMIN, Role.FLEET_MANAGER)
def delete_organisation(org_id: uuid.UUID):
    org = _get_active_or_404(org_id)
    org.soft_delete()
    db.session.commit()
    flash(_("Organisation %(name)s deleted", name=org.name), "success")
    return redirect(url_for("organisations.list_organisations"))