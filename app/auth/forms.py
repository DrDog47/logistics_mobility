"""WTForms definitions for auth blueprint."""

from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length


class LoginForm(FlaskForm):
    login = StringField(
        _l("Login"),
        validators=[DataRequired(), Length(min=3, max=64)],
        render_kw={"autocomplete": "username", "autofocus": True},
    )
    password = PasswordField(
        _l("Password"),
        validators=[DataRequired(), Length(min=8, max=128)],
        render_kw={"autocomplete": "current-password"},
    )
    remember_me = BooleanField(_l("Remember me"))
    submit = SubmitField(_l("Sign in"))
