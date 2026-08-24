import datetime

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import Doctor, Patient


class TimeSlot(models.Model):
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name="time_slots",
        verbose_name=_("doctor"),
    )
    date = models.DateField(_("date"))
    start_time = models.TimeField(_("start time"))
    end_time = models.TimeField(_("end time"))
    is_booked = models.BooleanField(_("booked"), default=False)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("time slot")
        verbose_name_plural = _("time slots")
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "date", "start_time"],
                name="unique_doctor_date_start_time",
            )
        ]
        ordering = ["date", "start_time"]

    def __str__(self):
        return f"{self.doctor} — {self.date} {self.start_time:%H:%M}"

    @property
    def start_datetime(self):
        naive = datetime.datetime.combine(self.date, self.start_time)
        return timezone.make_aware(naive, timezone.get_current_timezone())

    @property
    def is_past(self):
        return self.start_datetime < timezone.now()


class Appointment(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")

    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="appointments",
        verbose_name=_("patient"),
    )
    time_slot = models.OneToOneField(
        TimeSlot,
        on_delete=models.CASCADE,
        related_name="appointment",
        verbose_name=_("time slot"),
    )
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    doctor_summary = models.TextField(_("doctor's visit summary"), blank=True, default="")
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)

    class Meta:
        verbose_name = _("appointment")
        verbose_name_plural = _("appointments")
        ordering = ["-time_slot__date", "-time_slot__start_time"]

    def __str__(self):
        return f"{self.patient} — {self.time_slot}"
