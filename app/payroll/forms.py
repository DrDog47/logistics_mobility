"""Payroll forms."""

from datetime import date
from decimal import Decimal

from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DecimalField,
    IntegerField,
    SelectField,
    SubmitField,
)
from wtforms.validators import DataRequired, NumberRange, Optional

from app.db_types import uuid_or_none


class PayrollPeriodForm(FlaskForm):
    driver_id = SelectField(_l("Driver"), coerce=uuid_or_none, validators=[DataRequired()])
    year = IntegerField(
        _l("Year"),
        validators=[DataRequired(), NumberRange(min=2022, max=2099)],
        default=lambda: date.today().year,
    )
    month = SelectField(
        _l("Month"),
        coerce=int,
        choices=[
            (1, "01 — January"), (2, "02 — February"), (3, "03 — March"),
            (4, "04 — April"), (5, "05 — May"), (6, "06 — June"),
            (7, "07 — July"), (8, "08 — August"), (9, "09 — September"),
            (10, "10 — October"), (11, "11 — November"), (12, "12 — December"),
        ],
        validators=[DataRequired()],
        default=lambda: date.today().month,
    )
    auto_fetch_nbp = BooleanField(
        _l("Fetch EUR/PLN from NBP automatically"),
        default=True,
    )
    eur_pln_rate = DecimalField(
        _l("EUR/PLN rate (leave 0 to auto-fetch)"),
        validators=[Optional(), NumberRange(min=Decimal("0"), max=Decimal("6"))],
        places=4,
        default=Decimal("0"),
    )
    days_abroad_override = IntegerField(
        _l("Days abroad override (blank = auto from segments)"),
        validators=[Optional(), NumberRange(min=0, max=31)],
    )
    submit = SubmitField(_l("Create and calculate"))
