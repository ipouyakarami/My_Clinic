import datetime
from unittest.mock import patch

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Doctor, Patient, User
from appointments.models import Appointment, TimeSlot


STRONG_PASSWORD = "Correct-Horse-9x"


class AppointmentTestCase(TestCase):
    def _create_doctor(self, email="doc@example.com", password=STRONG_PASSWORD):
        user = User.objects.create_user(
            email=email,
            password=password,
            user_type=User.UserType.DOCTOR,
            first_name="علی",
            last_name="رضایی",
            is_active=True,
        )
        doctor = Doctor.objects.create(
            user=user,
            specialty="متخصص داخلی",
            description="پزشک عمومی",
            is_verified=True,
        )
        return user, doctor

    def _create_patient(self, email="pat@example.com", password=STRONG_PASSWORD):
        user = User.objects.create_user(
            email=email,
            password=password,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        return user, patient

    def _login(self, user):
        self.client.force_login(user)

    def _create_slot(self, doctor, date=None, start="09:00", end="09:30"):
        if date is None:
            date = (timezone.now() + datetime.timedelta(days=1)).date()
        return TimeSlot.objects.create(
            doctor=doctor,
            date=date,
            start_time=datetime.time.fromisoformat(start),
            end_time=datetime.time.fromisoformat(end),
        )


class WorkingHoursTests(AppointmentTestCase):
    def test_generates_full_30_min_slots(self):
        user, doctor = self._create_doctor()
        self._login(user)
        response = self.client.post(
            reverse("appointments:working_hours_create"),
            {"jalali_date": "1405/07/15", "start_time": "09:00", "end_time": "10:00"},
        )
        self.assertEqual(response.status_code, 302)
        slots = TimeSlot.objects.filter(doctor=doctor)
        self.assertEqual(slots.count(), 2)
        starts = list(slots.values_list("start_time", flat=True))
        self.assertIn(datetime.time(9, 0), starts)
        self.assertIn(datetime.time(9, 30), starts)

    def test_trailing_minutes_message(self):
        user, doctor = self._create_doctor()
        self._login(user)
        response = self.client.post(
            reverse("appointments:working_hours_create"),
            {"jalali_date": "1405/07/15", "start_time": "09:00", "end_time": "10:45"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "15 دقیقه")

    def test_expired_unbooked_slots_removed(self):
        user, doctor = self._create_doctor()
        past_slot = self._create_slot(
            doctor,
            date=(timezone.now() - datetime.timedelta(hours=2)).date(),
            start="09:00",
            end="09:30",
        )
        self._login(user)
        response = self.client.post(
            reverse("appointments:working_hours_create"),
            {"jalali_date": "1405/07/15", "start_time": "09:00", "end_time": "09:30"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "منقضی")
        self.assertFalse(TimeSlot.objects.filter(pk=past_slot.pk).exists())

    def test_dedupes_existing_slots(self):
        user, doctor = self._create_doctor()
        self._login(user)
        self.client.post(
            reverse("appointments:working_hours_create"),
            {"jalali_date": "1405/07/15", "start_time": "09:00", "end_time": "09:30"},
        )
        self.client.post(
            reverse("appointments:working_hours_create"),
            {"jalali_date": "1405/07/15", "start_time": "09:00", "end_time": "09:30"},
        )
        slots = TimeSlot.objects.filter(doctor=doctor)
        self.assertEqual(slots.count(), 1)

    def test_overlapping_range_rejected(self):
        user, doctor = self._create_doctor()
        target_date = timezone.now().date() + datetime.timedelta(days=10)
        import jdatetime
        jalali_date = jdatetime.date.fromgregorian(date=target_date).strftime("%Y/%m/%d")
        TimeSlot.objects.create(
            doctor=doctor,
            date=target_date,
            start_time=datetime.time(9, 0),
            end_time=datetime.time(9, 30),
        )
        self._login(user)
        response = self.client.post(
            reverse("appointments:working_hours_create"),
            {"jalali_date": jalali_date, "start_time": "09:15", "end_time": "09:45"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تعارض")

    def test_past_jalali_date_rejected(self):
        user, doctor = self._create_doctor()
        self._login(user)
        response = self.client.post(
            reverse("appointments:working_hours_create"),
            {"jalali_date": "1300/01/01", "start_time": "09:00", "end_time": "10:00"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "گذشته")

    def test_malformed_jalali_date_rejected(self):
        user, doctor = self._create_doctor()
        self._login(user)
        response = self.client.post(
            reverse("appointments:working_hours_create"),
            {"jalali_date": "not-a-date", "start_time": "09:00", "end_time": "10:00"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "نامعتبر")


class TimeSlotDeleteTests(AppointmentTestCase):
    def test_doctor_can_delete_open_slot(self):
        user, doctor = self._create_doctor()
        slot = self._create_slot(doctor)
        self._login(user)
        response = self.client.post(reverse("appointments:time_slot_delete", args=[slot.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TimeSlot.objects.filter(pk=slot.pk).exists())

    def test_doctor_cannot_delete_booked_slot(self):
        user, doctor = self._create_doctor()
        slot = self._create_slot(doctor)
        slot.is_booked = True
        slot.save()
        self._login(user)
        response = self.client.post(reverse("appointments:time_slot_delete", args=[slot.pk]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(TimeSlot.objects.filter(pk=slot.pk).exists())
        self.assertContains(response, "قبلاً رزرو شده")


class BookingTests(AppointmentTestCase):
    def test_patient_can_book_open_slot(self):
        doc_user, doctor = self._create_doctor()
        pat_user, patient = self._create_patient()
        slot = self._create_slot(doctor)
        self._login(pat_user)
        response = self.client.get(reverse("appointments:book_appointment", args=[doctor.pk]) + f"?slot={slot.pk}")
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("appointments:book_appointment", args=[doctor.pk]) + f"?slot={slot.pk}"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Appointment.objects.count(), 1)
        slot.refresh_from_db()
        self.assertTrue(slot.is_booked)

    def test_booking_race_condition(self):
        doc_user, doctor = self._create_doctor()
        pat_user, patient = self._create_patient()
        slot = self._create_slot(doctor)

        self._login(pat_user)
        url = reverse("appointments:book_appointment", args=[doctor.pk]) + f"?slot={slot.pk}"

        response1 = self.client.post(url)
        self.assertEqual(response1.status_code, 302)
        self.assertEqual(Appointment.objects.count(), 1)

        response2 = self.client.post(url, follow=True)
        self.assertEqual(response2.status_code, 200)
        self.assertContains(response2, "تازه رزرو شده")
        self.assertEqual(Appointment.objects.count(), 1)

        slot.refresh_from_db()
        self.assertTrue(slot.is_booked)


class CancelAppointmentTests(AppointmentTestCase):
    def test_cancel_blocked_within_12_hours(self):
        doc_user, doctor = self._create_doctor()
        pat_user, patient = self._create_patient()
        slot = self._create_slot(doctor, start="12:00", end="12:30")
        slot.date = timezone.now().date()
        slot.save()
        appointment = Appointment.objects.create(patient=patient, time_slot=slot)

        fake_now = datetime.datetime.combine(
            timezone.now().date(),
            datetime.time(10, 0),
        ).replace(tzinfo=timezone.get_current_timezone())
        with patch("django.utils.timezone.now", return_value=fake_now):
            self._login(pat_user)
            response = self.client.post(reverse("appointments:appointment_cancel", args=[appointment.pk]), follow=True)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "۱۲ ساعت قبل")

    def test_cancel_succeeds_after_12_hours(self):
        doc_user, doctor = self._create_doctor()
        pat_user, patient = self._create_patient()
        slot = self._create_slot(doctor, start="23:00", end="23:30")
        slot.date = timezone.now().date()
        slot.save()
        appointment = Appointment.objects.create(patient=patient, time_slot=slot)

        fake_now = datetime.datetime.combine(
            timezone.now().date(),
            datetime.time(10, 0),
        ).replace(tzinfo=timezone.get_current_timezone())
        with patch("django.utils.timezone.now", return_value=fake_now):
            self._login(pat_user)
            response = self.client.post(reverse("appointments:appointment_cancel", args=[appointment.pk]))
            self.assertRedirects(response, reverse("appointments:patient_appointment_list"))

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.CANCELLED)
        self.assertIsNotNone(appointment.cancelled_at)
        slot.refresh_from_db()
        self.assertFalse(slot.is_booked)


class DoctorAppointmentTests(AppointmentTestCase):
    def test_doctor_sees_today_appointments(self):
        doc_user, doctor = self._create_doctor()
        pat_user, patient = self._create_patient()
        today = timezone.now().date()
        slot = self._create_slot(doctor, date=today, start="10:00", end="10:30")
        Appointment.objects.create(patient=patient, time_slot=slot)

        self._login(doc_user)
        response = self.client.get(reverse("appointments:doctor_today_appointments"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مریم")

    def test_doctor_adds_visit_summary(self):
        doc_user, doctor = self._create_doctor()
        pat_user, patient = self._create_patient()
        slot = self._create_slot(doctor)
        appointment = Appointment.objects.create(patient=patient, time_slot=slot)

        self._login(doc_user)
        response = self.client.post(
            reverse("appointments:appointment_detail", args=[appointment.pk]),
            {"doctor_summary": "ویزیت عادی، توالع پایدار."},
        )
        self.assertRedirects(
            response, reverse("appointments:appointment_detail", args=[appointment.pk])
        )
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.COMPLETED)
        self.assertEqual(appointment.doctor_summary, "ویزیت عادی، توالع پایدار.")


class BookingConfirmationEmailTests(AppointmentTestCase):
    def test_booking_sends_confirmation_email(self):
        doc_user, doctor = self._create_doctor()
        pat_user, patient = self._create_patient()
        slot = self._create_slot(doctor)
        self._login(pat_user)
        response = self.client.post(
            reverse("appointments:book_appointment", args=[doctor.pk]) + f"?slot={slot.pk}"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("تایید", mail.outbox[0].subject)
        self.assertIn(pat_user.email, mail.outbox[0].to)
