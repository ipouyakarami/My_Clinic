"""
Shared service functions for the medications app.

These functions are used by both the web views and the Telegram bot so that
the "today's medications" query logic and the intake-action logic are not
duplicated between the two code paths.
"""

from django.utils import timezone

from .models import Medication, MedicationIntake


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
    Returns True if the status was changed, False if it was already taken/skipped.
    """
    if intake.status != MedicationIntake.Status.PENDING:
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
