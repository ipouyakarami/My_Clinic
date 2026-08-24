import datetime
import json
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Doctor, Patient, User
from appointments.models import Appointment, TimeSlot

from .forms import MedicationStep1Form, MedicationStep3Form
from .models import EmailFailureLog, Medication, MedicationIntake, MedicationSchedule
from . import tasks


STRONG_PASSWORD = "Correct-Horse-9x"


class MedicationModelTests(TestCase):
    def test_create_medication_and_schedule(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient,
            name="آسپرین",
            unit="قرص",
            dosage="۱ قرص",
            current_inventory=10,
            refill_reminder_threshold=5,
        )
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00", "20:00"],
        )
        self.assertEqual(medication.name, "آسپرین")
        self.assertEqual(schedule.frequency_type, "daily")
        self.assertEqual(len(schedule.medication_times), 2)


class ScheduleGenerationTests(TestCase):
    def _create_patient(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        return Patient.objects.create(user=user, phone_number="09121234567")

    def _create_schedule(self, patient, frequency_type, **kwargs):
        medication = Medication.objects.create(
            patient=patient,
            name="تست",
            unit="قرص",
            dosage="۱ قرص",
        )
        defaults = {
            "medication": medication,
            "frequency_type": frequency_type,
        }
        defaults.update(kwargs)
        return MedicationSchedule.objects.create(**defaults)

    def test_daily_generates_intakes(self):
        patient = self._create_patient()
        schedule = self._create_schedule(
            patient,
            MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00", "20:00"],
            start_date=timezone.now().date(),
            end_date=timezone.now().date(),
        )
        from .views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        intakes = MedicationIntake.objects.filter(medication=schedule.medication)
        self.assertEqual(intakes.count(), 2)

    def test_specific_days_generates_intakes(self):
        patient = self._create_patient()
        schedule = self._create_schedule(
            patient,
            MedicationSchedule.FrequencyType.SPECIFIC_DAYS,
            medication_times=["08:00"],
            specific_days=["mon", "wed", "fri"],
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + datetime.timedelta(days=7),
        )
        from .views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        intakes = MedicationIntake.objects.filter(medication=schedule.medication)
        self.assertGreater(intakes.count(), 0)
        self.assertLessEqual(intakes.count(), 22)

    def test_every_n_days_generates_intakes(self):
        patient = self._create_patient()
        schedule = self._create_schedule(
            patient,
            MedicationSchedule.FrequencyType.EVERY_N_DAYS,
            medication_times=["08:00"],
            n_days_interval=2,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + datetime.timedelta(days=7),
        )
        from .views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        intakes = MedicationIntake.objects.filter(medication=schedule.medication)
        self.assertGreater(intakes.count(), 0)

    def test_every_x_hours_generates_intakes(self):
        patient = self._create_patient()
        tz = timezone.get_current_timezone()
        anchor = timezone.now().astimezone(tz)
        schedule = self._create_schedule(
            patient,
            MedicationSchedule.FrequencyType.EVERY_X_HOURS,
            x_hours_interval=4,
            anchor_datetime=anchor,
        )
        from .views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        intakes = MedicationIntake.objects.filter(medication=schedule.medication)
        self.assertGreater(intakes.count(), 0)


class MissedIntakeTransitionTests(TestCase):
    def test_pending_intakes_become_missed_after_30_minutes(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient,
            name="آسپرین",
            unit="قرص",
            dosage="۱ قرص",
        )
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
            start_date=timezone.now().date(),
            end_date=timezone.now().date(),
        )
        from .views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        intake = MedicationIntake.objects.filter(medication=medication).first()
        self.assertEqual(intake.status, MedicationIntake.Status.PENDING)

        fake_now = intake.scheduled_time + datetime.timedelta(minutes=31)
        with patch("django.utils.timezone.now", return_value=fake_now):
            from .tasks import transition_missed_intakes
            transition_missed_intakes()

        intake.refresh_from_db()
        self.assertEqual(intake.status, MedicationIntake.Status.MISSED)

    def test_taken_intake_not_overwritten_by_missed_task(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient,
            name="آسپرین",
            unit="قرص",
            dosage="۱ قرص",
        )
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
            start_date=timezone.now().date(),
            end_date=timezone.now().date(),
        )
        from .views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        intake = MedicationIntake.objects.filter(medication=medication).first()
        intake.status = MedicationIntake.Status.TAKEN
        intake.recorded_at = timezone.now()
        intake.save(update_fields=["status", "recorded_at"])

        fake_now = intake.scheduled_time + datetime.timedelta(minutes=31)
        with patch("django.utils.timezone.now", return_value=fake_now):
            from .tasks import transition_missed_intakes
            transition_missed_intakes()

        intake.refresh_from_db()
        self.assertEqual(intake.status, MedicationIntake.Status.TAKEN)


