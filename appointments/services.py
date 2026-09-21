import datetime

from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.models import Patient

from .models import Appointment, TimeSlot


class SlotAlreadyBooked(Exception):
    pass


class SlotTooSoon(Exception):
    pass


def generate_time_slots(doctor, slot_date, start_time, end_time):
    """Generate 30-minute ``TimeSlot`` rows for ``doctor`` on ``slot_date``.

    Dedupes against existing slots for the same ``(doctor, date, start_time)``
    so re-running never creates duplicates, and drops any trailing minutes that
    don't fit a full 30-minute block.

    Returns a tuple ``(created, dropped_minutes)``.
    """
    existing_slots = TimeSlot.objects.filter(
        doctor=doctor,
        date=slot_date,
        start_time__gte=start_time,
        start_time__lt=end_time,
    )
    existing_starts = set(existing_slots.values_list("start_time", flat=True))

    created = []
    dropped_minutes = 0
    current = datetime.datetime.combine(slot_date, start_time)
    end_dt = datetime.datetime.combine(slot_date, end_time)

    while current + datetime.timedelta(minutes=30) <= end_dt:
        slot_start = current.time()
        if slot_start not in existing_starts:
            slot = TimeSlot.objects.create(
                doctor=doctor,
                date=slot_date,
                start_time=slot_start,
                end_time=(current + datetime.timedelta(minutes=30)).time(),
            )
            created.append(slot)
        current += datetime.timedelta(minutes=30)

    if current < end_dt:
        dropped_minutes = int((end_dt - current).total_seconds() // 60)

    return created, dropped_minutes


def book_appointment(slot, patient):
    """Book ``slot`` for ``patient`` using the real booking logic.

    Wraps the lock + ``Appointment`` creation + confirmation email in the
    same ``select_for_update()`` transaction the view uses. Raises
    ``SlotAlreadyBooked`` if the slot was claimed by a concurrent caller
    or already has an active/completed appointment.

    A slot whose previous appointment was cancelled is re-bookable: the
    existing (cancelled) ``Appointment`` row is Reactivated and reassigned to
    ``patient``, because ``Appointment.time_slot`` is a one-to-one field and
    the two rows cannot coexist for the same slot.

    Raises ``SlotTooSoon`` if less than one hour remains until the slot
    starts; patients may not book at the last minute.

    Returns the ``Appointment`` backing this booking.
    """
    with transaction.atomic():
        locked = TimeSlot.objects.select_for_update().get(pk=slot.pk)
        if locked.start_datetime - timezone.now() < datetime.timedelta(hours=1):
            raise SlotTooSoon(locked.pk)
        existing = Appointment.objects.filter(time_slot=locked).first()
        if existing is not None and existing.status != Appointment.Status.CANCELLED:
            raise SlotAlreadyBooked(locked.pk)
        if existing is None and locked.is_booked:
            raise SlotAlreadyBooked(locked.pk)

        if existing is None:
            appointment = Appointment.objects.create(
                patient=patient,
                time_slot=locked,
                status=Appointment.Status.ACTIVE,
            )
        else:
            appointment = existing
            appointment.patient = patient
            appointment.status = Appointment.Status.ACTIVE
            appointment.cancelled_at = None
            appointment.doctor_summary = ""
            appointment.created_at = timezone.now()
            appointment.save()

        locked.is_booked = True
        locked.save(update_fields=["is_booked"])

    _send_confirmation_email(appointment)
    return appointment


def _send_confirmation_email(appointment):
    slot = appointment.time_slot
    subject = "Appointment Confirmation — MyClinic"
    context = {"appointment": appointment, "slot": slot}
    body = render_to_string("emails/appointment_confirmation.txt", context)
    html_body = render_to_string("emails/appointment_confirmation.html", context)
    try:
        send_mail(
            subject,
            body,
            None,
            [appointment.patient.user.email],
            html_message=html_body,
            fail_silently=False,
        )
    except Exception:
        pass
