import datetime

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import Patient, Doctor, User
from appointments.forms import GregorianDateField

from .models import (
    Medication,
    MedicationSchedule,
    MedicationIntake,
    UNIT_CHOICES,
    INVENTORY_MAX_DIGITS,
    INVENTORY_DECIMAL_PLACES,
)


FREQUENCY_CHOICES = MedicationSchedule.FrequencyType.choices
WEEKDAY_CHOICES = [
    ("sat", _("Saturday")),
    ("sun", _("Sunday")),
    ("mon", _("Monday")),
    ("tue", _("Tuesday")),
    ("wed", _("Wednesday")),
    ("thu", _("Thursday")),
    ("fri", _("Friday")),
]

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
LATIN_DIGITS = "0123456789"
_DIGIT_TRANSLATION = str.maketrans(
    PERSIAN_DIGITS + ARABIC_DIGITS,
    LATIN_DIGITS * 2,
)


def _normalize_digits(value):
    return value.translate(_DIGIT_TRANSLATION)


class MedicationStep1Form(forms.Form):
    name = forms.CharField(label=_("Medication Name"), max_length=200)
    unit = forms.ChoiceField(
        label=_("Unit"),
        choices=UNIT_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial = self.initial.get("unit")
        if initial and initial not in dict(UNIT_CHOICES):
            self.fields["unit"].choices = [(initial, f"{initial} (legacy)")] + list(UNIT_CHOICES)


class MedicationStep2Form(forms.Form):
    frequency_type = forms.ChoiceField(
        label=_("Frequency Type"),
        choices=FREQUENCY_CHOICES,
        widget=forms.RadioSelect(attrs={"class": "ml-3 mb-2"}),
    )


class MedicationStep3Form(forms.Form):
    medication_times = forms.CharField(
        label=_("Medication Times (one per line)"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "08:00\n14:00\n20:00"}),
    )
    medication_times_specific = forms.CharField(
        label=_("Medication Times (one per line)"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "08:00\n14:00\n20:00"}),
    )
    medication_times_n = forms.CharField(
        label=_("Medication Times (one per line)"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "08:00\n14:00\n20:00"}),
    )
    specific_days = forms.MultipleChoiceField(
        label=_("Specific Days"),
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "ml-3 mb-1"}),
        required=False,
    )
    n_days_interval = forms.IntegerField(label=_("Day Interval (N)"), min_value=1, required=False)
    x_hours_interval = forms.IntegerField(label=_("Hour Interval (X)"), min_value=1, required=False)
    anchor_date = GregorianDateField(
        label=_("Anchor Date"),
        required=False,
        allow_past=True,
        widget=forms.TextInput(attrs={"class": "calendar-picker", "data-min-date": "today"}),
    )
    anchor_time = forms.CharField(
        label=_("Anchor Time"),
        required=False,
        widget=forms.TextInput(attrs={"class": "time-picker", "placeholder": "HH:MM"}),
    )
    start_date = GregorianDateField(
        label=_("Start Date"),
        required=False,
        allow_past=True,
        widget=forms.TextInput(attrs={"class": "calendar-picker", "data-min-date": "today"}),
    )
    start_date_specific = GregorianDateField(
        label=_("Start Date"),
        required=False,
        allow_past=True,
        widget=forms.TextInput(attrs={"class": "calendar-picker", "data-min-date": "today"}),
    )
    start_date_n = GregorianDateField(
        label=_("Start Date"),
        required=False,
        allow_past=True,
        widget=forms.TextInput(attrs={"class": "calendar-picker", "data-min-date": "today"}),
    )
    end_date = GregorianDateField(
        label=_("End Date"),
        required=False,
        allow_past=True,
        widget=forms.TextInput(attrs={"class": "calendar-picker", "data-start-field": "start_date"}),
    )
    end_date_specific = GregorianDateField(
        label=_("End Date"),
        required=False,
        allow_past=True,
        widget=forms.TextInput(attrs={"class": "calendar-picker", "data-start-field": "start_date_specific"}),
    )
    end_date_n = GregorianDateField(
        label=_("End Date"),
        required=False,
        allow_past=True,
        widget=forms.TextInput(attrs={"class": "calendar-picker", "data-start-field": "start_date_n"}),
    )
    end_date_x = GregorianDateField(
        label=_("End Date"),
        required=False,
        allow_past=True,
        widget=forms.TextInput(attrs={"class": "calendar-picker", "data-start-field": "anchor_date"}),
    )

    _ACTIVE_DATE_FIELDS = {
        "daily": {"start_date", "end_date"},
        "specific_days": {"start_date_specific", "end_date_specific"},
        "every_n_days": {"start_date_n", "end_date_n"},
        "every_x_hours": {"anchor_date", "end_date_x"},
    }
    _ALL_DATE_FIELDS = [
        "start_date", "start_date_specific", "start_date_n",
        "end_date", "end_date_specific", "end_date_n", "end_date_x",
        "anchor_date",
    ]

    def __init__(self, *args, existing_schedule=None, **kwargs):
        self.existing_schedule = existing_schedule
        super().__init__(*args, **kwargs)

    def _today(self):
        return timezone.localtime(timezone.now()).date()

    def _now(self):
        return timezone.localtime(timezone.now())

    def _validate_not_past_date(self, field_name, submitted, existing, error_message):
        if submitted is None:
            return
        if existing is not None and submitted == existing:
            return
        if submitted < self._today():
            self.add_error(field_name, error_message)

    def _validate_not_past_datetime(self, field_name, submitted, existing, error_message):
        if submitted is None:
            return
        if existing is not None and submitted == existing:
            return
        if submitted < self._now():
            self.add_error(field_name, error_message)

    def _validate_end_not_before_start(self, end_field, end_value, start_value, error_message):
        if end_value is None:
            return
        if start_value is not None and end_value < start_value:
            self.add_error(end_field, error_message)

    _START_END_FIELD_MAP = {
        "daily": ("start_date", "end_date"),
        "specific_days": ("start_date_specific", "end_date_specific"),
        "every_n_days": ("start_date_n", "end_date_n"),
        "every_x_hours": ("anchor_date", "end_date_x"),
    }

    def clean(self):
        cleaned = super().clean()
        freq = self.data.get("frequency_type")

        active = self._ACTIVE_DATE_FIELDS.get(freq, set())
        for field_name in self._ALL_DATE_FIELDS:
            if field_name not in active:
                cleaned.pop(field_name, None)
                if self._errors is not None and field_name in self._errors:
                    del self._errors[field_name]

        existing = self.existing_schedule

        if freq == "daily":
            times = self._parse_times(cleaned.get("medication_times"))
            if not times:
                self.add_error("medication_times", _("Enter at least one medication time."))
            cleaned["medication_times"] = times
            self._validate_not_past_date(
                "start_date", cleaned.get("start_date"),
                existing.start_date if existing else None,
                _("Start date cannot be in the past."),
            )

        elif freq == "specific_days":
            days = cleaned.get("specific_days")
            if not days:
                self.add_error("specific_days", _("Select at least one day."))
            times = self._parse_times(cleaned.get("medication_times_specific"))
            if not times:
                self.add_error("medication_times_specific", _("Enter at least one medication time."))
            cleaned["specific_days"] = days
            cleaned["medication_times"] = times
            self._validate_not_past_date(
                "start_date_specific", cleaned.get("start_date_specific"),
                existing.start_date if existing else None,
                _("Start date cannot be in the past."),
            )

        elif freq == "every_n_days":
            if not cleaned.get("n_days_interval") or cleaned.get("n_days_interval") < 1:
                self.add_error("n_days_interval", _("Enter a number greater than or equal to 1."))
            times = self._parse_times(cleaned.get("medication_times_n"))
            if not times:
                self.add_error("medication_times_n", _("Enter at least one medication time."))
            if not cleaned.get("start_date_n"):
                self.add_error("start_date_n", _("Start date is required."))
            cleaned["n_days_interval"] = cleaned.get("n_days_interval")
            cleaned["medication_times"] = times
            self._validate_not_past_date(
                "start_date_n", cleaned.get("start_date_n"),
                existing.start_date if existing else None,
                _("Start date cannot be in the past."),
            )

        elif freq == "every_x_hours":
            if not cleaned.get("x_hours_interval") or cleaned.get("x_hours_interval") < 1:
                self.add_error("x_hours_interval", _("Enter a number greater than or equal to 1."))
            if not cleaned.get("anchor_date"):
                self.add_error("anchor_date", _("Anchor date is required."))
            if not cleaned.get("anchor_time"):
                self.add_error("anchor_time", _("Anchor time is required."))
            cleaned["x_hours_interval"] = cleaned.get("x_hours_interval")
            anchor_date = cleaned.get("anchor_date")
            anchor_time = cleaned.get("anchor_time")
            if anchor_date and anchor_time:
                try:
                    hh, mm = map(int, anchor_time.split(":"))
                    anchor_dt = timezone.make_aware(
                        datetime.datetime.combine(anchor_date, datetime.time(hh, mm)),
                        timezone.get_current_timezone(),
                    )
                except (ValueError, TypeError):
                    anchor_dt = None
                existing_ad = existing.anchor_datetime if existing else None
                if existing_ad is not None:
                    existing_ad_local = timezone.localtime(existing_ad)
                else:
                    existing_ad_local = None
                unchanged = (
                    anchor_dt is not None and existing_ad_local is not None
                    and anchor_dt.date() == existing_ad_local.date()
                    and hh == existing_ad_local.hour and mm == existing_ad_local.minute
                )
                if not unchanged and anchor_dt is not None and anchor_dt < self._now():
                    self.add_error("anchor_date", _("Anchor date cannot be in the past."))

        start_end = self._START_END_FIELD_MAP.get(freq)
        if start_end:
            start_field, end_field = start_end
            self._validate_end_not_before_start(
                end_field, cleaned.get(end_field), cleaned.get(start_field),
                _("End date cannot be before the start date."),
            )

        return cleaned

    def _parse_times(self, value):
        if not value:
            return []
        if isinstance(value, list):
            return value
        raw = value.split("\n") if "\n" in value else value.split(",")
        times = []
        for t in raw:
            t = t.strip()
            if not t:
                continue
            parts = t.split(":")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                hh = int(parts[0])
                mm = int(parts[1])
                if 0 <= hh <= 23 and 0 <= mm <= 59:
                    times.append(f"{hh:02d}:{mm:02d}")
        return times