class BulkMarkTakenTests(TestCase):
    def test_bulk_mark_only_pending_intakes_for_patient(self):
        pat1 = User.objects.create_user(
            email="p1@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        pat2 = User.objects.create_user(
            email="p2@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="علی",
            last_name="رضایی",
            is_active=True,
        )
        patient1 = Patient.objects.create(user=pat1, phone_number="09121234567")
        patient2 = Patient.objects.create(user=pat2, phone_number="09129876543")

        med1 = Medication.objects.create(patient=patient1, name="دارو۱", unit="قرص")
        med2 = Medication.objects.create(patient=patient2, name="دارو۲", unit="قرص")

        now = timezone.now()
        scheduled = now.replace(minute=0, second=0, microsecond=0) + timezone.timedelta(hours=1)
        intake1 = MedicationIntake.objects.create(
            medication=med1, scheduled_time=scheduled, status=MedicationIntake.Status.PENDING
        )
        intake2 = MedicationIntake.objects.create(
            medication=med2, scheduled_time=scheduled, status=MedicationIntake.Status.PENDING
        )

        self.client.force_login(pat1)
        response = self.client.post(
            reverse("medications:intake_mark_batch"),
            {"scheduled_time": scheduled.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 1)

        intake1.refresh_from_db()
        intake2.refresh_from_db()
        self.assertEqual(intake1.status, MedicationIntake.Status.TAKEN)
        self.assertEqual(intake2.status, MedicationIntake.Status.PENDING)


class InventoryAndRefillTests(TestCase):
    def test_inventory_decrements_on_taken(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient,
            name="آسپرین",
            unit="قرص",
            current_inventory=10,
            refill_reminder_threshold=5,
            refill_reminder_sent=False,
        )
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
            start_date=timezone.now().date(),
            end_date=timezone.now().date(),
        )
        from .views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        intake = MedicationIntake.objects.filter(medication=medication).first()

        from .tasks import handle_intake_taken
        handle_intake_taken(intake.pk)

        medication.refresh_from_db()
        self.assertEqual(medication.current_inventory, 9)
        self.assertFalse(medication.refill_reminder_sent)

    def test_refill_reminder_sends_once_at_threshold(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient,
            name="آسپرین",
            unit="قرص",
            current_inventory=6,
            refill_reminder_threshold=5,
            refill_reminder_sent=False,
        )
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00", "14:00"],
            start_date=timezone.now().date(),
            end_date=timezone.now().date(),
        )
        from .views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        intakes = list(MedicationIntake.objects.filter(medication=medication))
        self.assertEqual(len(intakes), 2)

        from .tasks import handle_intake_taken
        for intake in intakes:
            handle_intake_taken(intake.pk)

        medication.refresh_from_db()
        self.assertEqual(medication.current_inventory, 4)
        self.assertTrue(medication.refill_reminder_sent)
        self.assertEqual(EmailFailureLog.objects.count(), 0)

    def test_refill_reminder_resets_after_inventory_edited(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient,
            name="آسپرین",
            unit="قرص",
            current_inventory=4,
            refill_reminder_threshold=5,
            refill_reminder_sent=True,
        )
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
            start_date=timezone.now().date(),
            end_date=timezone.now().date(),
        )
        from .views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        intakes = list(MedicationIntake.objects.filter(medication=medication))

        medication.current_inventory = 10
        medication.refill_reminder_sent = False
        medication.save(update_fields=["current_inventory", "refill_reminder_sent"])

        from .tasks import handle_intake_taken
        handle_intake_taken(intakes[0].pk)

        medication.refresh_from_db()
        self.assertFalse(medication.refill_reminder_sent)
        self.assertEqual(medication.current_inventory, 9)


class EmailFailureHandlingTests(TestCase):
    def test_reminder_email_failure_is_logged(self):
        from django.core.mail import EmailMessage
        from unittest.mock import patch

        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient,
            name="آسپرین",
            unit="قرص",
        )
        intake = MedicationIntake.objects.create(
            medication=medication,
            scheduled_time=timezone.now() - timezone.timedelta(minutes=5),
            status=MedicationIntake.Status.PENDING,
        )

        with patch.object(tasks, "send_mail", side_effect=Exception("SMTP error")):
            tasks.send_reminder_emails()

        intake.refresh_from_db()
        self.assertFalse(intake.reminder_sent)
        self.assertTrue(EmailFailureLog.objects.exists())
        log = EmailFailureLog.objects.first()
        self.assertIn("SMTP error", log.error_message)


