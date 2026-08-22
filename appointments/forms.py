import jdatetime

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models import Doctor, Patient, User


class JalaliDateField(forms.Field):
    def __init__(self, **kwargs):
        kwargs.setdefault("widget", forms.TextInput(attrs={"placeholder": "مثال: 1403/07/15"}))
        super().__init__(**kwargs)

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return None
        value = value.strip()
        try:
            parts = value.split("/")
            if len(parts) != 3:
                raise ValueError("فرمت تاریخ نامعتبر است")
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            jd = jdatetime.date(year, month, day)
            gregorian = jd.togregorian()
            if gregorian < timezone.now().date():
                raise ValidationError("تاریخ نمی‌تواند در گذشته باشد.")
            return gregorian
        except (ValueError, TypeError) as exc:
            raise ValidationError("تاریخ وارد شده نامعتبر است. لطفاً از فرمت سال/ماه/روز استفاده کنید.") from exc


class WorkingHoursForm(forms.Form):
    jalali_date = JalaliDateField(label="تاریخ (شمسی)")
    start_time = forms.TimeField(
        label="ساعت شروع",
        widget=forms.TimeInput(attrs={"type": "time"}),
        input_formats=["%H:%M"],
    )
    end_time = forms.TimeField(
        label="ساعت پایان",
        widget=forms.TimeInput(attrs={"type": "time"}),
        input_formats=["%H:%M"],
    )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        if start and end and end <= start:
            raise forms.ValidationError("ساعت پایان باید بعد از ساعت شروع باشد.")
        return cleaned


class VisitSummaryForm(forms.Form):
    doctor_summary = forms.CharField(
        label="خلاصه ویزیت",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "یادداشت‌های ویزیت..."}),
        max_length=2000,
    )
