import datetime
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import Patient


class Medication(models.Model):
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="medications",
        verbose_name=_("patient"),
    )
    name = models.CharField(_("medication name"), max_length=200)
    unit = models.CharField(_("unit"), max_length=100)
    dosage = models.CharField(_("dosage"), max_length=200, default="")
    current_inventory = models.IntegerField(_("current inventory"), default=0)
    refill_reminder_threshold = models.IntegerField(
        _("refill reminder threshold"),
        null=True,
        blank=True,
    )
    refill_reminder_sent = models.BooleanField(_("refill reminder sent"), default=False)
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("medication")
        verbose_name_plural = _("medications")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.patient})"


class MedicationSchedule(models.Model):
    class FrequencyType(models.TextChoices):
        DAILY = "daily", _("Daily")
        SPECIFIC_DAYS = "specific_days", _("Specific Days")
        EVERY_N_DAYS = "every_n_days", _("Every N Days")
        EVERY_X_HOURS = "every_x_hours", _("Every X Hours")

    medication = models.ForeignKey(
        Medication,
        on_delete=models.CASCADE,
        related_name="schedules",
        verbose_name=_("medication"),
    )
    frequency_type = models.CharField(
        _("frequency type"),
        max_length=20,
        choices=FrequencyType.choices,
    )
    specific_days = models.JSONField(
        _("specific days"),
        default=list,
        blank=True,
    )
    n_days_interval = models.IntegerField(
        _("day interval (N)"),
        null=True,
        blank=True,
    )
    x_hours_interval = models.IntegerField(
        _("hour interval (X)"),
        null=True,
        blank=True,
    )
    medication_times = models.JSONField(
        _("medication times"),
        default=list,
        blank=True,
    )
    anchor_datetime = models.DateTimeField(
        _("anchor datetime"),
        null=True,
        blank=True,
    )
    start_date = models.DateField(_("start date"), null=True, blank=True)
    end_date = models.DateField(_("end date"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("medication schedule")
        verbose_name_plural = _("medication schedules")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.medication.name} — {self.get_frequency_type_display()}"


class MedicationIntake(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        TAKEN = "taken", _("Taken")
        SKIPPED = "skipped", _("Skipped")
        MISSED = "missed", _("Missed")

    medication = models.ForeignKey(
        Medication,
        on_delete=models.CASCADE,
        related_name="intakes",
        verbose_name=_("medication"),
    )
    scheduled_time = models.DateTimeField(_("scheduled time"))
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    recorded_at = models.DateTimeField(_("recorded at"), null=True, blank=True)
    reminder_sent = models.BooleanField(_("reminder sent"), default=False)
    token = models.CharField(_("one-time token"), max_length=128, unique=True, default=uuid.uuid4)

    class Meta:
        verbose_name = _("medication intake")
        verbose_name_plural = _("medication intakes")
        ordering = ["scheduled_time"]
        indexes = [
            models.Index(fields=["scheduled_time", "status"]),
            models.Index(fields=["medication", "scheduled_time"]),
        ]

    def __str__(self):
        return f"{self.medication.name} — {self.scheduled_time:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = str(uuid.uuid4())
        super().save(*args, **kwargs)


class EmailFailureLog(models.Model):
    intake_ids = models.JSONField(_("intake IDs"))
    medication = models.ForeignKey(
        Medication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("medication"),
    )
    error_message = models.TextField(_("error message"))
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("email failure log")
        verbose_name_plural = _("email failure logs")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Email failure at {self.created_at}"