class DoctorAccessBoundaryTests(TestCase):
    def test_doctor_can_access_patient_meds_via_shared_appointment(self):
        doc_user = User.objects.create_user(
            email="doc@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.DOCTOR,
            first_name="علی",
            last_name="رضایی",
            is_active=True,
        )
        doctor = Doctor.objects.create(
            user=doc_user,
            specialty="متخصص داخلی",
            description="پزشک عمومی",
            is_verified=True,
        )
        pat_user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=pat_user, phone_number="09121234567")
        slot = TimeSlot.objects.create(
            doctor=doctor,
            date=timezone.now().date() + timezone.timedelta(days=1),
            start_time=datetime.time(9, 0),
            end_time=datetime.time(9, 30),
        )
        appointment = Appointment.objects.create(patient=patient, time_slot=slot)
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص")

        self.client.force_login(doc_user)
        response = self.client.get(
            reverse("medications:doctor_medication_list", args=[appointment.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "آسپرین")

    def test_doctor_cannot_access_unrelated_patient_meds(self):
        doc_user = User.objects.create_user(
            email="doc@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.DOCTOR,
            first_name="علی",
            last_name="رضایی",
            is_active=True,
        )
        doctor = Doctor.objects.create(
            user=doc_user,
            specialty="متخصص داخلی",
            description="پزشک عمومی",
            is_verified=True,
        )
        other_user = User.objects.create_user(
            email="other@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="سارا",
            last_name="محمدی",
            is_active=True,
        )
        other_patient = Patient.objects.create(user=other_user, phone_number="09121234567")
        other_med = Medication.objects.create(patient=other_patient, name="پنادول", unit="قرص")

        pat_user = User.objects.create_user(
            email="pat2@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=pat_user, phone_number="09121234567")
        slot = TimeSlot.objects.create(
            doctor=doctor,
            date=timezone.now().date() + timezone.timedelta(days=1),
            start_time=datetime.time(9, 0),
            end_time=datetime.time(9, 30),
        )
        appointment = Appointment.objects.create(patient=patient, time_slot=slot)

        self.client.force_login(doc_user)
        response = self.client.get(
            reverse("medications:doctor_medication_edit", args=[appointment.pk, other_med.pk])
        )
        self.assertEqual(response.status_code, 404)

    def _create_doctor_appointment(self, doc_email="doc@example.com", pat_email="pat@example.com"):
        doc_user = User.objects.create_user(
            email=doc_email, password=STRONG_PASSWORD,
            user_type=User.UserType.DOCTOR, first_name="علی", last_name="رضایی", is_active=True,
        )
        Doctor.objects.create(user=doc_user, specialty="متخصص داخلی", description="پزشک عمومی", is_verified=True)
        pat_user = User.objects.create_user(
            email=pat_email, password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=pat_user, phone_number="09121234567")
        slot = TimeSlot.objects.create(
            doctor=doc_user.doctor,
            date=timezone.now().date() + timezone.timedelta(days=1),
            start_time=datetime.time(9, 0), end_time=datetime.time(9, 30),
        )
        appointment = Appointment.objects.create(patient=patient, time_slot=slot)
        return doc_user, patient, appointment

    def test_doctor_can_add_medication_to_patient(self):
        doc_user, patient, appointment = self._create_doctor_appointment()
        self.client.force_login(doc_user)

        session = self.client.session
        session["medication_wizard_%d" % doc_user.id] = {
            "name": "آسپرین", "unit": "قرص", "frequency_type": "daily",
            "medication_times": ["08:00"], "current_inventory": 30,
            "refill_reminder_threshold": 5, "start_date": "", "end_date": "",
        }
        session.save()

        response = self.client.get(
            reverse("medications:doctor_medication_add", args=[appointment.pk]) + "?step=6"
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse("medications:doctor_medication_add", args=[appointment.pk]),
            {"step": "6", "frequency_type": "daily"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Medication.objects.filter(patient=patient, name="آسپرین", is_active=True).exists()
        )

    def test_doctor_can_edit_patient_medication(self):
        doc_user, patient, appointment = self._create_doctor_appointment()
        self.client.force_login(doc_user)
        med = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص", dosage="۱ قرص")

        response = self.client.get(
            reverse("medications:doctor_medication_edit", args=[appointment.pk, med.pk]) + "?step=1"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "آسپرین")

        session = self.client.session
        session["medication_wizard_%d" % doc_user.id] = {
            "medication_pk": med.pk, "name": "ایبوپروفن", "unit": "قرص",
            "dosage": "۲ قرص", "frequency_type": "daily",
            "medication_times": ["08:00"], "current_inventory": 20,
            "refill_reminder_threshold": 5, "start_date": "", "end_date": "",
        }
        session.save()

        response = self.client.post(
            reverse("medications:doctor_medication_edit", args=[appointment.pk, med.pk]),
            {"step": "6", "frequency_type": "daily"},
        )
        self.assertEqual(response.status_code, 302)
        med.refresh_from_db()
        self.assertEqual(med.name, "ایبوپروفن")
        self.assertEqual(med.dosage, "۲ قرص")

    def test_doctor_redirected_from_patient_medication_urls(self):
        doc_user, _, _ = self._create_doctor_appointment()
        self.client.force_login(doc_user)
        response = self.client.get(reverse("medications:medication_add") + "?step=2")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("appointments:doctor_appointment_list"), response.url)

    def test_doctor_next_step_stays_on_doctor_url(self):
        """Clicking Next in the wizard must redirect to the doctor URL, not patient URL."""
        doc_user, patient, appointment = self._create_doctor_appointment()
        self.client.force_login(doc_user)

        session = self.client.session
        session["medication_wizard_%d" % doc_user.id] = {
            "name": "آسپرین", "unit": "قرص", "frequency_type": "daily",
            "medication_times": ["08:00"], "current_inventory": 30,
            "refill_reminder_threshold": 5, "start_date": "", "end_date": "",
        }
        session.save()

        response = self.client.post(
            reverse("medications:doctor_medication_add", args=[appointment.pk]),
            {"step": "1", "name": "آسپرین", "unit": "قرص"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("medications:doctor_medication_add", args=[appointment.pk]),
            response.url,
        )

    def test_doctor_save_redirects_to_doctor_list(self):
        """Saving a medication must redirect to the doctor list, not patient list."""
        doc_user, patient, appointment = self._create_doctor_appointment()
        self.client.force_login(doc_user)

        session = self.client.session
        session["medication_wizard_%d" % doc_user.id] = {
            "name": "آسپرین", "unit": "قرص", "frequency_type": "daily",
            "medication_times": ["08:00"], "current_inventory": 30,
            "refill_reminder_threshold": 5, "start_date": "", "end_date": "",
        }
        session.save()

        response = self.client.post(
            reverse("medications:doctor_medication_add", args=[appointment.pk]),
            {"step": "6", "frequency_type": "daily"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("medications:doctor_medication_list", args=[appointment.pk]),
        )


class MedicationViewTests(TestCase):
    def test_patient_can_add_medication(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        response = self.client.post(
            reverse("medications:medication_add"),
            {
                "step": "1",
                "name": "آسپرین",
                "unit": "قرص",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("medications", response.url)

        response = self.client.post(
            reverse("medications:medication_add"),
            {
                "step": "2",
                "frequency_type": "daily",
            },
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            reverse("medications:medication_add"),
            {
                "step": "3",
                "medication_times": "08:00\n20:00",
                "frequency_type": "daily",
            },
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            reverse("medications:medication_add"),
            {
                "step": "4",
                "dosage": "۱ قرص",
            },
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            reverse("medications:medication_add"),
            {
                "step": "5",
                "current_inventory": 10,
                "refill_reminder_threshold": 5,
            },
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            reverse("medications:medication_add"),
            {
                "step": "6",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("medications", response.url)

        self.assertEqual(Medication.objects.count(), 1)
        self.assertEqual(MedicationSchedule.objects.count(), 1)
        self.assertGreater(MedicationIntake.objects.count(), 0)

    def test_patient_can_edit_medication(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient,
            name="آسپرین",
            unit="قرص",
            dosage="۱ قرص",
            current_inventory=10,
        )
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
            start_date=timezone.now().date(),
            end_date=timezone.now().date(),
        )
        from .views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)

        self.client.force_login(user)
        response = self.client.get(reverse("medications:medication_edit", args=[medication.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Medication")

    def test_step3_daily_shows_correct_fields(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})

        response = self.client.get(reverse("medications:medication_add") + "?step=3")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "daily-fields")
        self.assertContains(response, 'id="frequency-type-value"')
        self.assertContains(response, 'value="daily"')
        content = response.content.decode("utf-8")
        self.assertIn('id="frequency-type-value" value="daily"', content)

    def test_step3_field_groups_toggle_based_on_frequency(self):
        """Each frequency_type must show its own field group and hide others."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})

        test_cases = [
            ("daily", "daily-fields"),
            ("specific_days", "specific-days-fields"),
            ("every_n_days", "every-n-days-fields"),
            ("every_x_hours", "every-x-hours-fields"),
        ]

        for freq, expected_group_id in test_cases:
            self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": freq})
            response = self.client.get(reverse("medications:medication_add") + "?step=3")
            self.assertEqual(response.status_code, 200)
            content = response.content.decode("utf-8")
            self.assertIn(f'id="frequency-type-value" value="{freq}"', content)
            self.assertIn(expected_group_id, content)

    def test_step3_edit_flow_toggles_when_frequency_changes(self):
        """Edit flow must also switch correctly if user changes frequency_type."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        medication = Medication.objects.create(
            patient=patient, name="آسپرین", unit="قرص", dosage="۱ قرص",
            current_inventory=10,
        )
        MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
            start_date=timezone.now().date(),
            end_date=timezone.now().date(),
        )

        response = self.client.get(reverse("medications:medication_edit", args=[medication.pk]) + "?step=3")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('id="frequency-type-value" value="daily"', content)
        self.assertIn("daily-fields", content)

    def test_step3_specific_days_shows_correct_fields(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "specific_days"})

        response = self.client.get(reverse("medications:medication_add") + "?step=3")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "specific-days-fields")
        self.assertContains(response, 'name="specific_days"')
        self.assertContains(response, 'value="sat"')
        self.assertContains(response, 'value="mon"')
        self.assertContains(response, 'value="fri"')

    def test_step3_every_n_days_shows_correct_fields(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "every_n_days"})

        response = self.client.get(reverse("medications:medication_add") + "?step=3")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "every-n-days-fields")
        self.assertContains(response, 'name="n_days_interval"')

    def test_step3_every_x_hours_shows_correct_fields(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "every_x_hours"})

        response = self.client.get(reverse("medications:medication_add") + "?step=3")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "every-x-hours-fields")
        self.assertContains(response, 'name="x_hours_interval"')
        self.assertContains(response, 'name="anchor_date"')
        self.assertContains(response, 'name="anchor_time"')

    def test_step3_specific_days_allows_multiple_selection(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "specific_days"})

        response = self.client.post(
            reverse("medications:medication_add"),
            {
                "step": "3",
                "frequency_type": "specific_days",
                "specific_days": ["sat", "mon", "wed"],
                "medication_times_specific": "08:00",
                "start_date_specific": "2026/08/25",
            },
        )
        self.assertEqual(response.status_code, 302)

    def test_step3_only_relevant_fields_validated(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})

        response = self.client.post(
            reverse("medications:medication_add"),
            {
                "step": "3",
                "frequency_type": "daily",
                "medication_times": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at least one medication time")

    def test_edit_medication_prefills_all_frequency_types(self):
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        medication = Medication.objects.create(
            patient=patient, name="آسپرین", unit="قرص", dosage="۱ قرص",
            current_inventory=10,
        )
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.EVERY_X_HOURS,
            x_hours_interval=4,
            anchor_datetime=timezone.now(),
            start_date=timezone.now().date(),
            end_date=timezone.now().date(),
        )

        response = self.client.get(reverse("medications:medication_edit", args=[medication.pk]) + "?step=3")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "every-x-hours-fields")
        self.assertContains(response, 'value="every_x_hours"')
        self.assertContains(response, 'name="x_hours_interval"')

    def test_every_x_hours_step3_no_typeerror(self):
        """Regression test: every_x_hours Step 3 must not raise TypeError during session serialization."""
        import json
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "every_x_hours"})

        response = self.client.post(
            reverse("medications:medication_add"),
            {
                "step": "3",
                "frequency_type": "every_x_hours",
                "anchor_date": "2026/08/25",
                "anchor_time": "08:00",
                "x_hours_interval": 4,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("step=4", response.url)

        session = self.client.session
        wizard_data = session.get(f"medication_wizard_{user.id}", {})
        self.assertEqual(wizard_data.get("anchor_date"), "2026/08/25")
        self.assertEqual(wizard_data.get("anchor_time"), "08:00")
        self.assertEqual(wizard_data.get("x_hours_interval"), 4)
        json.dumps(wizard_data)

        response = self.client.get(reverse("medications:medication_add") + "?step=4")
        self.assertEqual(response.status_code, 200)

    def test_step6_review_shows_general_info(self):
        """Step 6 must display medication name, unit, dosage, inventory."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "daily", "medication_times": "08:00\n20:00", "start_date": "2026/08/25"})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "۱ قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})

        response = self.client.get(reverse("medications:medication_add") + "?step=6")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "آسپرین")
        self.assertContains(response, "قرص")
        self.assertContains(response, "۱ قرص")
        self.assertContains(response, "10")
        self.assertContains(response, "5")
        self.assertContains(response, "08:00")
        self.assertContains(response, "20:00")
        self.assertContains(response, "2026/08/25")

    def test_step6_review_for_every_x_hours(self):
        """Step 6 must show anchor_datetime and x_hours_interval for every_x_hours."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "every_x_hours"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "every_x_hours", "anchor_date": "2026/08/25", "anchor_time": "08:00", "x_hours_interval": 4})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "۱ قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})

        response = self.client.get(reverse("medications:medication_add") + "?step=6")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "آسپرین")
        self.assertContains(response, "۱ قرص")
        self.assertContains(response, "2026/08/25")
        self.assertContains(response, "08:00")
        self.assertContains(response, "Every 4 hour(s)")
        self.assertNotContains(response, "Specific Days")

    def test_step6_review_for_specific_days(self):
        """Step 6 must show selected weekdays for specific_days."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "specific_days"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "specific_days", "specific_days": ["sat", "mon"], "medication_times_specific": "08:00", "start_date_specific": "2026/08/25"})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "۱ قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})

        response = self.client.get(reverse("medications:medication_add") + "?step=6")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "آسپرین")
        self.assertContains(response, "Saturday")
        self.assertContains(response, "Monday")
        self.assertContains(response, "2026/08/25")

    def test_gregorian_date_input_stored_directly(self):
        """Gregorian date input is stored directly in the database."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "daily", "medication_times": "08:00", "start_date": "2026/08/25"})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "۱ قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})
        response = self.client.post(reverse("medications:medication_add"), {"step": "6"})
        self.assertEqual(response.status_code, 302)

        medication = Medication.objects.get(name="آسپرین")
        schedule = MedicationSchedule.objects.get(medication=medication)
        import datetime as dt_module
        self.assertEqual(schedule.start_date, dt_module.date(2026, 8, 25))

    def test_edit_medication_shows_dates(self):
        """Edit Medication must display stored Gregorian dates."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        medication = Medication.objects.create(
            patient=patient, name="آسپرین", unit="قرص", dosage="۱ قرص",
            current_inventory=10,
        )
        import datetime as dt_module
        gregorian_date = dt_module.date(2026, 8, 25)
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
            start_date=gregorian_date,
            end_date=gregorian_date,
        )

        response = self.client.get(reverse("medications:medication_edit", args=[medication.pk]) + "?step=3")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2026/08/25")


class MedicationCalendarTests(TestCase):
    def _create_patient(self, email="pat@example.com"):
        user = User.objects.create_user(
            email=email,
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        return Patient.objects.create(user=user, phone_number="09121234567"), user

    def test_calendar_shows_intakes_for_selected_date(self):
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص", dosage="۱ قرص")
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
        )
        from medications.views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        date_str = timezone.now().date().strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "آسپرین")

    def test_calendar_excludes_other_patients_intakes(self):
        patient1, user1 = self._create_patient("p1@example.com")
        patient2, user2 = self._create_patient("p2@example.com")
        self.client.force_login(user1)
        med1 = Medication.objects.create(patient=patient1, name="دارو۱", unit="قرص")
        med2 = Medication.objects.create(patient=patient2, name="دارو۲", unit="قرص")
        schedule1 = MedicationSchedule.objects.create(medication=med1, frequency_type=MedicationSchedule.FrequencyType.DAILY, medication_times=["08:00"])
        schedule2 = MedicationSchedule.objects.create(medication=med2, frequency_type=MedicationSchedule.FrequencyType.DAILY, medication_times=["08:00"])
        from medications.views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule1)
        _generate_intakes_for_schedule(schedule2)
        date_str = timezone.now().date().strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "دارو۱")
        self.assertNotContains(response, "دارو۲")

    def test_calendar_empty_date_shows_empty_state(self):
        patient, user = self._create_patient()
        self.client.force_login(user)
        import datetime as dt_module
        future_date = timezone.now().date() + dt_module.timedelta(days=30)
        date_str = future_date.strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No medications scheduled for this date")

    def test_calendar_multiple_intakes_same_date(self):
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص", dosage="۱ قرص")
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00", "14:00", "20:00"],
        )
        from medications.views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        date_str = timezone.now().date().strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "08:00")
        self.assertContains(response, "14:00")
        self.assertContains(response, "20:00")

    def test_calendar_gregorian_date_queries(self):
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص")
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
        )
        from medications.views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        date_str = timezone.now().date().strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        intake = MedicationIntake.objects.filter(
            medication=medication,
            scheduled_time__date=timezone.now().date(),
        ).first()
        self.assertIsNotNone(intake)

    def test_calendar_uses_gregorian_config(self):
        """The calendar must use the Gregorian calendar picker."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        response = self.client.get(reverse("medications:medication_calendar"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "calendar-picker")

    def test_calendar_preserves_date_in_ui(self):
        """The selected date must remain in the UI, not be converted."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص")
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
        )
        from medications.views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        date_str = timezone.now().date().strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, date_str)

    def test_calendar_groups_intakes_by_time(self):
        """Medications at the same time should be grouped together."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        med1 = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص", dosage="۱ قرص")
        med2 = Medication.objects.create(patient=patient, name="یبوپروفن", unit="قرص", dosage="۲ قرص")
        schedule1 = MedicationSchedule.objects.create(
            medication=med1,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
        )
        schedule2 = MedicationSchedule.objects.create(
            medication=med2,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00", "20:00"],
        )
        from medications.views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule1)
        _generate_intakes_for_schedule(schedule2)
        date_str = timezone.now().date().strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "آسپرین")
        self.assertContains(response, "یبوپروفن")
        self.assertContains(response, "08:00")
        self.assertContains(response, "20:00")

    def test_calendar_shows_all_statuses(self):
        """All statuses (pending, taken, skipped, missed) should be displayed."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص", dosage="۱ قرص")
        tz = timezone.get_current_timezone()
        today = timezone.now().date()
        from django.utils import timezone as tz_utils
        for status, hh, mm in [
            ("pending", 8, 0),
            ("taken", 12, 0),
            ("skipped", 16, 0),
            ("missed", 20, 0),
        ]:
            dt = tz_utils.make_aware(
                datetime.datetime.combine(today, datetime.time(hh, mm)), tz
            )
            MedicationIntake.objects.create(
                medication=medication,
                scheduled_time=dt,
                status=status,
            )
        date_str = today.strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending")
        self.assertContains(response, "Taken")
        self.assertContains(response, "Skipped")
        self.assertContains(response, "Missed")

    def test_calendar_midnight_boundary(self):
        """Intakes around midnight should be handled correctly with Asia/Tehran timezone."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص", dosage="۱ قرص")
        tz = timezone.get_current_timezone()
        today = timezone.now().date()
        from django.utils import timezone as tz_utils
        midnight = tz_utils.make_aware(
            datetime.datetime.combine(today, datetime.time(0, 0)), tz
        )
        almost_midnight = tz_utils.make_aware(
            datetime.datetime.combine(today, datetime.time(23, 59)), tz
        )
        MedicationIntake.objects.create(
            medication=medication,
            scheduled_time=midnight,
            status="pending",
        )
        MedicationIntake.objects.create(
            medication=medication,
            scheduled_time=almost_midnight,
            status="pending",
        )
        date_str = today.strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "00:00")
        self.assertContains(response, "23:59")

    def test_calendar_invalid_date_shows_no_error(self):
        """Invalid date strings should not cause errors."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        response = self.client.get(reverse("medications:medication_calendar") + "?date=invalid")
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("medications:medication_calendar") + "?date=9999/99/99")
        self.assertEqual(response.status_code, 200)

    def test_calendar_context_has_date_display(self):
        """The view should provide a formatted date for display."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص", dosage="۱ قرص")
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
        )
        from medications.views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        date_str = timezone.now().date().strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("date_display", response.context)
        self.assertIn("intakes_by_time", response.context)
        self.assertIsNotNone(response.context["date_display"])
        self.assertIn("Medications for", response.content.decode("utf-8"))

    def test_calendar_intakes_sorted_chronologically(self):
        """Intakes should be sorted chronologically by scheduled_time."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص", dosage="۱ قرص")
        tz = timezone.get_current_timezone()
        today = timezone.now().date()
        from django.utils import timezone as tz_utils
        for hh, mm in [(20, 0), (8, 0), (14, 0)]:
            dt = tz_utils.make_aware(
                datetime.datetime.combine(today, datetime.time(hh, mm)), tz
            )
            MedicationIntake.objects.create(
                medication=medication,
                scheduled_time=dt,
                status="pending",
            )
        date_str = today.strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        pos_08 = content.find("08:00")
        pos_14 = content.find("14:00")
        pos_20 = content.find("20:00")
        self.assertLess(pos_08, pos_14)
        self.assertLess(pos_14, pos_20)

    def test_calendar_no_date_param_shows_empty(self):
        """Without a date param, the calendar should show no medications."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        response = self.client.get(reverse("medications:medication_calendar"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Medications for")

    def test_calendar_patient_filter_is_correct(self):
        """Verify the query correctly filters by the logged-in patient."""
        patient1, user1 = self._create_patient("p1@example.com")
        patient2, user2 = self._create_patient("p2@example.com")
        self.client.force_login(user1)
        med1 = Medication.objects.create(patient=patient1, name="داروی-یک", unit="قرص")
        med2 = Medication.objects.create(patient=patient2, name="داروی-دو", unit="قرص")
        tz = timezone.get_current_timezone()
        today = timezone.now().date()
        from django.utils import timezone as tz_utils
        for med in [med1, med2]:
            dt = tz_utils.make_aware(
                datetime.datetime.combine(today, datetime.time(8, 0)), tz
            )
            MedicationIntake.objects.create(
                medication=med,
                scheduled_time=dt,
                status="pending",
            )
        date_str = today.strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        intakes = response.context["intakes"]
        self.assertEqual(intakes.count(), 1)
        self.assertEqual(intakes.first().medication.patient, patient1)

    def test_calendar_different_months_return_correct_month(self):
        """Selecting different months should return medications for exactly that month."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص", dosage="۱ قرص")
        tz = timezone.get_current_timezone()
        from django.utils import timezone as tz_utils
        import datetime as dt_module

        test_dates = [
            dt_module.date(2026, 1, 15),
            dt_module.date(2026, 3, 1),
            dt_module.date(2026, 6, 10),
            dt_module.date(2026, 9, 5),
            dt_module.date(2026, 12, 20),
        ]

        for test_date in test_dates:
            dt = tz_utils.make_aware(
                datetime.datetime.combine(test_date, datetime.time(8, 0)), tz
            )
            MedicationIntake.objects.create(
                medication=medication,
                scheduled_time=dt,
                status="pending",
            )

        for test_date in test_dates:
            date_str = test_date.strftime("%Y/%m/%d")
            response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
            self.assertEqual(response.status_code, 200)
            intakes = response.context["intakes"]
            self.assertEqual(intakes.count(), 1, f"Expected 1 intake for {date_str}, got {intakes.count()}")
            scheduled = intakes.first().scheduled_time
            self.assertEqual(scheduled.month, test_date.month, f"Month mismatch for {date_str}")
            self.assertEqual(scheduled.year, test_date.year, f"Year mismatch for {date_str}")

    def test_calendar_month_boundary_first_month(self):
        """January should be handled correctly."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="دارو", unit="قرص")
        tz = timezone.get_current_timezone()
        from django.utils import timezone as tz_utils
        import datetime as dt_module

        test_date = dt_module.date(2026, 1, 15)
        dt = tz_utils.make_aware(
            datetime.datetime.combine(test_date, datetime.time(10, 0)), tz
        )
        MedicationIntake.objects.create(medication=medication, scheduled_time=dt, status="pending")

        date_str = test_date.strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "دارو")
        intakes = response.context["intakes"]
        self.assertEqual(intakes.count(), 1)

    def test_calendar_month_boundary_last_month(self):
        """December should be handled correctly."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="دارو", unit="قرص")
        tz = timezone.get_current_timezone()
        from django.utils import timezone as tz_utils
        import datetime as dt_module

        test_date = dt_module.date(2026, 12, 5)
        dt = tz_utils.make_aware(
            datetime.datetime.combine(test_date, datetime.time(10, 0)), tz
        )
        MedicationIntake.objects.create(medication=medication, scheduled_time=dt, status="pending")

        date_str = test_date.strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "دارو")
        intakes = response.context["intakes"]
        self.assertEqual(intakes.count(), 1)

    def test_calendar_year_boundary(self):
        """Year boundary should be handled correctly."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="دارو", unit="قرص")
        tz = timezone.get_current_timezone()
        from django.utils import timezone as tz_utils
        import datetime as dt_module

        prev_year = dt_module.date(2025, 12, 20)
        next_year = dt_module.date(2026, 1, 10)

        for test_date in [prev_year, next_year]:
            dt = tz_utils.make_aware(
                datetime.datetime.combine(test_date, datetime.time(10, 0)), tz
            )
            MedicationIntake.objects.create(medication=medication, scheduled_time=dt, status="pending")

        for test_date in [prev_year, next_year]:
            date_str = test_date.strftime("%Y/%m/%d")
            response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
            self.assertEqual(response.status_code, 200)
            intakes = response.context["intakes"]
            self.assertEqual(intakes.count(), 1, f"Expected 1 intake for {date_str}, got {intakes.count()}")

    def test_calendar_date_display_matches_selected_month(self):
        """The date_display should match the selected month."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        import datetime as dt_module

        test_cases = [
            (2026, 1, "January"),
            (2026, 2, "February"),
            (2026, 3, "March"),
            (2026, 4, "April"),
            (2026, 5, "May"),
            (2026, 6, "June"),
            (2026, 7, "July"),
            (2026, 8, "August"),
            (2026, 9, "September"),
            (2026, 10, "October"),
            (2026, 11, "November"),
            (2026, 12, "December"),
        ]

        for year, month, expected_name in test_cases:
            test_date = dt_module.date(year, month, 1)
            date_str = test_date.strftime("%Y/%m/%d")
            response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
            self.assertEqual(response.status_code, 200)
            date_display = response.context["date_display"]
            self.assertIsNotNone(date_display, f"date_display is None for {date_str}")
            self.assertIn(expected_name, date_display,
                          f"Expected '{expected_name}' in display for {date_str}, got '{date_display}'")

    def test_calendar_passes_explicit_year_month_day_to_template(self):
        """The view must pass year/month/day as integers so JS can build a
        timezone-safe local Date via new Date(y, m-1, d). Without this, a
        date-only string parsed as UTC midnight can shift by a day in
        timezones behind UTC."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص")
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
        )
        from medications.views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)
        date_str = "2026/08/25"
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["year"], 2026)
        self.assertEqual(response.context["month"], 8)
        self.assertEqual(response.context["day"], 25)
        content = response.content.decode("utf-8")
        self.assertIn("new Date(2026, 7, 25)", content)


