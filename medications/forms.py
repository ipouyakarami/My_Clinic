from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from accounts.models import Patient, Doctor, User
from appointments.forms import GregorianDateField

from .models import Medication, MedicationSchedule, MedicationIntake


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


class MedicationStep1Form(forms.Form):
    name = forms.CharField(label=_("Medication Name"), max_length=200)
    unit = forms.CharField(label=_("Unit"), max_length=100)


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
        widget=forms.TextInput(attrs={"class": "calendar-picker"}),
    )
    anchor_time = forms.CharField(
        label=_("Anchor Time"),
        required=False,
        widget=forms.TextInput(attrs={"class": "time-picker", "placeholder": "HH:MM"}),
    )
    start_date = GregorianDateField(
        label=_("Start Date"),
        required=False,
        widget=forms.TextInput(attrs={"class": "calendar-picker"}),
    )
    start_date_specific = GregorianDateField(
        label=_("Start Date"),
        required=False,
        widget=forms.TextInput(attrs={"class": "calendar-picker"}),
    )
    start_date_n = GregorianDateField(
        label=_("Start Date"),
        required=False,
        widget=forms.TextInput(attrs={"class": "calendar-picker"}),
    )
    end_date = GregorianDateField(
        label=_("End Date"),
        required=False,
        widget=forms.TextInput(attrs={"class": "calendar-picker"}),
    )
    end_date_specific = GregorianDateField(
        label=_("End Date"),
        required=False,
        widget=forms.TextInput(attrs={"class": "calendar-picker"}),
    )
    end_date_n = GregorianDateField(
        label=_("End Date"),
        required=False,
        widget=forms.TextInput(attrs={"class": "calendar-picker"}),
    )
    end_date_x = GregorianDateField(
        label=_("End Date"),
        required=False,
        widget=forms.TextInput(attrs={"class": "calendar-picker"}),
    )

    def clean(self):
        cleaned = super().clean()
        freq = self.data.get("frequency_type")

        if freq == "daily":
            times = self._parse_times(cleaned.get("medication_times"))
            if not times:
                self.add_error("medication_times", _("Enter at least one medication time."))
            cleaned["medication_times"] = times

        elif freq == "specific_days":
            days = cleaned.get("specific_days")
            if not days:
                self.add_error("specific_days", _("Select at least one day."))
            times = self._parse_times(cleaned.get("medication_times_specific"))
            if not times:
                self.add_error("medication_times_specific", _("Enter at least one medication time."))
            cleaned["specific_days"] = days
            cleaned["medication_times"] = times

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

        elif freq == "every_x_hours":
            if not cleaned.get("x_hours_interval") or cleaned.get("x_hours_interval") < 1:
                self.add_error("x_hours_interval", _("Enter a number greater than or equal to 1."))
            if not cleaned.get("anchor_date"):
                self.add_error("anchor_date", _("Anchor date is required."))
            if not cleaned.get("anchor_time"):
                self.add_error("anchor_time", _("Anchor time is required."))
            cleaned["x_hours_interval"] = cleaned.get("x_hours_interval")

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
    dosage = forms.CharField(label=_("Dosage"), max_length=200, required=False)


class MedicationStep5Form(forms.Form):
    current_inventory = forms.IntegerField(label=_("Current Inventory"), min_value=0, initial=0)
    refill_reminder_threshold = forms.IntegerField(
        label=_("Refill Reminder Threshold"),
        min_value=0,
        required=False,
    )


class MedicationStep6Form(forms.Form):
    pass


class MedicationDeleteForm(forms.Form):
    confirm = forms.BooleanField(label=_("Confirm Deletion"), required=True)


class MedicationIntakeActionForm(forms.Form):
    pass
