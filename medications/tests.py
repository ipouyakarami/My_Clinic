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

        med1 = Medication.objects.create(patient=patient1, name="دارو۱", unit="قرص", current_inventory=5)
        med2 = Medication.objects.create(patient=patient2, name="دارو۲", unit="قرص", current_inventory=5)

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
            {"scheduled_time": timezone.localtime(scheduled).strftime("%Y-%m-%d %H:%M:%S")},
        )
        self.assertRedirects(response, reverse("accounts:dashboard"))

        intake1.refresh_from_db()
        intake2.refresh_from_db()
        self.assertEqual(intake1.status, MedicationIntake.Status.TAKEN)
        self.assertEqual(intake2.status, MedicationIntake.Status.PENDING)

    def test_bulk_mark_decrements_inventory_for_each(self):
        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        med = Medication.objects.create(patient=patient, name="دارو", unit="قرص", current_inventory=10)

        now = timezone.now()
        scheduled = now.replace(minute=0, second=0, microsecond=0) + timezone.timedelta(hours=1)
        for _ in range(3):
            MedicationIntake.objects.create(
                medication=med, scheduled_time=scheduled, status=MedicationIntake.Status.PENDING
            )

        self.client.force_login(user)
        response = self.client.post(
            reverse("medications:intake_mark_batch"),
            {"scheduled_time": timezone.localtime(scheduled).strftime("%Y-%m-%d %H:%M:%S")},
        )
        self.assertRedirects(response, reverse("accounts:dashboard"))

        med.refresh_from_db()
        self.assertEqual(med.current_inventory, 7)
        self.assertEqual(
            MedicationIntake.objects.filter(
                medication=med, scheduled_time=scheduled, status=MedicationIntake.Status.TAKEN,
            ).count(),
            3,
        )

    def test_bulk_mark_blocked_when_inventory_zero(self):
        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        med = Medication.objects.create(patient=patient, name="دارو", unit="قرص", current_inventory=0)

        now = timezone.now()
        scheduled = now.replace(minute=0, second=0, microsecond=0) + timezone.timedelta(hours=1)
        MedicationIntake.objects.create(
            medication=med, scheduled_time=scheduled, status=MedicationIntake.Status.PENDING
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse("medications:intake_mark_batch"),
            {"scheduled_time": timezone.localtime(scheduled).strftime("%Y-%m-%d %H:%M:%S")},
        )
        self.assertRedirects(response, reverse("accounts:dashboard"))

        med.refresh_from_db()
        self.assertEqual(med.current_inventory, 0)
        self.assertEqual(
            MedicationIntake.objects.filter(medication=med, status=MedicationIntake.Status.TAKEN).count(),
            0,
        )

        self.assertTrue(
            any("Not enough inventory to take" in m.message for m in response.wsgi_request._messages)
        )

    def test_single_intake_taken_blocked_when_inventory_zero(self):
        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        med = Medication.objects.create(patient=patient, name="دارو", unit="قرص", current_inventory=0)

        now = timezone.now()
        scheduled = now.replace(minute=0, second=0, microsecond=0) + timezone.timedelta(hours=1)
        intake = MedicationIntake.objects.create(
            medication=med, scheduled_time=scheduled, status=MedicationIntake.Status.PENDING
        )

        self.client.force_login(user)
        response = self.client.get(reverse("medications:intake_taken", args=[intake.token]))
        self.assertRedirects(response, reverse("accounts:dashboard"))

        intake.refresh_from_db()
        med.refresh_from_db()
        self.assertEqual(intake.status, MedicationIntake.Status.PENDING)
        self.assertEqual(med.current_inventory, 0)

        self.assertTrue(
            any("Not enough inventory to take" in m.message for m in response.wsgi_request._messages)
        )

    def test_single_intake_taken_blocked_when_inventory_below_dosage(self):
        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        med = Medication.objects.create(
            patient=patient, name="دارو", unit="قرص", dosage="3", current_inventory=2,
        )

        now = timezone.now()
        scheduled = now.replace(minute=0, second=0, microsecond=0) + timezone.timedelta(hours=1)
        intake = MedicationIntake.objects.create(
            medication=med, scheduled_time=scheduled, status=MedicationIntake.Status.PENDING
        )

        self.client.force_login(user)
        response = self.client.get(reverse("medications:intake_taken", args=[intake.token]))
        self.assertRedirects(response, reverse("accounts:dashboard"))

        intake.refresh_from_db()
        med.refresh_from_db()
        self.assertEqual(intake.status, MedicationIntake.Status.PENDING)
        self.assertEqual(med.current_inventory, 2)

        self.assertTrue(
            any("requires 3" in m.message for m in response.wsgi_request._messages)
        )

    def test_bulk_mark_decrements_by_dosage_and_blocks_partial_stock(self):
        from decimal import Decimal
        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        med = Medication.objects.create(
            patient=patient, name="دارو", unit="قرص", dosage="2.5", current_inventory=5,
        )

        now = timezone.now()
        scheduled = now.replace(minute=0, second=0, microsecond=0) + timezone.timedelta(hours=1)
        for _ in range(3):
            MedicationIntake.objects.create(
                medication=med, scheduled_time=scheduled, status=MedicationIntake.Status.PENDING
            )

        self.client.force_login(user)
        response = self.client.post(
            reverse("medications:intake_mark_batch"),
            {"scheduled_time": timezone.localtime(scheduled).strftime("%Y-%m-%d %H:%M:%S")},
        )
        self.assertRedirects(response, reverse("accounts:dashboard"))

        med.refresh_from_db()
        self.assertEqual(med.current_inventory, Decimal("0"))
        self.assertEqual(
            MedicationIntake.objects.filter(
                medication=med, scheduled_time=scheduled, status=MedicationIntake.Status.TAKEN,
            ).count(),
            2,
        )
        self.assertEqual(
            MedicationIntake.objects.filter(
                medication=med, scheduled_time=scheduled, status=MedicationIntake.Status.PENDING,
            ).count(),
            1,
        )
        self.assertTrue(
            any("Not enough inventory to take" in m.message for m in response.wsgi_request._messages)
        )


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

    def test_inventory_decrements_by_dosage(self):
        from decimal import Decimal
        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient, name="آسپرین", unit="قرص",
            dosage="2", current_inventory=10,
        )
        intake = MedicationIntake.objects.create(
            medication=medication,
            scheduled_time=timezone.now() + datetime.timedelta(hours=1),
            status=MedicationIntake.Status.PENDING,
        )
        from .tasks import handle_intake_taken
        handle_intake_taken(intake.pk)
        medication.refresh_from_db()
        self.assertEqual(medication.current_inventory, Decimal("8"))

    def test_inventory_decrements_by_fractional_dosage(self):
        from decimal import Decimal
        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient, name="آسپرین", unit="ml",
            dosage="0.5", current_inventory=10,
        )
        intake = MedicationIntake.objects.create(
            medication=medication,
            scheduled_time=timezone.now() + datetime.timedelta(hours=1),
            status=MedicationIntake.Status.PENDING,
        )
        from .tasks import handle_intake_taken
        handle_intake_taken(intake.pk)
        medication.refresh_from_db()
        self.assertEqual(medication.current_inventory, Decimal("9.5"))

    def test_inventory_decrements_by_dosage_without_rounding(self):
        from decimal import Decimal
        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient, name="آسپرین", unit="میکروگرم",
            dosage="0.125", current_inventory=1,
        )
        intake = MedicationIntake.objects.create(
            medication=medication,
            scheduled_time=timezone.now() + datetime.timedelta(hours=1),
            status=MedicationIntake.Status.PENDING,
        )
        from .tasks import handle_intake_taken
        handle_intake_taken(intake.pk)
        medication.refresh_from_db()
        self.assertEqual(medication.current_inventory, Decimal("0.875"))

    def test_fractional_dosage_parses_persian_digits(self):
        from decimal import Decimal
        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient, name="آسپرین", unit="قرص",
            dosage="۲.۵", current_inventory=10,
        )
        intake = MedicationIntake.objects.create(
            medication=medication,
            scheduled_time=timezone.now() + datetime.timedelta(hours=1),
            status=MedicationIntake.Status.PENDING,
        )
        from .tasks import handle_intake_taken
        handle_intake_taken(intake.pk)
        medication.refresh_from_db()
        self.assertEqual(medication.current_inventory, Decimal("7.5"))

    def test_inventory_does_not_go_below_zero_when_dosage_exceeds_stock(self):
        from decimal import Decimal
        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(
            patient=patient, name="آسپرین", unit="قرص",
            dosage="3", current_inventory=1,
        )
        intake = MedicationIntake.objects.create(
            medication=medication,
            scheduled_time=timezone.now() + datetime.timedelta(hours=1),
            status=MedicationIntake.Status.PENDING,
        )
        from .tasks import handle_intake_taken
        handle_intake_taken(intake.pk)
        medication.refresh_from_db()
        self.assertEqual(medication.current_inventory, Decimal("0"))


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

    def test_reminder_email_successfully_sent(self):
        """An intake due now should trigger exactly one reminder email."""
        from medications import tasks

        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = MedicationIntake.objects.create(
            medication=Medication.objects.create(patient=patient, name="آسپرین", unit="قرص"),
            scheduled_time=timezone.now() - timezone.timedelta(minutes=5),
            status=MedicationIntake.Status.PENDING,
        )

        tasks.send_reminder_emails()

        medication.refresh_from_db()
        self.assertTrue(medication.reminder_sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("آسپرین", mail.outbox[0].body)

    def test_reminder_sent_for_generated_intakes(self):
        """Full flow: medication + schedule → generate intakes → reminder email sent."""
        from medications import tasks
        from medications.views import _generate_intakes_for_schedule

        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="مریم",
            last_name="صادقی",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص")
        schedule = MedicationSchedule.objects.create(
            medication=medication,
            frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"],
        )

        _generate_intakes_for_schedule(schedule)

        due_intake = MedicationIntake.objects.filter(
            medication=medication,
            scheduled_time__lte=timezone.now(),
        ).first()
        self.assertIsNotNone(due_intake, "No intake was generated with scheduled_time <= now")

        tasks.send_reminder_emails()

        due_intake.refresh_from_db()
        self.assertTrue(due_intake.reminder_sent)
        self.assertGreaterEqual(len(mail.outbox), 1)

    def test_reminder_groups_by_patient_and_time(self):
        """Multiple medications at the same time → one email. Different times → separate emails."""
        from medications import tasks

        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        med1 = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص")
        med2 = Medication.objects.create(patient=patient, name="ایبوپروفن", unit="قرص")

        past = timezone.now() - timezone.timedelta(minutes=5)
        later = timezone.now() - timezone.timedelta(minutes=10)

        MedicationIntake.objects.create(medication=med1, scheduled_time=past, status=MedicationIntake.Status.PENDING)
        MedicationIntake.objects.create(medication=med2, scheduled_time=past, status=MedicationIntake.Status.PENDING)
        MedicationIntake.objects.create(medication=med1, scheduled_time=later, status=MedicationIntake.Status.PENDING)

        tasks.send_reminder_emails()

        self.assertEqual(len(mail.outbox), 2)
        for intake in MedicationIntake.objects.all():
            intake.refresh_from_db()
            self.assertTrue(intake.reminder_sent)

    def test_reminder_not_sent_for_future_intake(self):
        """An intake scheduled in the future must NOT be emailed."""
        from medications import tasks

        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص")

        future = timezone.now() + timezone.timedelta(hours=1)
        intake = MedicationIntake.objects.create(
            medication=medication, scheduled_time=future, status=MedicationIntake.Status.PENDING
        )

        tasks.send_reminder_emails()

        self.assertEqual(len(mail.outbox), 0)
        intake.refresh_from_db()
        self.assertFalse(intake.reminder_sent)

    def test_reminder_not_resent_for_already_sent(self):
        """An intake with reminder_sent=True must NOT be re-emailed."""
        from medications import tasks

        user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        medication = Medication.objects.create(patient=patient, name="آسپرین", unit="قرص")

        past = timezone.now() - timezone.timedelta(minutes=5)
        intake = MedicationIntake.objects.create(
            medication=medication, scheduled_time=past, status=MedicationIntake.Status.PENDING,
            reminder_sent=True,
        )

        tasks.send_reminder_emails()

        self.assertEqual(len(mail.outbox), 0)


class ReminderSchedulePrecisionTests(TestCase):
    """Assert the reminder query matches past/current intakes exactly, not future ones."""

    def _make_patient(self, email="pat@example.com"):
        user = User.objects.create_user(
            email=email, password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="A", last_name="B", is_active=True,
        )
        return Patient.objects.create(user=user, phone_number="09121234567")

    def test_intake_in_past_is_matched(self):
        patient = self._make_patient()
        med = Medication.objects.create(patient=patient, name="Med", unit="tab")
        past = timezone.now() - timezone.timedelta(minutes=5)
        MedicationIntake.objects.create(medication=med, scheduled_time=past, status=MedicationIntake.Status.PENDING)

        due = MedicationIntake.objects.filter(
            status=MedicationIntake.Status.PENDING, reminder_sent=False, scheduled_time__lte=timezone.now(),
        )
        self.assertEqual(due.count(), 1)

    def test_intake_exactly_at_now_is_matched(self):
        patient = self._make_patient(email="now@example.com")
        med = Medication.objects.create(patient=patient, name="MedNow", unit="tab")
        MedicationIntake.objects.create(
            medication=med, scheduled_time=timezone.now(), status=MedicationIntake.Status.PENDING,
        )

        due = MedicationIntake.objects.filter(
            status=MedicationIntake.Status.PENDING, reminder_sent=False, scheduled_time__lte=timezone.now(),
        )
        self.assertEqual(due.count(), 1)

    def test_intake_one_minute_in_future_is_not_matched(self):
        patient = self._make_patient(email="future@example.com")
        med = Medication.objects.create(patient=patient, name="MedFut", unit="tab")
        future = timezone.now() + timezone.timedelta(minutes=1)
        MedicationIntake.objects.create(medication=med, scheduled_time=future, status=MedicationIntake.Status.PENDING)

        due = MedicationIntake.objects.filter(
            status=MedicationIntake.Status.PENDING, reminder_sent=False, scheduled_time__lte=timezone.now(),
        )
        self.assertEqual(due.count(), 0)

    def test_beat_schedule_uses_minute_boundary_crontab(self):
        """The reminder task must be on a per-minute crontab, not a drifting interval."""
        from myclinic import settings

        reminder = settings.CELERY_BEAT_SCHEDULE["send-medication-reminders-every-minute"]
        from celery.schedules import crontab as _crontab

        self.assertIsInstance(reminder["schedule"], _crontab)
        self.assertEqual(reminder["schedule"].minute, set(range(60)))


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
            "name": "آسپرین", "unit": "tablet", "frequency_type": "daily",
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
            "medication_pk": med.pk, "name": "ایبوپروفن", "unit": "tablet",
            "dosage": "2", "frequency_type": "daily",
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
        self.assertEqual(med.dosage, "2")

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
            "name": "آسپرین", "unit": "tablet", "frequency_type": "daily",
            "medication_times": ["08:00"], "current_inventory": 30,
            "refill_reminder_threshold": 5, "start_date": "", "end_date": "",
        }
        session.save()

        response = self.client.post(
            reverse("medications:doctor_medication_add", args=[appointment.pk]),
            {"step": "1", "name": "آسپرین", "unit": "tablet"},
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
            "name": "آسپرین", "unit": "tablet", "frequency_type": "daily",
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
                "unit": "tablet",
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
                "dosage": "1",
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

    def test_step1_visible_on_fresh_load(self):
        """Bug 1 regression: step 1 inputs must be visible on fresh page load without clicking Next."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="Maryam",
            last_name="S",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        response = self.client.get(reverse("medications:medication_add"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('name="name"', content)
        self.assertIn('name="unit"', content)
        self.assertIn("<select", content)

    def test_step1_visible_on_validation_error_reload(self):
        """Bug 1 regression: step 1 must remain visible when returning due to a validation error."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="Maryam",
            last_name="S",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "1", "name": "", "unit": ""},
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('name="name"', content)
        self.assertIn('name="unit"', content)

    def test_step1_edit_flow_visible_on_load(self):
        """Bug 1 regression: edit flow must also show step 1 immediately on load."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="Maryam",
            last_name="S",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        medication = Medication.objects.create(
            patient=patient, name="Aspirin", unit="tablet", dosage="1 tablet",
            current_inventory=10,
        )

        response = self.client.get(reverse("medications:medication_edit", args=[medication.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('name="name"', content)
        self.assertIn('name="unit"', content)
        self.assertIn("<select", content)
        self.assertIn("Aspirin", content)

    def test_unit_dropdown_has_all_choices(self):
        """Bug 2: unit field must render as a <select> with all 19 choices in order."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="Maryam",
            last_name="S",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        response = self.client.get(reverse("medications:medication_add"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        expected_units = [
            "tablet", "capsule", "pill", "sachet", "packet", "bottle", "vial",
            "ampoule", "tube", "drop", "spray", "patch", "suppository", "dose",
            "ml", "mg", "g", "mcg", "unit",
        ]
        for unit_value in expected_units:
            self.assertIn(f'value="{unit_value}"', content)

    def test_legacy_unit_value_shows_in_edit_dropdown(self):
        """Bug 2: medication with a legacy free-text unit must not crash edit page."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="Maryam",
            last_name="S",
            is_active=True,
        )
        patient = Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        medication = Medication.objects.create(
            patient=patient, name="Old Med", unit="قرص", dosage="1",
            current_inventory=10,
        )

        response = self.client.get(reverse("medications:medication_edit", args=[medication.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("قرص", content)

    def test_submit_valid_unit_saves_correctly(self):
        """Bug 2: submitting with a valid unit choice saves and displays correctly."""
        user = User.objects.create_user(
            email="pat@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            first_name="Maryam",
            last_name="S",
            is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)

        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "1", "name": "Aspirin", "unit": "tablet"},
        )
        self.assertEqual(response.status_code, 302)

        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(
            reverse("medications:medication_add"),
            {"step": "3", "frequency_type": "daily", "medication_times": "08:00", "start_date": "2026/09/25"},
        )
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "1"})
        self.client.post(
            reverse("medications:medication_add"),
            {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5},
        )
        response = self.client.post(reverse("medications:medication_add"), {"step": "6"})
        self.assertEqual(response.status_code, 302)

        medication = Medication.objects.get(name="Aspirin")
        self.assertEqual(medication.unit, "tablet")

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

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
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

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})

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

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
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

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
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

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
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

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "specific_days"})

        response = self.client.post(
            reverse("medications:medication_add"),
            {
                "step": "3",
                "frequency_type": "specific_days",
                "specific_days": ["sat", "mon", "wed"],
                "medication_times_specific": "08:00",
                "start_date_specific": "2026/09/25",
            },
        )
        self.assertEqual(response.status_code, 302)

    def test_step3_advances_to_step4_for_all_frequency_types(self):
        """Step 3 must advance to step 4 for every frequency type, using a
        date that is not in the past so the GregorianDateField accepts it."""
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

        future_date = (timezone.now().date() + datetime.timedelta(days=7)).strftime("%Y/%m/%d")

        cases = {
            "daily": {"frequency_type": "daily", "medication_times": "08:00\n20:00", "start_date": future_date},
            "specific_days": {
                "frequency_type": "specific_days",
                "specific_days": ["sat", "mon"],
                "medication_times_specific": "08:00",
                "start_date_specific": future_date,
            },
            "every_n_days": {
                "frequency_type": "every_n_days",
                "n_days_interval": 2,
                "medication_times_n": "08:00",
                "start_date_n": future_date,
            },
            "every_x_hours": {
                "frequency_type": "every_x_hours",
                "x_hours_interval": 4,
                "anchor_date": future_date,
                "anchor_time": "08:00",
            },
        }

        for freq, payload in cases.items():
            with self.subTest(freq=freq):
                self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
                self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": freq})
                response = self.client.post(
                    reverse("medications:medication_add"),
                    {"step": "3", **payload},
                )
                self.assertEqual(response.status_code, 302, f"{freq}: expected redirect to step 4, got {response.status_code}")
                self.assertIn("step=4", response.url, f"{freq}: redirect did not go to step 4")

    def test_step3_edit_flow_advances_for_all_frequency_types(self):
        """The edit flow reuses the same wizard; step 3 must advance for all
        frequency types there too, including with a past start date."""
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

        past_date = (timezone.now().date() - datetime.timedelta(days=7)).strftime("%Y/%m/%d")
        future_date = (timezone.now().date() + datetime.timedelta(days=7)).strftime("%Y/%m/%d")

        med = Medication.objects.create(
            patient=patient, name="آسپرین", unit="tablet", dosage="۱ قرص", current_inventory=10,
        )

        schedules = {
            "daily": MedicationSchedule.objects.create(
                medication=med, frequency_type=MedicationSchedule.FrequencyType.DAILY,
                medication_times=["08:00"], start_date=timezone.now().date() - datetime.timedelta(days=7),
                end_date=timezone.now().date() + datetime.timedelta(days=7),
            ),
            "specific_days": MedicationSchedule.objects.create(
                medication=med, frequency_type=MedicationSchedule.FrequencyType.SPECIFIC_DAYS,
                specific_days=["sat", "mon"], medication_times=["08:00"],
                start_date=timezone.now().date() - datetime.timedelta(days=7),
                end_date=timezone.now().date() + datetime.timedelta(days=7),
            ),
            "every_n_days": MedicationSchedule.objects.create(
                medication=med, frequency_type=MedicationSchedule.FrequencyType.EVERY_N_DAYS,
                n_days_interval=2, medication_times=["08:00"],
                start_date=timezone.now().date() - datetime.timedelta(days=7),
            ),
            "every_x_hours": MedicationSchedule.objects.create(
                medication=med, frequency_type=MedicationSchedule.FrequencyType.EVERY_X_HOURS,
                x_hours_interval=4, anchor_datetime=timezone.make_aware(
                    datetime.datetime.combine(
                        timezone.now().date() - datetime.timedelta(days=7),
                        datetime.time(9, 0),
                    ),
                    timezone.get_current_timezone(),
                ),
                start_date=timezone.now().date() - datetime.timedelta(days=7),
                end_date=timezone.now().date() + datetime.timedelta(days=7),
            ),
        }

        edit_payloads = {
            "daily": {"medication_times": "08:00\n20:00", "start_date": past_date},
            "specific_days": {
                "specific_days": ["sat", "mon", "wed"],
                "medication_times_specific": "08:00",
                "start_date_specific": past_date,
            },
            "every_n_days": {
                "n_days_interval": 3,
                "medication_times_n": "09:00",
                "start_date_n": past_date,
            },
            "every_x_hours": {
                "x_hours_interval": 6,
                "anchor_date": past_date,
                "anchor_time": "09:00",
            },
        }

        for freq, payload in edit_payloads.items():
            with self.subTest(freq=freq):
                session = self.client.session
                session[f"medication_wizard_{user.id}"] = {
                    "medication_pk": med.pk, "name": "آسپرین", "unit": "tablet",
                    "dosage": "1", "frequency_type": freq,
                    "medication_times": ["08:00"], "specific_days": ["sat", "mon"],
                    "n_days_interval": 2, "x_hours_interval": 4,
                    "anchor_date": past_date, "anchor_time": "08:00",
                    "start_date": past_date, "end_date": future_date,
                }
                session.save()
                response = self.client.post(
                    reverse("medications:medication_edit", args=[med.pk]),
                    {"step": "3", "frequency_type": freq, **payload},
                )
                self.assertEqual(response.status_code, 302, f"{freq}: expected redirect to step 4, got {response.status_code}")
                self.assertIn("step=4", response.url, f"{freq}: redirect did not go to step 4")

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

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "every_x_hours"})

        response = self.client.post(
            reverse("medications:medication_add"),
            {
                "step": "3",
                "frequency_type": "every_x_hours",
                "anchor_date": "2026/09/25",
                "anchor_time": "08:00",
                "x_hours_interval": 4,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("step=4", response.url)

        session = self.client.session
        wizard_data = session.get(f"medication_wizard_{user.id}", {})
        self.assertEqual(wizard_data.get("anchor_date"), "2026/09/25")
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

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "daily", "medication_times": "08:00\n20:00", "start_date": "2026/09/25"})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "1"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})

        response = self.client.get(reverse("medications:medication_add") + "?step=6")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "آسپرین")
        self.assertContains(response, "tablet")
        self.assertContains(response, "1")
        self.assertContains(response, "10")
        self.assertContains(response, "5")
        self.assertContains(response, "08:00")
        self.assertContains(response, "20:00")
        self.assertContains(response, "2026/09/25")

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

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "every_x_hours"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "every_x_hours", "anchor_date": "2026/09/25", "anchor_time": "08:00", "x_hours_interval": 4})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "1"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})

        response = self.client.get(reverse("medications:medication_add") + "?step=6")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "آسپرین")
        self.assertContains(response, "1")
        self.assertContains(response, "2026/09/25")
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

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "specific_days"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "specific_days", "specific_days": ["sat", "mon"], "medication_times_specific": "08:00", "start_date_specific": "2026/09/25"})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "1"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})

        response = self.client.get(reverse("medications:medication_add") + "?step=6")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "آسپرین")
        self.assertContains(response, "Saturday")
        self.assertContains(response, "Monday")
        self.assertContains(response, "2026/09/25")

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

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "daily", "medication_times": "08:00", "start_date": "2026/09/25"})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "1"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})
        response = self.client.post(reverse("medications:medication_add"), {"step": "6"})
        self.assertEqual(response.status_code, 302)

        medication = Medication.objects.get(name="آسپرین")
        schedule = MedicationSchedule.objects.get(medication=medication)
        import datetime as dt_module
        self.assertEqual(schedule.start_date, dt_module.date(2026, 9, 25))

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


    def _create_patient_for_dosage_tests(self, email="pat@example.com"):
        user = User.objects.create_user(
            email=email, password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        Patient.objects.create(user=user, phone_number="09121234567")
        self.client.force_login(user)
        return user

    def _post_steps_1_to_3_for_dosage(self, freq="daily", start_date=None, anchor_date=None, anchor_time="08:00"):
        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": freq})
        data = {"step": "3", "frequency_type": freq}
        if freq == "daily":
            data["medication_times"] = "08:00"
            if start_date:
                data["start_date"] = start_date
        elif freq == "specific_days":
            data["specific_days"] = ["sat", "mon"]
            data["medication_times_specific"] = "08:00"
            if start_date:
                data["start_date_specific"] = start_date
        elif freq == "every_n_days":
            data["n_days_interval"] = 2
            data["medication_times_n"] = "08:00"
            if start_date:
                data["start_date_n"] = start_date
        elif freq == "every_x_hours":
            data["x_hours_interval"] = 4
            if anchor_date:
                data["anchor_date"] = anchor_date
            data["anchor_time"] = anchor_time
        self.client.post(reverse("medications:medication_add"), data)

    def test_add_medication_requires_inventory_greater_than_zero(self):
        self._create_patient_for_dosage_tests()
        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(
            reverse("medications:medication_add"),
            {"step": "3", "frequency_type": "daily", "medication_times": "08:00"},
        )
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "1"})

        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "5", "current_inventory": 0, "refill_reminder_threshold": 5},
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Current inventory must be more than 0", content)

        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "5", "current_inventory": -2, "refill_reminder_threshold": 5},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Current inventory cannot be negative", response.content.decode("utf-8"))


class DosageValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        Patient.objects.create(user=self.user, phone_number="09121234567")
        self.client.force_login(self.user)
        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "daily", "medication_times": "08:00"})

    def _post_dosage(self, value):
        return self.client.post(
            reverse("medications:medication_add"),
            {"step": "4", "dosage": value},
        )

    def test_valid_integer_dosage_accepted(self):
        response = self._post_dosage("2")
        self.assertEqual(response.status_code, 302)
        self.assertIn("step=5", response.url)

    def test_valid_decimal_dosage_accepted(self):
        response = self._post_dosage("0.5")
        self.assertEqual(response.status_code, 302)

    def test_too_precise_dosage_rejected(self):
        response = self._post_dosage("0.1234567")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "decimal place")

    def test_valid_persian_digits_accepted(self):
        response = self._post_dosage("۲.۵")
        self.assertEqual(response.status_code, 302)

    def test_non_numeric_dosage_rejected(self):
        response = self._post_dosage("1 tablet")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "must be a number")

    def test_zero_dosage_rejected(self):
        response = self._post_dosage("0")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "positive number")

    def test_negative_dosage_rejected(self):
        response = self._post_dosage("-1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "positive number")

    def test_empty_dosage_accepted(self):
        response = self._post_dosage("")
        self.assertEqual(response.status_code, 302)


class StartDateNotPastTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="pat@example.com", password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT, first_name="مریم", last_name="صادقی", is_active=True,
        )
        Patient.objects.create(user=self.user, phone_number="09121234567")
        self.client.force_login(self.user)
        self.past_date = (timezone.now().date() - datetime.timedelta(days=7)).strftime("%Y/%m/%d")
        self.future_date = (timezone.now().date() + datetime.timedelta(days=7)).strftime("%Y/%m/%d")

    def test_daily_past_start_date_rejected(self):
        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "3", "frequency_type": "daily", "medication_times": "08:00", "start_date": self.past_date},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be in the past")

    def test_daily_future_start_date_accepted(self):
        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "3", "frequency_type": "daily", "medication_times": "08:00", "start_date": self.future_date},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("step=4", response.url)

    def test_every_x_hours_past_anchor_rejected(self):
        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "every_x_hours"})
        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "3", "frequency_type": "every_x_hours", "anchor_date": self.past_date, "anchor_time": "08:00", "x_hours_interval": 4},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be in the past")

    def test_edit_existing_past_start_date_not_blocked(self):
        med = Medication.objects.create(patient=self.user.patient, name="آسپرین", unit="tablet", dosage="1")
        MedicationSchedule.objects.create(
            medication=med, frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"], start_date=timezone.now().date() - datetime.timedelta(days=7),
        )
        session = self.client.session
        session[f"medication_wizard_{self.user.id}"] = {
            "medication_pk": med.pk, "name": "آسپرین", "unit": "tablet",
            "dosage": "1", "frequency_type": "daily", "medication_times": ["08:00"],
            "start_date": self.past_date,
        }
        session.save()
        response = self.client.post(
            reverse("medications:medication_edit", args=[med.pk]),
            {"step": "3", "frequency_type": "daily", "medication_times": "08:00\n20:00", "start_date": self.past_date},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("step=4", response.url)

    def test_edit_existing_past_anchor_not_blocked(self):
        past_local = timezone.localtime(timezone.now()) - datetime.timedelta(days=7)
        med = Medication.objects.create(patient=self.user.patient, name="آسپرین", unit="tablet", dosage="1")
        MedicationSchedule.objects.create(
            medication=med, frequency_type=MedicationSchedule.FrequencyType.EVERY_X_HOURS,
            x_hours_interval=4, anchor_datetime=past_local,
        )
        anchor_date = past_local.date().strftime("%Y/%m/%d")
        anchor_time = past_local.strftime("%H:%M")
        session = self.client.session
        session[f"medication_wizard_{self.user.id}"] = {
            "medication_pk": med.pk, "name": "آسپرین", "unit": "tablet",
            "dosage": "1", "frequency_type": "every_x_hours",
            "x_hours_interval": 4, "anchor_date": anchor_date, "anchor_time": anchor_time,
        }
        session.save()
        response = self.client.post(
            reverse("medications:medication_edit", args=[med.pk]),
            {"step": "3", "frequency_type": "every_x_hours", "anchor_date": anchor_date, "anchor_time": anchor_time, "x_hours_interval": 6},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("step=4", response.url)

    def test_end_before_start_rejected_for_daily(self):
        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "3", "frequency_type": "daily", "medication_times": "08:00",
             "start_date": self.future_date, "end_date": self.past_date},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "End date cannot be before the start date")

    def test_end_equals_start_accepted_for_daily(self):
        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "3", "frequency_type": "daily", "medication_times": "08:00",
             "start_date": self.future_date, "end_date": self.future_date},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("step=4", response.url)

    def test_blank_end_date_accepted_for_daily(self):
        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "3", "frequency_type": "daily", "medication_times": "08:00",
             "start_date": self.future_date},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("step=4", response.url)

    def test_end_before_start_rejected_for_specific_days(self):
        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "specific_days"})
        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "3", "frequency_type": "specific_days", "specific_days": ["sat", "mon"],
             "medication_times_specific": "08:00",
             "start_date_specific": self.future_date, "end_date_specific": self.past_date},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "End date cannot be before the start date")

    def test_end_before_start_rejected_for_every_n_days(self):
        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "every_n_days"})
        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "3", "frequency_type": "every_n_days", "n_days_interval": 2,
             "medication_times_n": "08:00",
             "start_date_n": self.future_date, "end_date_n": self.past_date},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "End date cannot be before the start date")

    def test_end_before_anchor_rejected_for_every_x_hours(self):
        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "every_x_hours"})
        response = self.client.post(
            reverse("medications:medication_add"),
            {"step": "3", "frequency_type": "every_x_hours",
             "anchor_date": self.future_date, "anchor_time": "08:00", "x_hours_interval": 4,
             "end_date_x": self.past_date},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "End date cannot be before the start date")

    def test_edit_end_date_validated_against_start_date(self):
        med = Medication.objects.create(patient=self.user.patient, name="آسپرین", unit="tablet", dosage="1")
        MedicationSchedule.objects.create(
            medication=med, frequency_type=MedicationSchedule.FrequencyType.DAILY,
            medication_times=["08:00"], start_date=timezone.now().date() - datetime.timedelta(days=7),
        )
        earlier_date = (timezone.now().date() - datetime.timedelta(days=14)).strftime("%Y/%m/%d")
        session = self.client.session
        session[f"medication_wizard_{self.user.id}"] = {
            "medication_pk": med.pk, "name": "آسپرین", "unit": "tablet",
            "dosage": "1", "frequency_type": "daily", "medication_times": ["08:00"],
            "start_date": self.past_date,
        }
        session.save()
        response = self.client.post(
            reverse("medications:medication_edit", args=[med.pk]),
            {"step": "3", "frequency_type": "daily", "medication_times": "08:00",
             "start_date": self.past_date, "end_date": earlier_date},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "End date cannot be before the start date")


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
        """All statuses (pending, taken, skipped) should be displayed."""
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

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "daily", "medication_times": "08:00", "start_date": "2026/09/25"})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "1"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})
        self.client.post(reverse("medications:medication_add"), {"step": "6"})

        schedule = MedicationSchedule.objects.get(medication__patient=patient)
        self.assertEqual(schedule.start_date, dt_module.date(2026, 9, 25))

    def test_end_date_gregorian_storage(self):
        """end_date: Gregorian input must be stored directly in DB."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        import datetime as dt_module

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "daily", "medication_times": "08:00", "start_date": "2026/09/25", "end_date": "2026/09/25"})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "1"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})
        self.client.post(reverse("medications:medication_add"), {"step": "6"})

        schedule = MedicationSchedule.objects.get(medication__patient=patient)
        self.assertEqual(schedule.end_date, dt_module.date(2026, 9, 25))

    def test_anchor_datetime_gregorian_storage(self):
        """anchor_datetime: Gregorian date portion must be stored directly in DB."""
        patient, user = self._create_patient()
        self.client.force_login(user)
        import datetime as dt_module

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "every_x_hours"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "every_x_hours", "anchor_date": "2026/09/25", "anchor_time": "08:30", "x_hours_interval": 4})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "1"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})
        self.client.post(reverse("medications:medication_add"), {"step": "6"})

        schedule = MedicationSchedule.objects.get(medication__patient=patient)
        tz = timezone.get_current_timezone()
        local_dt = timezone.localtime(schedule.anchor_datetime, tz)
        self.assertEqual(local_dt.date(), dt_module.date(2026, 9, 25))
        self.assertEqual(local_dt.hour, 8)
        self.assertEqual(local_dt.minute, 30)

    def test_start_date_round_trip_displays_gregorian(self):
        """start_date: stored Gregorian must display as Gregorian in edit view."""
        patient, user = self._create_patient()
        self.client.force_login(user)

        self.client.post(reverse("medications:medication_add"), {"step": "1", "name": "آسپرین", "unit": "tablet"})
        self.client.post(reverse("medications:medication_add"), {"step": "2", "frequency_type": "daily"})
        self.client.post(reverse("medications:medication_add"), {"step": "3", "frequency_type": "daily", "medication_times": "08:00", "start_date": "2026/09/25"})
        self.client.post(reverse("medications:medication_add"), {"step": "4", "dosage": "1"})
        self.client.post(reverse("medications:medication_add"), {"step": "5", "current_inventory": 10, "refill_reminder_threshold": 5})
        self.client.post(reverse("medications:medication_add"), {"step": "6"})

        medication = Medication.objects.get(patient=patient)
        response = self.client.get(reverse("medications:medication_edit", args=[medication.pk]) + "?step=3")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2026/09/25")

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


