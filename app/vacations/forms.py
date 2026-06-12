"""WTForms for the vacations blueprint."""

from __future__ import annotations

from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import DateField, IntegerField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

from app.db_types import uuid_or_none
from app.vacations.models import LeaveKind

_KIND_LABELS = {
    LeaveKind.ANNUAL: _l("Annual leave"),
    LeaveKind.ON_DEMAND: _l("On-demand leave"),
    LeaveKind.SICK: _l("Sick leave (L4)"),
    LeaveKind.UNPAID: _l("Unpaid leave"),
    LeaveKind.OTHER: _l("Other"),
}


class LeaveEntryForm(FlaskForm):
    kind = SelectField(
        _l("Type"),
        choices=[(k.value, _KIND_LABELS[k]) for k in LeaveKind],
        validators=[DataRequired()],
        default=LeaveKind.ANNUAL.value,
    )
    start_date = DateField(_l("Start date"), validators=[DataRequired()])
    end_date = DateField(_l("End date"), validators=[DataRequired()])
    note = StringField(_l("Note"), validators=[Optional(), Length(max=500)])
    submit = SubmitField(_l("Save"))

    def validate_end_date(self, field: DateField) -> None:
        if self.start_date.data and field.data and field.data < self.start_date.data:
            raise ValidationError(_l("End date must not be before the start date."))


class FleetLeaveForm(LeaveEntryForm):
    """Leave form for the fleet vacations page, where the driver is chosen too.

    ``driver_uuid.choices`` is populated in the route from the active roster.
    """

    driver_uuid = SelectField(
        _l("Driver"), coerce=uuid_or_none, validators=[DataRequired()]
    )


class EntitlementForm(FlaskForm):
    base_days = IntegerField(
        _l("Base days"), validators=[DataRequired(), NumberRange(min=0, max=366)]
    )
    carried_over_days = IntegerField(
        _l("Carried over"), validators=[Optional(), NumberRange(min=0, max=366)], default=0
    )
    adjustment_days = IntegerField(
        _l("Adjustment (±)"), validators=[Optional(), NumberRange(min=-366, max=366)], default=0
    )
    submit = SubmitField(_l("Save"))
