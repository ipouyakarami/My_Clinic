import os

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import MedicalTestResult


MAX_PDF_SIZE = 5 * 1024 * 1024  # 5MB


def validate_pdf_file(value):
    if not value:
        raise ValidationError(_("فایل انتخاب نشده است."))

    if value.size > MAX_PDF_SIZE:
        raise ValidationError(_("حجم فایل نباید بیشتر از ۵ مگابایت باشد."))

    try:
        file_path = value.temporary_file_path()
    except AttributeError:
        file_obj = value.file if hasattr(value, 'file') else value
        header = file_obj.read(8)
        file_obj.seek(0)
        if not header.startswith(b'%PDF-'):
            raise ValidationError(_("فایل باید از نوع PDF باشد. فایل ارسال شده معتبر نیست."))
        return

    if not file_path or not os.path.exists(file_path):
        raise ValidationError(_("فایل موقت برای بررسی یافت نشد. لطفاً دوباره تلاش کنید."))

    with open(file_path, 'rb') as f:
        header = f.read(8)

    if not header.startswith(b'%PDF-'):
        raise ValidationError(_("فایل باید از نوع PDF باشد. فایل ارسال شده معتبر نیست."))


class MedicalTestResultForm(forms.ModelForm):
    class Meta:
        model = MedicalTestResult
        fields = ["category", "pdf_file"]
        labels = {
            "category": "دسته‌بندی",
            "pdf_file": "فایل PDF",
        }
        widgets = {
            "pdf_file": forms.FileInput(attrs={"accept": ".pdf"}),
        }

    def clean_pdf_file(self):
        validate_pdf_file(self.cleaned_data.get("pdf_file"))
        return self.cleaned_data["pdf_file"]
