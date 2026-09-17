"""
Shared service functions for the medications app.

These functions are used by both the web views and the Telegram bot so that
the "today's medications" query logic and the intake-action logic are not
duplicated between the two code paths.
"""

import re
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .models import Medication, MedicationIntake


_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_LATIN_DIGITS = "0123456789"
_DIGIT_TRANSLATION = str.maketrans(
    _PERSIAN_DIGITS + _ARABIC_DIGITS,
    _LATIN_DIGITS * 2,
)
_DOSAGE_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def normalize_digits(value):
    return str(value).translate(_DIGIT_TRANSLATION)


def parse_dosage_amount(dosage):
    """
    Return the numeric amount one intake should decrease inventory by.

    Falls back to 1 when dosage is empty or contains no parseable number so a
    valid decrement always happens.
    """
    if not dosage:
        return Decimal("1")
    match = _DOSAGE_RE.search(normalize_digits(dosage))
    if not match:
        return Decimal("1")
    try:
        amount = Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return Decimal("1")
    if amount <= 0:
        return Decimal("1")
    return amount


def clean_decimal(value):
    """
    Collapse a Decimal to fixed notation without trailing zeros so stored
    inventory values stay clean (e.g. "10", "9.5" not "9.50").
    """
    if value == 0:
        return Decimal("0")
    text = format(value, "f").rstrip("0").rstrip(".")
    return Decimal(text or "0")


def get_todays_intakes_for_patient(patient):
    """
    Return today's MedicationIntake rows for the given patient,
    grouped by scheduled_time (just like the dashboard widget).

    Returns a list of (scheduled_time, [intakes]) tuples, sorted by scheduled_time.
    """
    now = timezone.now()
    tz = timezone.get_current_timezone()
    today = now.date()

    start_dt = timezone.make_aware(
        timezone.datetime.combine(today, timezone.datetime.min.time()),
        tz,
    )
    end_dt = start_dt + timezone.timedelta(days=1)

    today_intakes = (
        MedicationIntake.objects.filter(
            medication__patient=patient,
            scheduled_time__gte=start_dt,
            scheduled_time__lt=end_dt,
        )
        .select_related("medication")
        .order_by("scheduled_time")
    )

    groups = {}
    for intake in today_intakes:
        key = intake.scheduled_time
        groups.setdefault(key, []).append(intake)

    return list(groups.items())


def mark_intake_taken(intake):
    """
    Mark an intake as taken and trigger the inventory/refill side effects.

    Reuses the same logic as the web views (MedicationIntakeActionView /
    MedicationIntakeBatchView) by calling handle_intake_taken.
    Returns True if the status was changed, False if it was already taken/skipped
    or if there is not enough inventory to cover this dose.
    """
    if intake.status != MedicationIntake.Status.PENDING:
        return False

    medication = intake.medication
    medication.refresh_from_db(fields=["current_inventory"])
    if medication.current_inventory < parse_dosage_amount(medication.dosage):
        return False

    from .tasks import handle_intake_taken

    intake.status = MedicationIntake.Status.TAKEN
    intake.recorded_at = timezone.now()
    intake.save(update_fields=["status", "recorded_at"])
    handle_intake_taken(intake.pk)
    return True


def mark_intake_skipped(intake):
    """
    Mark an intake as skipped.
    Returns True if the status was changed, False if it was already taken/skipped.
    """
    if intake.status != MedicationIntake.Status.PENDING:
        return False

    intake.status = MedicationIntake.Status.SKIPPED
    intake.recorded_at = timezone.now()
    intake.save(update_fields=["status", "recorded_at"])
    return True