class TelegramActivationCodeTests(TestCase):
    """Tests for TelegramActivationCode generation, expiry, and single-use."""

    def _create_patient(self):
        user = User.objects.create_user(
            email="telegram@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            is_active=True,
        )
        return Patient.objects.create(user=user)

    def test_generate_creates_code_with_10_minute_expiry(self):
        from .models import TelegramActivationCode

        code = TelegramActivationCode.generate_for_chat("123456789", "testuser")
        delta = code.expires_at - code.created_at
        self.assertAlmostEqual(delta.total_seconds(), 600, delta=5)  # 10 minutes +/- 5s
        self.assertEqual(code.telegram_chat_id, "123456789")
        self.assertEqual(code.telegram_username, "testuser")
        self.assertIsNone(code.used_at)
        self.assertIsNone(code.used_by_patient)

    def test_get_valid_for_chat_returns_unexpired_unused_code(self):
        from .models import TelegramActivationCode

        code = TelegramActivationCode.generate_for_chat("999", "user999")
        valid = TelegramActivationCode.get_valid_for_chat("999")
        self.assertEqual(valid, code)

    def test_get_valid_for_chat_excludes_used_codes(self):
        from .models import TelegramActivationCode
        from django.utils import timezone

        patient = self._create_patient()
        code = TelegramActivationCode.generate_for_chat("888", "user888")
        code.used_at = timezone.now()
        code.used_by_patient = patient
        code.save()

        valid = TelegramActivationCode.get_valid_for_chat("888")
        self.assertIsNone(valid)

    def test_get_valid_for_chat_excludes_expired_codes(self):
        from .models import TelegramActivationCode
        from django.utils import timezone
        import datetime

        code = TelegramActivationCode.generate_for_chat("777", "user777")
        # Make it expired
        code.expires_at = timezone.now() - datetime.timedelta(minutes=1)
        code.save()

        valid = TelegramActivationCode.get_valid_for_chat("777")
        self.assertIsNone(valid)

    def test_get_valid_for_chat_does_not_cross_chats(self):
        from .models import TelegramActivationCode

        TelegramActivationCode.generate_for_chat("111", "user111")
        TelegramActivationCode.generate_for_chat("222", "user222")

        valid = TelegramActivationCode.get_valid_for_chat("111")
        self.assertEqual(valid.telegram_chat_id, "111")

    def test_is_expired_flag(self):
        from .models import TelegramActivationCode
        from django.utils import timezone
        import datetime

        code = TelegramActivationCode.generate_for_chat("555", "user555")
        self.assertFalse(code.is_expired)

        code.expires_at = timezone.now() - datetime.timedelta(seconds=1)
        self.assertTrue(code.is_expired)

    def test_is_used_flag(self):
        from .models import TelegramActivationCode

        code = TelegramActivationCode.generate_for_chat("444", "user444")
        self.assertFalse(code.is_used)
        code.used_at = timezone.now()
        self.assertTrue(code.is_used)


