"""Document forms — shared base with driver/vehicle variants, plus the
operator-facing form for managing the document-type catalogue."""

from types import SimpleNamespace

from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.docs.constants import ENTITY_TYPES
from app.docs.validation import validate_recognition


class _IdentifierValidationMixin:
    """Enforce the document identifier/date format rules on insert/update by
    reusing :func:`validate_recognition` and mapping its errors onto fields."""

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators=extra_validators):
            return False
        probe = SimpleNamespace(
            document_type=getattr(self, "document_type", None) and self.document_type.data,
            document_id=(self.document_id.data or None) if hasattr(self, "document_id") else None,
            start_date=self.start_date.data if hasattr(self, "start_date") else None,
            end_date=self.end_date.data if hasattr(self, "end_date") else None,
            identification_id=None,
            passport_number=None,
            nationality=None,
            birth_date=None,
        )
        errors = validate_recognition(probe)
        for field_name, message in errors.items():
            field = getattr(self, field_name, None)
            if field is not None:
                field.errors = list(field.errors) + [message]
        return not errors


class _DocumentForm(_IdentifierValidationMixin, FlaskForm):
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


class DriverFileForm(_IdentifierValidationMixin, FlaskForm):
    """Edit a single file attached to a driver document (driver_file row)."""

    file_link = StringField(
        _l("File link / path"), validators=[DataRequired(), Length(max=2000)]
    )
    document_type = StringField(_l("Type"), validators=[Optional(), Length(max=30)])
    document_id = StringField(_l("Document number"), validators=[Optional(), Length(max=100)])
    start_date = DateField(_l("Start date"), validators=[Optional()])
    end_date = DateField(_l("End date (expiry)"), validators=[Optional()])
    submit = SubmitField(_l("Save"))


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