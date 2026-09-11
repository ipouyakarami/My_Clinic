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


import datetime
import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailFailureLog, Medication, MedicationIntake

logger = logging.getLogger(__name__)


def send_telegram_message(chat_id, text):
    """Send a message via the Telegram Bot API. Fails silently (logged)."""
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or __import__("os").environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warn("Cannot send Telegram message: TELEGRAM_BOT_TOKEN not set")
        return False

    import requests

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        if resp.status_code != 200:
            logger.error("Telegram API error (%s) for chat %s: %s", resp.status_code, chat_id, resp.text[:200])
            return False
        return True
    except Exception as exc:
        logger.error("Failed to send Telegram message to chat %s: %s", chat_id, exc)
        return False


def build_telegram_message(patient_name, intakes, scheduled_time_str, date_str, site_url):
    """Build the text message for a Telegram medication reminder."""
    lines = [
        f"💊 Medication Reminder — MyClinic",
        f"",
        f"Hello {patient_name}, it's time to take your medication scheduled for {date_str} at {scheduled_time_str}.",
        f"",
    ]
    for intake in intakes:
        med_name = intake.medication.name
        dosage = intake.medication.dosage or "—"
        lines.append(f"• <b>{med_name}</b> — {dosage}")

    lines.append("")
    lines.append(f"Log in to {site_url} to mark as taken or skipped.")
    lines.append("")
    lines.append("MyClinic — Doctor appointment booking & medication tracking")
    return "\n".join(lines)


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
        patient = first.medication.patient
        patient_name = patient.user.get_full_name()
        scheduled_time_local = scheduled_time.astimezone(tz)
        scheduled_time_str = scheduled_time_local.strftime("%H:%M")
        date_str = scheduled_time_local.strftime("%Y/%m/%d")

        context = {
            "patient": patient_name,
            "intakes": intakes,
            "scheduled_time": scheduled_time_str,
            "date": date_str,
            "site_url": settings.SITE_URL,
        }

        # --- Send reminder via the patient's preferred channel ---
        # Linked patients: Telegram only (no email).
        # Unlinked patients: email only.
        sent = False
        if patient.telegram_chat_id:
            telegram_text = build_telegram_message(
                patient_name, intakes, scheduled_time_str, date_str, settings.SITE_URL,
            )
            sent = send_telegram_message(patient.telegram_chat_id, telegram_text)
            if not sent:
                logger.error("Telegram reminder failed for patient %s (chat %s)", email, patient.telegram_chat_id)
        else:
            try:
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
                sent = True
            except Exception as exc:
                logger.error("Failed to send medication reminder email for intakes %s: %s", [i.pk for i in intakes], exc)
                EmailFailureLog.objects.create(
                    intake_ids=[i.pk for i in intakes],
                    error_message=str(exc)[:1000],
                )

        if sent:
            MedicationIntake.objects.filter(pk__in=[i.pk for i in intakes]).update(reminder_sent=True)
        else:
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
    if medication.current_inventory > 0:
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
