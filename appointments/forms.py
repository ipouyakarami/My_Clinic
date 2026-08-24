from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models import Doctor, Patient, User


class GregorianDateField(forms.Field):
    def __init__(self, **kwargs):
        kwargs.setdefault("widget", forms.TextInput(attrs={"placeholder": "YYYY/MM/DD"}))
        super().__init__(**kwargs)

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return None
        value = value.strip()
        try:
            parts = value.split("/")
            if len(parts) != 3:
                raise ValueError("Invalid date format")
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            from datetime import date
            result = date(year, month, day)
            if result < timezone.now().date():
                raise ValidationError("Date cannot be in the past.")
            return result
        except (ValueError, TypeError) as exc:
            raise ValidationError("Invalid date. Please use the YYYY/MM/DD format.") from exc

    def prepare_value(self, value):
        if isinstance(value, str):
            return value
        if hasattr(value, "strftime"):
            return value.strftime("%Y/%m/%d")
        return value


class WorkingHoursForm(forms.Form):
    date = GregorianDateField(label="Date")
    start_time = forms.CharField(
        label="Start Time",
        widget=forms.TextInput(attrs={"class": "time-range-picker-start", "placeholder": "HH:MM"}),
    )
    end_time = forms.CharField(
        label="End Time",
        widget=forms.TextInput(attrs={"class": "time-range-picker-end", "placeholder": "HH:MM"}),
    )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        if start and end and end <= start:
            raise forms.ValidationError("End time must be after start time.")
        return cleaned


class VisitSummaryForm(forms.Form):
    doctor_summary = forms.CharField(
        label="Visit Summary",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Visit notes..."}),
        max_length=2000,
    )
