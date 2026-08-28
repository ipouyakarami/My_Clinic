import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailFailureLog, Medication, MedicationIntake

logger = logging.getLogger(__name__)


@shared_task
def generate_intakes_for_schedule_task(schedule_id):
    from .models import MedicationSchedule
    from .views import _generate_intakes_for_schedule
    try:
        schedule = MedicationSchedule.objects.get(pk=schedule_id)
        _generate_intakes_for_schedule(schedule)
    except MedicationSchedule.DoesNotExist:
        pass


@shared_task
def extend_intake_window():
    from .models import MedicationSchedule
    from .views import _generate_intakes_for_schedule
    schedules = MedicationSchedule.objects.filter(medication__is_active=True)
    for schedule in schedules:
        _generate_intakes_for_schedule(schedule, horizon_days=7)


@shared_task
def send_reminder_emails():
    now = timezone.now()
    tz = timezone.get_current_timezone()
    pending_intakes = MedicationIntake.objects.filter(
        status=MedicationIntake.Status.PENDING,
        reminder_sent=False,
        scheduled_time__lte=now,
    ).select_related("medication", "medication__patient", "medication__patient__user")

    groups = {}
    for intake in pending_intakes:
        key = (intake.medication.patient.user.email, intake.scheduled_time)
        groups.setdefault(key, []).append(intake)

    for (email, scheduled_time), intakes in groups.items():
        first = intakes[0]
        try:
            context = {
                "patient": first.medication.patient.user.get_full_name(),
                "intakes": intakes,
                "scheduled_time": scheduled_time.astimezone(tz).strftime("%H:%M"),
                "date": scheduled_time.astimezone(tz).strftime("%Y/%m/%d"),
                "site_url": settings.SITE_URL,
            }
            html_body = render_to_string("emails/medication_reminder.html", context)
            text_body = render_to_string("emails/medication_reminder.txt", context)
            send_mail(
                "Medication Time — MyClinic",
                text_body,
                None,
                [email],
                html_message=html_body,
                fail_silently=False,
            )
            MedicationIntake.objects.filter(pk__in=[i.pk for i in intakes]).update(reminder_sent=True)
        except Exception as exc:
            logger.error("Failed to send medication reminder email for intakes %s: %s", [i.pk for i in intakes], exc)
            EmailFailureLog.objects.create(
                intake_ids=[i.pk for i in intakes],
                error_message=str(exc)[:1000],
            )
            for i in intakes:
                i.reminder_sent = False
                i.save(update_fields=["reminder_sent"])


@shared_task
def handle_intake_taken(intake_id):
    try:
        intake = MedicationIntake.objects.get(pk=intake_id)
    except MedicationIntake.DoesNotExist:
        return
    medication = intake.medication
    if medication.current_inventory < 0:
        medication.current_inventory = 0
    else:
        medication.current_inventory -= 1
    medication.save(update_fields=["current_inventory"])

    if medication.refill_reminder_threshold is not None:
        if medication.current_inventory <= medication.refill_reminder_threshold and not medication.refill_reminder_sent:
            send_refill_reminder_email(medication)
            medication.refill_reminder_sent = True
            medication.save(update_fields=["refill_reminder_sent"])


def send_refill_reminder_email(medication):
    try:
        html_body = render_to_string("emails/refill_reminder.html", {
            "patient": medication.patient.user.get_full_name(),
            "medication": medication,
            "remaining": medication.current_inventory,
        })
        text_body = render_to_string("emails/refill_reminder.txt", {
            "patient": medication.patient.user.get_full_name(),
            "medication": medication,
            "remaining": medication.current_inventory,
        })
        send_mail(
            "Time to Refill Your Medication — MyClinic",
            text_body,
            None,
            [medication.patient.user.email],
            html_message=html_body,
            fail_silently=False,
        )
    except Exception as exc:
        logger.error("Failed to send refill reminder for medication %s: %s", medication.pk, exc)
        EmailFailureLog.objects.create(
            intake_ids=[],
            error_message=f"Refill reminder failed: {str(exc)[:1000]}",
            medication=medication,
        )
