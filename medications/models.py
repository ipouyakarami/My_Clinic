import datetime
import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import Patient


UNIT_CHOICES = [
    ("tablet", _("Tablet")),
    ("capsule", _("Capsule")),
    ("pill", _("Pill")),
    ("sachet", _("Sachet")),
    ("packet", _("Packet")),
    ("bottle", _("Bottle")),
    ("vial", _("Vial")),
    ("ampoule", _("Ampoule")),
    ("tube", _("Tube")),
    ("drop", _("Drop")),
    ("spray", _("Spray")),
    ("patch", _("Patch")),
    ("suppository", _("Suppository")),
    ("dose", _("Dose")),
    ("ml", _("mL")),
    ("mg", _("mg")),
    ("g", _("g")),
    ("mcg", _("mcg")),
    ("unit", _("Unit")),
]

INVENTORY_MAX_DIGITS = 18
INVENTORY_DECIMAL_PLACES = 6


class Medication(models.Model):
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="medications",
        verbose_name=_("patient"),
    )
    name = models.CharField(_("medication name"), max_length=200)
    unit = models.CharField(_("unit"), max_length=100, choices=UNIT_CHOICES)
    dosage = models.CharField(_("dosage"), max_length=200, default="")
    current_inventory = models.DecimalField(
        _("current inventory"),
        max_digits=INVENTORY_MAX_DIGITS,
        decimal_places=INVENTORY_DECIMAL_PLACES,
        default=Decimal("0"),
    )
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
        constraints = [
            models.CheckConstraint(
                condition=models.Q(current_inventory__gte=0),
                name="medication_current_inventory_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.patient})"

    @property
    def display_inventory(self):
        """Inventory formatted without trailing zeros (e.g. '9.5', not '9.500000')."""
        value = self.current_inventory
        if value == 0:
            return "0"
        return format(value, "f").rstrip("0").rstrip(".")


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


class TelegramActivationCode(models.Model):
    """
    Short-lived, single-use activation code generated by the Telegram bot
    when a patient sends /start.  The patient enters this code on the site
    to link their Telegram chat to their patient account.
    """

    code = models.CharField(_("activation code"), max_length=64, unique=True)
    telegram_chat_id = models.CharField(_("Telegram chat ID"), max_length=64)
    telegram_username = models.CharField(
        _("Telegram username"),
        max_length=64,
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    expires_at = models.DateTimeField(_("expires at"))
    used_at = models.DateTimeField(_("used at"), null=True, blank=True)
    used_by_patient = models.ForeignKey(
        Patient,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telegram_activation_codes",
        verbose_name=_("used by patient"),
    )

    class Meta:
        verbose_name = _("Telegram activation code")
        verbose_name_plural = _("Telegram activation codes")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["telegram_chat_id"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"Code {self.code} (chat {self.telegram_chat_id})"

    @classmethod
    def generate_for_chat(cls, chat_id, username=None):
        """Create a new activation code for a Telegram chat."""
        return cls.objects.create(
            code=secrets.token_urlsafe(6)[:8].upper(),
            telegram_chat_id=chat_id,
            telegram_username=username,
            expires_at=timezone.now() + datetime.timedelta(minutes=10),
        )

    @classmethod
    def get_valid_for_chat(cls, chat_id):
        """Return the most recent valid (unused, unexpired) code for a chat."""
        now = timezone.now()
        return cls.objects.filter(
            telegram_chat_id=chat_id,
            expires_at__gt=now,
            used_at__isnull=True,
        ).order_by("-created_at").first()

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_used(self):
        return self.used_at is not None
