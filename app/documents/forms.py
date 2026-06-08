"""Document forms — shared base with driver/vehicle variants, plus the
operator-facing form for managing the document-type catalogue."""

from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.documents.constants import ENTITY_TYPES


class _DocumentForm(FlaskForm):
    """Common document fields (PRD §4.3).

    ``document_type`` choices are assigned per-request from the ``document_type``
    catalogue (see ``document_type_choices``), so they vary by entity.
    """

    document_type = SelectField(_l("Document type"), validators=[DataRequired()])
    document_id = StringField(_l("Document number"), validators=[Optional(), Length(max=100)])
    start_date = DateField(_l("Start date"), validators=[Optional()])
    end_date = DateField(_l("End date (expiry)"), validators=[Optional()])
    file_links = TextAreaField(
        _l("Scan links (one URL per line)"),
        validators=[Optional(), Length(max=8000)],
    )
    submit = SubmitField(_l("Save"))


class DriverDocumentForm(_DocumentForm):
    # extra: license categories, e.g. "C+E" or "B,C,C+E"
    categories = StringField(_l("Categories (licence only)"), validators=[Optional(), Length(max=64)])


class VehicleDocumentForm(_DocumentForm):
    # extra: insurance company name
    insurance_company = StringField(
        _l("Insurance company (insurance only)"),
        validators=[Optional(), Length(max=120)],
    )


class DocumentTypeForm(FlaskForm):
    """Create / edit an entry in the document-type catalogue."""

    type = StringField(
        _l("Type code"),
        validators=[DataRequired(), Length(max=30)],
        description=_l("Short machine code, e.g. passport, insurance"),
    )
    entity_type = SelectField(
        _l("Belongs to"),
        choices=ENTITY_TYPES,
        validators=[DataRequired()],
    )
    label = StringField(_l("Display name"), validators=[Optional(), Length(max=100)])
    submit = SubmitField(_l("Save"))