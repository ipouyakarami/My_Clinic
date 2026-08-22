import datetime

from django.db import models
from django.utils import timezone

from accounts.models import Doctor, Patient


class TimeSlot(models.Model):
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name="time_slots",
        verbose_name="پزشک",
    )
    date = models.DateField("تاریخ (میلادی)")
    start_time = models.TimeField("ساعت شروع")
    end_time = models.TimeField("ساعت پایان")
    is_booked = models.BooleanField("رزرو شده", default=False)
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)

    class Meta:
        verbose_name = "نوبت"
        verbose_name_plural = "نوبت‌ها"
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

    @property
    def jalali_date(self):
        import jdatetime
        return jdatetime.date.fromgregorian(date=self.date).strftime("%Y/%m/%d")


class Appointment(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "فعال"
        COMPLETED = "completed", "انجام شده"
        CANCELLED = "cancelled", "لغو شده"

    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="appointments",
        verbose_name="بیمار",
    )
    time_slot = models.OneToOneField(
        TimeSlot,
        on_delete=models.CASCADE,
        related_name="appointment",
        verbose_name="نوبت",
    )
    status = models.CharField(
        "وضعیت",
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    doctor_summary = models.TextField("خلاصه ویزیت پزشک", blank=True, default="")
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)
    cancelled_at = models.DateTimeField("زمان لغو", null=True, blank=True)

    class Meta:
        verbose_name = "ویزیت"
        verbose_name_plural = "ویزیت‌ها"
        ordering = ["-time_slot__date", "-time_slot__start_time"]

    def __str__(self):
        return f"{self.patient} — {self.time_slot}"