class TelegramConnectViewTests(TestCase):
    """Tests for the /telegram/connect/ page and linking flow."""

    def _create_patient(self, email="patient@example.com"):
        user = User.objects.create_user(
            email=email,
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            is_active=True,
        )
        patient = Patient.objects.create(user=user)
        self.client.login(username=email, password=STRONG_PASSWORD)
        return patient

    def _create_doctor(self, email="doctor@example.com"):
        user = User.objects.create_user(
            email=email,
            password=STRONG_PASSWORD,
            user_type=User.UserType.DOCTOR,
            is_active=True,
        )
        return user

    def test_connect_page_requires_login(self):
        response = self.client.get(reverse("accounts:telegram_connect"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_connect_page_denies_doctors(self):
        self._create_doctor()
        response = self.client.get(reverse("accounts:telegram_connect"))
        self.assertEqual(response.status_code, 302)

    def test_connect_page_shows_when_not_linked(self):
        patient = self._create_patient()
        response = self.client.get(reverse("accounts:telegram_connect"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Connect Your Account")
        self.assertContains(response, "myyclinicbot")

    def test_connect_page_shows_connected_status(self):
        patient = self._create_patient()
        patient.telegram_chat_id = "123456"
        patient.telegram_username = "testuser"
        patient.save()

        response = self.client.get(reverse("accounts:telegram_connect"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Connected")
        self.assertContains(response, "@testuser")
        self.assertContains(response, "Disconnect Telegram")

    def test_valid_code_links_patient_to_chat(self):
        from medications.models import TelegramActivationCode

        patient = self._create_patient()
        code = TelegramActivationCode.generate_for_chat("123456", "testuser")

        response = self.client.post(reverse("accounts:telegram_link"), {"activation_code": code.code})
        self.assertEqual(response.status_code, 302)
        patient.refresh_from_db()
        self.assertEqual(patient.telegram_chat_id, "123456")
        self.assertEqual(patient.telegram_username, "testuser")

        code.refresh_from_db()
        self.assertIsNotNone(code.used_at)
        self.assertEqual(code.used_by_patient, patient)

    def test_invalid_code_rejected(self):
        self._create_patient()
        response = self.client.post(reverse("accounts:telegram_link"), {"activation_code": "FAKE123"})
        self.assertEqual(response.status_code, 200)  # Returns to page with error
        self.assertContains(response, "invalid")

    def test_expired_code_rejected(self):
        from medications.models import TelegramActivationCode
        from django.utils import timezone
        import datetime

        patient = self._create_patient()
        code = TelegramActivationCode.generate_for_chat("789", "user789")
        code.expires_at = timezone.now() - datetime.timedelta(minutes=1)
        code.save()

        response = self.client.post(reverse("accounts:telegram_link"), {"activation_code": code.code})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "expired")
        patient.refresh_from_db()
        self.assertIsNone(patient.telegram_chat_id)

    def test_already_used_code_rejected(self):
        from medications.models import TelegramActivationCode
        from django.utils import timezone

        patient = self._create_patient()
        code = TelegramActivationCode.generate_for_chat("111", "user111")
        code.used_at = timezone.now()
        code.used_by_patient = patient
        code.save()

        response = self.client.post(reverse("accounts:telegram_link"), {"activation_code": code.code})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already been used")

    def test_code_is_case_insensitive(self):
        from medications.models import TelegramActivationCode

        patient = self._create_patient()
        code = TelegramActivationCode.generate_for_chat("222", "user222")

        response = self.client.post(reverse("accounts:telegram_link"), {"activation_code": code.code.lower()})
        self.assertEqual(response.status_code, 302)
        patient.refresh_from_db()
        self.assertEqual(patient.telegram_chat_id, "222")

    def test_cannot_link_on_behalf_of_another_patient(self):
        """A code generated for chat X can be used by any patient, but the chat_id
        is unique — so a different patient linking the same chat_id is blocked."""
        from medications.models import TelegramActivationCode

        # Patient A creates the code
        patient_a = self._create_patient("a@example.com")
        code = TelegramActivationCode.generate_for_chat("999", "user999")

        # Log out patient A
        self.client.logout()

        # Patient B logs in and tries to use the same code
        patient_b = self._create_patient("b@example.com")
        response = self.client.post(reverse("accounts:telegram_link"), {"activation_code": code.code})
        # Patient B gets linked to chat 999 (they submitted the code)
        patient_b.refresh_from_db()
        self.assertEqual(patient_b.telegram_chat_id, "999")

    def test_disconnect_removes_link(self):
        patient = self._create_patient()
        patient.telegram_chat_id = "123456"
        patient.telegram_username = "testuser"
        patient.save()

        response = self.client.post(reverse("accounts:telegram_disconnect"))
        self.assertEqual(response.status_code, 302)
        patient.refresh_from_db()
        self.assertIsNone(patient.telegram_chat_id)
        self.assertIsNone(patient.telegram_username)

    def test_disconnect_requires_login(self):
        response = self.client.post(reverse("accounts:telegram_disconnect"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_doctor_cannot_unlink(self):
        self._create_doctor()
        response = self.client.post(reverse("accounts:telegram_disconnect"))
        self.assertEqual(response.status_code, 302)


class TelegramReminderIntegrationTests(TestCase):
    """Tests for reminder task Telegram integration."""

    def _create_patient_with_intake(self, linked=True):
        user = User.objects.create_user(
            email="reminder@example.com",
            password=STRONG_PASSWORD,
            user_type=User.UserType.PATIENT,
            is_active=True,
        )
        patient = Patient.objects.create(user=user)
        if linked:
            patient.telegram_chat_id = "555666"
            patient.telegram_username = "reminder_user"
            patient.save()

        medication = Medication.objects.create(
            patient=patient,
            name="Aspirin",
            dosage="1 tablet",
        )
        now = timezone.now()
        intake = MedicationIntake.objects.create(
            medication=medication,
            scheduled_time=now - datetime.timedelta(minutes=5),
            status=MedicationIntake.Status.PENDING,
        )
        return patient, intake

    @patch("medications.tasks.send_telegram_message")
    @patch("medications.tasks.send_mail")
    def test_linked_patient_receives_telegram_and_email(self, mock_send_mail, mock_send_telegram):
        from medications.tasks import send_reminder_emails

        patient, intake = self._create_patient_with_intake(linked=True)
        mock_send_mail.return_value = 1
        mock_send_telegram.return_value = True

        send_reminder_emails()

        mock_send_mail.assert_called_once()
        mock_send_telegram.assert_called_once()
        self.assertEqual(mock_send_telegram.call_args[0][0], "555666")

        intake.refresh_from_db()
        self.assertTrue(intake.reminder_sent)

    @patch("medications.tasks.send_telegram_message")
    @patch("medications.tasks.send_mail")
    def test_unlinked_patient_receives_email_only(self, mock_send_mail, mock_send_telegram):
        from medications.tasks import send_reminder_emails

        patient, intake = self._create_patient_with_intake(linked=False)
        mock_send_mail.return_value = 1

        send_reminder_emails()

        mock_send_mail.assert_called_once()
        mock_send_telegram.assert_not_called()

        intake.refresh_from_db()
        self.assertTrue(intake.reminder_sent)

    @patch("medications.tasks.send_telegram_message")
    @patch("medications.tasks.send_mail")
    def test_telegram_failure_does_not_block_email(self, mock_send_mail, mock_send_telegram):
        from medications.tasks import send_reminder_emails

        patient, intake = self._create_patient_with_intake(linked=True)
        mock_send_mail.return_value = 1
        mock_send_telegram.return_value = False  # Telegram fails

        send_reminder_emails()

        mock_send_mail.assert_called_once()
        mock_send_telegram.assert_called_once()

        # reminder_sent should still be True because email succeeded
        intake.refresh_from_db()
        self.assertTrue(intake.reminder_sent)

    @patch("medications.tasks.send_telegram_message")
    @patch("medications.tasks.send_mail")
    def test_both_channels_failure_leaves_reminder_unsent(self, mock_send_mail, mock_send_telegram):
        from medications.tasks import send_reminder_emails

        patient, intake = self._create_patient_with_intake(linked=True)
        mock_send_mail.side_effect = Exception("SMTP error")
        mock_send_telegram.return_value = False

        send_reminder_emails()

        intake.refresh_from_db()
        self.assertFalse(intake.reminder_sent)

    @patch("medications.tasks.send_telegram_message")
    def test_telegram_message_content_is_correct(self, mock_send_telegram):
        from medications.tasks import send_reminder_emails

        patient, intake = self._create_patient_with_intake(linked=True)
        mock_send_telegram.return_value = True

        send_reminder_emails()

        message_text = mock_send_telegram.call_args[0][1]
        self.assertIn("Medication Reminder", message_text)
        self.assertIn("Aspirin", message_text)
        self.assertIn("1 tablet", message_text)

