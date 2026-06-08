"""Trip and TripSegment forms."""

from datetime import date
from decimal import Decimal

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

from app.trips.models import SegmentType


class TripForm(FlaskForm):
    driver_id = SelectField(_l("Driver"), coerce=int, validators=[DataRequired()])
    vehicle_id = SelectField(_l("Vehicle (truck)"), coerce=int, validators=[Optional()])
    trip_number = StringField(
        _l("Trip number"),
        validators=[DataRequired(), Length(max=32)],
    )
    start_date = DateField(_l("Start date"), validators=[DataRequired()], default=date.today)
    end_date = DateField(_l("End date"), validators=[DataRequired()], default=date.today)
    notes = TextAreaField(_l("Notes"), validators=[Optional(), Length(max=1000)])
    submit = SubmitField(_l("Save"))


class TripSegmentForm(FlaskForm):
    sequence = IntegerField(
        _l("Sequence"),
        validators=[DataRequired(), NumberRange(min=0)],
        default=0,
    )
    work_date = DateField(_l("Work date"), validators=[DataRequired()], default=date.today)
    country = StringField(
        _l("Country (ISO-2)"),
        validators=[
            DataRequired(),
            Length(min=2, max=2),
            Regexp(r"^[A-Z]{2}$", message=_l("Must be 2-letter ISO code: DE, FR, IT, ...")),
        ],
        default="DE",
    )
    segment_type = SelectField(
        _l("Segment type"),
        choices=[(t.value, t.name.replace("_", " ").title()) for t in SegmentType],
        validators=[DataRequired()],
    )
    work_hours = DecimalField(
        _l("Work hours"),
        validators=[DataRequired(), NumberRange(min=Decimal("0.01"), max=Decimal("24"))],
        places=2,
    )
    rate_name = StringField(
        _l("Rate name (from YAML)"),
        validators=[DataRequired(), Length(max=64)],
        default="driver_default",
        render_kw={
            "placeholder": "driver_default / statutory_minimum / driver_coef_150m",
        },
    )
    notes = StringField(_l("Notes"), validators=[Optional(), Length(max=500)])
    submit = SubmitField(_l("Save segment"))
