"""Vehicle forms."""

from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, Regexp

from app.db_types import uuid_or_none
from app.forms_common import country_choices
from app.vehicles.models import VEHICLE_TYPE_CHOICES


def _blank_to_none(value: str | None) -> str | None:
    """Empty/whitespace select value → ``None`` (registration country is optional)."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _country_choices_optional() -> list[tuple[str, str]]:
    """Country choices with a leading blank — registration country may be unknown."""
    return [("", _l("— not set —"))] + country_choices()


class VehicleForm(FlaskForm):
    registration_plate = StringField(
        _l("Registration plate"),
        validators=[DataRequired(), Length(min=3, max=20)],
        render_kw={"autocomplete": "off"},
    )
    vehicle_type = SelectField(
        _l("Type"),
        choices=VEHICLE_TYPE_CHOICES,
        validators=[DataRequired()],
    )
    vin = StringField(
        _l("VIN"),
        validators=[
            DataRequired(),
            Length(min=17, max=17),
            Regexp(r"^[A-HJ-NPR-Z0-9]{17}$", message=_l("VIN must be 17 alphanumeric chars (no I/O/Q)")),
        ],
    )
    brand = StringField(_l("Brand"), validators=[DataRequired(), Length(max=100)])
    model = StringField(_l("Model"), validators=[DataRequired(), Length(max=100)])
    organisation_uuid = SelectField(
        _l("Organisation"),
        coerce=uuid_or_none,
        validators=[DataRequired()],
    )
    registration_country = SelectField(
        _l("Country of registration"),
        choices=_country_choices_optional,
        validators=[Optional(), Length(max=3)],
        filters=[_blank_to_none],
    )
    registration_date = DateField(_l("Registration date"), validators=[Optional()])
    acquisition_date = DateField(_l("Acquisition date"), validators=[Optional()])
    manufacture_date = DateField(_l("Manufacture date"), validators=[Optional()])
    submit = SubmitField(_l("Save"))