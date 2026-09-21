import os
import re

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import MedicalTestResult


MAX_PDF_SIZE = 5 * 1024 * 1024  # 5MB


def validate_pdf_file(value):
    if not value:
        raise ValidationError(_("No file selected."))

    if value.size > MAX_PDF_SIZE:
        raise ValidationError(_("File size must not exceed 5 MB."))

    try:
        file_path = value.temporary_file_path()
    except AttributeError:
        file_obj = value.file if hasattr(value, 'file') else value
        header = file_obj.read(8)
        file_obj.seek(0)
        if not header.startswith(b'%PDF-'):
            raise ValidationError(_("File must be a PDF. The submitted file is not valid."))
        return

    if not file_path or not os.path.exists(file_path):
        raise ValidationError(_("Temporary file not found for validation. Please try again."))

    with open(file_path, 'rb') as f:
        header = f.read(8)

    if not header.startswith(b'%PDF-'):
        raise ValidationError(_("File must be a PDF. The submitted file is not valid."))


class MedicalTestResultForm(forms.ModelForm):
    class Meta:
        model = MedicalTestResult
        fields = ["category", "name", "pdf_file"]
        labels = {
            "category": _("Test Category"),
            "name": _("Test Name"),
            "pdf_file": _("PDF File"),
        }
        widgets = {
            "category": forms.Select(attrs={"class": "mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2"}),
            "name": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2", "placeholder": "e.g. CBC, Thyroid panel"}),
            "pdf_file": forms.FileInput(attrs={"accept": ".pdf"}),
        }

    def clean_pdf_file(self):
        validate_pdf_file(self.cleaned_data.get("pdf_file"))
        return self.cleaned_data["pdf_file"]

    def clean_name(self):
        name = self.cleaned_data.get("name", "")
        if name and not re.fullmatch(r"[\x20-\x7E]*", name):
            raise ValidationError(
                _("Test name must use English letters and numbers only (e.g. CBC, Thyroid panel).")
            )
        return name
