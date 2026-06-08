"""Organisation forms."""

from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length

from app.forms_common import country_choices


class OrganisationForm(FlaskForm):
    name = StringField(_l("Company name"), validators=[DataRequired(), Length(max=255)])
    national_id = StringField(
        _l("National ID (NIP / ИНН / EDRPOU)"),
        validators=[DataRequired(), Length(max=100)],
    )
    country = SelectField(
        _l("Country"),
        choices=country_choices,
        validators=[DataRequired(), Length(min=3, max=3)],
        default="POL",
    )
    city = StringField(_l("City"), validators=[DataRequired(), Length(max=100)])
    address = StringField(_l("Address"), validators=[DataRequired(), Length(max=255)])
    submit = SubmitField(_l("Save"))