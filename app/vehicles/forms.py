"""Vehicle forms."""

from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import DateField, IntegerField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, Regexp

from app.vehicles.models import VehicleType


class VehicleForm(FlaskForm):
    plate = StringField(
        _l("License plate"),
        validators=[DataRequired(), Length(min=3, max=20)],
        render_kw={"autocomplete": "off"},
    )
    vehicle_type = SelectField(
        _l("Type"),
        choices=[(t.value, t.name.title()) for t in VehicleType],
        validators=[DataRequired()],
    )
    vin = StringField(
        _l("VIN"),
        validators=[
            Optional(),
            Length(min=17, max=17),
            Regexp(r"^[A-HJ-NPR-Z0-9]{17}$", message=_l("VIN must be 17 alphanumeric chars (no I/O/Q)")),
        ],
    )
    make = StringField(_l("Make"), validators=[Optional(), Length(max=64)])
    model = StringField(_l("Model"), validators=[Optional(), Length(max=64)])
    year = IntegerField(_l("Year"), validators=[Optional(), NumberRange(min=1990, max=2099)])
    purchase_date = DateField(_l("Purchase date"), validators=[Optional()])
    submit = SubmitField(_l("Save"))
