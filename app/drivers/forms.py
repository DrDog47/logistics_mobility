"""WTForms for drivers blueprint."""

from datetime import date

from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    DecimalField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional, Regexp

from app.db_types import uuid_or_none
from app.drivers.models import ContractType
from app.forms_common import country_choices


class DriverForm(FlaskForm):
    first_name = StringField(_l("First name"), validators=[DataRequired(), Length(max=64)])
    last_name = StringField(_l("Last name"), validators=[DataRequired(), Length(max=64)])

    birth_date = DateField(_l("Birth date"), validators=[DataRequired()])

    nationality = SelectField(
        _l("Nationality"),
        choices=country_choices,
        validators=[DataRequired(), Length(min=3, max=3)],
        default="POL",
    )

    organisation_uuid = SelectField(
        _l("Organisation"),
        coerce=uuid_or_none,
        validators=[DataRequired()],
    )

    identification_id = StringField(
        _l("national ID number"),
        validators=[DataRequired(), Length(max=30)],
    )
    pesel = StringField(
        _l("PESEL (optional, Polish residents)"),
        validators=[Optional(), Length(min=11, max=11), Regexp(r"^\d{11}$")],
    )
    passport_number = StringField(_l("Passport number"), validators=[Optional(), Length(max=32)])
    tachograph_card_number = StringField(
        _l("Tachograph card number"),
        validators=[Optional(), Length(max=20)],
    )

    phone = StringField(_l("Phone"), validators=[Optional(), Length(max=32)])
    notes = TextAreaField(_l("Notes"), validators=[Optional(), Length(max=1000)])

    hire_date = DateField(_l("Hire date"), validators=[DataRequired()], default=date.today)

    submit = SubmitField(_l("Save"))


class DriverContractForm(FlaskForm):
    contract_type = SelectField(
        _l("Contract type"),
        choices=[(t.value, t.name.replace("_", " ").title()) for t in ContractType],
        validators=[DataRequired()],
    )
    start_date = DateField(_l("Start date"), validators=[DataRequired()])
    end_date = DateField(_l("End date (optional)"), validators=[Optional()])
    base_salary_pln = DecimalField(
        _l("Base salary (PLN, gross)"),
        validators=[DataRequired(), NumberRange(min=0)],
        places=2,
    )
    hours_norm = IntegerField(
        _l("Monthly hours norm"),
        validators=[DataRequired(), NumberRange(min=0, max=300)],
        default=168,
    )
    submit = SubmitField(_l("Save"))