class MedicationStep4Form(forms.Form):
    dosage = forms.CharField(
        label=_("Dosage"),
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            "type": "number",
            "step": "any",
            "min": "0",
            "inputmode": "decimal",
            "placeholder": "e.g. 1, 0.5, 2.5",
        }),
    )

    def clean_dosage(self):
        value = (self.cleaned_data.get("dosage") or "").strip()
        if not value:
            return ""
        normalized = _normalize_digits(value)
        try:
            from decimal import Decimal, InvalidOperation

            amount = Decimal(normalized)
        except (InvalidOperation, ValueError, ArithmeticError):
            raise ValidationError(_("Dosage must be a number."))
        if amount != amount:
            raise ValidationError(_("Dosage must be a number."))
        if amount <= 0:
            raise ValidationError(_("Dosage must be a positive number."))
        if amount.as_tuple().exponent < -INVENTORY_DECIMAL_PLACES:
            raise ValidationError(
                _(
                    "Dosage can have at most %(places)s decimal place(s)."
                    % {"places": INVENTORY_DECIMAL_PLACES}
                )
            )
        return str(amount)


class MedicationStep5Form(forms.Form):
    current_inventory = forms.DecimalField(
        label=_("Current Inventory"),
        initial=0,
        max_digits=INVENTORY_MAX_DIGITS,
        decimal_places=INVENTORY_DECIMAL_PLACES,
    )
    refill_reminder_threshold = forms.IntegerField(
        label=_("Refill Reminder Threshold"),
        min_value=0,
        required=False,
    )

    def __init__(self, *args, require_positive_inventory=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_positive_inventory = require_positive_inventory
        if require_positive_inventory:
            self.fields["current_inventory"].widget.attrs["min"] = "1"

    def clean_current_inventory(self):
        value = self.cleaned_data.get("current_inventory")
        if value is None:
            return value
        if value < 0:
            raise ValidationError(_("Current inventory cannot be negative."))
        if self.require_positive_inventory and value < 1:
            raise ValidationError(_("Current inventory must be more than 0."))
        return value


class MedicationStep6Form(forms.Form):
    pass


class MedicationDeleteForm(forms.Form):
    confirm = forms.BooleanField(label=_("Confirm Deletion"), required=True)


class MedicationIntakeActionForm(forms.Form):
    pass