class GregorianDateRoundTripTests(TestCase):
    """Tests verifying Gregorian input → storage → display round-trip."""

    def _create_patient(self, email="pat@example.com"):
        user = User.objects.create_user(
            email=email,
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        return patient, user

    def test_start_date_gregorian_storage(self):
        """start_date: Gregorian input must be stored directly in DB."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        import datetime as dt_module

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "daily", "medication_times": "08:00", "start_date": "2026/08/25"})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "۱ قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})
        self.client.post(reverse("medications:medication_add"), {"step": "6"})

        schedule = MedicationSchedule.objects.get(medication__patient=patient)
        self.assertEqual(schedule.start_date, dt_module.date(2026, 8, 25))

    def test_end_date_gregorian_storage(self):
        """end_date: Gregorian input must be stored directly in DB."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        import datetime as dt_module

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "daily", "medication_times": "08:00", "start_date": "2026/08/25", "end_date": "2026/09/25"})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "۱ قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})
        self.client.post(reverse("medications:medication_add"), {"step": "6"})

        schedule = MedicationSchedule.objects.get(medication__patient=patient)
        self.assertEqual(schedule.end_date, dt_module.date(2026, 9, 25))

    def test_anchor_datetime_gregorian_storage(self):
        """anchor_datetime: Gregorian date portion must be stored directly in DB."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        import datetime as dt_module

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "every_x_hours"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "every_x_hours", "anchor_date": "2026/08/25", "anchor_time": "08:30", "x_hours_interval": 4})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "۱ قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})
        self.client.post(reverse("medications:medication_add"), {"step": "6"})

        schedule = MedicationSchedule.objects.get(medication__patient=patient)
        tz = timezone.get_current_timezone()
        local_dt = timezone.localtime(schedule.anchor_datetime, tz)
        self.assertEqual(local_dt.date(), dt_module.date(2026, 8, 25))
        self.assertEqual(local_dt.hour, 8)
        self.assertEqual(local_dt.minute, 30)

    def test_start_date_round_trip_displays_gregorian(self):
        """start_date: stored Gregorian must display as Gregorian in edit view."""
        patient, user = self._create_patient()
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "daily", "medication_times": "08:00", "start_date": "2026/08/25"})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "۱ قرص"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})
        self.client.post(reverse("medications:medication_add"), {"step": "6"})

        medication = Medication.objects.get(patient=patient)
        response = self.client.get(reverse("medications:medication_edit", args=[medication.pk]) + "?step=3")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2026/08/25")

    def test_calendar_gregorian_date_queries(self):
        """Calendar view must query using Gregorian date directly."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        import datetime as dt_module

        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص")
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
        )

        test_date = dt_module.date(2026, 8, 25)
        tz = timezone.get_current_timezone()
        dt = timezone.make_aware(datetime.datetime.combine(test_date, datetime.time(8, 0)), tz)
        MedicationIntake.objects.create(medication=medication, scheduled_time=dt, status="pending")

        date_str = test_date.strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar") + f"?date={date_str}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "آسپرین")
        intakes = response.context["intakes"]
        self.assertEqual(intakes.count(), 1)

    def test_calendar_date_in_url_path(self):
        """Calendar view must accept date in URL path (/calendar/<date>/)."""
        patient, user = self._create_patient()
        self.client.force_login(user)

        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص")
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
        )
        from medications.views import _generate_intakes_for_schedule
        _generate_intakes_for_schedule(schedule)

        date_str = timezone.now().date().strftime("%Y/%m/%d")
        response = self.client.get(reverse("medications:medication_calendar_date", args=[date_str]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "آسپرین")

