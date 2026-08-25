import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Doctor, Patient, User
from allauth.account.models import EmailAddress
from appointments.models import TimeSlot
from appointments.services import book_appointment, generate_time_slots, SlotAlreadyBooked
from medications.models import Medication, MedicationSchedule, MedicationIntake
from medications.views import _generate_intakes_for_schedule


class Command(BaseCommand):
    help = "Seed a repeatable demo dataset (doctors, slots, patients, medications, appointments)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--i-know-this-is-demo-data",
            action="store_true",
            help="Bypass the DEBUG-only guard and run even with DEBUG=False.",
        )

    # -- Demo identity namespace -------------------------------------------
    EMAIL_DOMAIN = "demo.myclinic.test"
    PASSWORD = settings.DEMO_PASSWORD  # shared with the login-page demo panel

    DOCTORS = [
        {
            "first_name": "Alice",
            "last_name": "Heart",
            "specialty": "Cardiology",
            "description": (
                "Board-certified cardiologist with a focus on preventive heart "
                "care, hypertension, and arrhythmia management."
            ),
        },
        {
            "first_name": "Ben",
            "last_name": "Dermis",
            "specialty": "Dermatology",
            "description": (
                "Specializes in medical and cosmetic dermatology, including "
                "eczema, psoriasis, and acne treatment."
            ),
        },
        {
            "first_name": "Carla",
            "last_name": "Neuro",
            "specialty": "Neurology",
            "description": (
                "Neurologist experienced in migraine, epilepsy, and peripheral "
                "neuropathy diagnosis and care."
            ),
        },
        {
            "first_name": "Daniel",
            "last_name": "Ortho",
            "specialty": "Orthopedics",
            "description": (
                "Orthopedic surgeon focusing on sports injuries, joint "
                "replacement, and fracture management."
            ),
        },
    ]

    # Each patient gets one medication. Frequencies are spread across the set
    # so all four types are exercised. Values are realistic; patient4 is kept
    # one unit above their refill threshold so a single "taken" intake will
    # cross it and fire the refill reminder.
    PATIENTS = [
        {
            "first_name": "Peggy",
            "last_name": "One",
            "phone": "09120000001",
            "medications": [
                {
                    "name": "Metformin",
                    "unit": "tablet",
                    "dosage": "500mg with dinner",
                    "frequency_type": MedicationSchedule.FrequencyType.DAILY,
                    "medication_times": ["08:00", "20:00"],
                    "start_date_offset": 0,
                    "current_inventory": 60,
                    "refill_reminder_threshold": 10,
                }
            ],
        },
        {
            "first_name": "Percy",
            "last_name": "Two",
            "phone": "09120000002",
            "medications": [
                {
                    "name": "Vitamin D3",
                    "unit": "capsule",
                    "dosage": "1000 IU",
                    "frequency_type": MedicationSchedule.FrequencyType.SPECIFIC_DAYS,
                    "medication_times": ["09:00"],
                    "specific_days": ["sat", "mon", "wed"],
                    "start_date_offset": 0,
                    "current_inventory": 20,
                    "refill_reminder_threshold": 5,
                }
            ],
        },
        {
            "first_name": "Petra",
            "last_name": "Three",
            "phone": "09120000003",
            "medications": [
                {
                    "name": "Amoxicillin",
                    "unit": "capsule",
                    "dosage": "500mg",
                    "frequency_type": MedicationSchedule.FrequencyType.EVERY_N_DAYS,
                    "medication_times": ["07:00", "19:00"],
                    "n_days_interval": 2,
                    "start_date_offset": 0,
                    "current_inventory": 14,
                    "refill_reminder_threshold": 4,
                }
            ],
        },
        {
            "first_name": "Pete",
            "last_name": "Four",
            "phone": "09120000004",
            "medications": [
                {
                    "name": "Ibuprofen",
                    "unit": "tablet",
                    "dosage": "200mg",
                    "frequency_type": MedicationSchedule.FrequencyType.EVERY_X_HOURS,
                    "x_hours_interval": 8,
                    "start_date_offset": 0,
                    "current_inventory": 6,
                    "refill_reminder_threshold": 5,  # one "taken" intake -> 5 <= 5 -> refill reminder fires
                }
            ],
        },
        {
            "first_name": "Polly",
            "last_name": "Five",
            "phone": "09120000005",
            "medications": [
                {
                    "name": "Lisinopril",
                    "unit": "tablet",
                    "dosage": "10mg",
                    "frequency_type": MedicationSchedule.FrequencyType.DAILY,
                    "medication_times": ["07:30"],
                    "start_date_offset": 0,
                    "end_date_offset": 14,
                    "current_inventory": 30,
                    "refill_reminder_threshold": 7,
                },
                {
                    "name": "Atorvastatin",
                    "unit": "tablet",
                    "dosage": "20mg at bedtime",
                    "frequency_type": MedicationSchedule.FrequencyType.SPECIFIC_DAYS,
                    "medication_times": ["22:00"],
                    "specific_days": ["sun", "tue", "thu"],
                    "start_date_offset": 0,
                    "current_inventory": 15,
                    "refill_reminder_threshold": 3,
                },
            ],
        },
    ]

    # -- Working hours: 09:00-13:00 -> 8 slots/day per doctor --------------
    WORK_START = datetime.time(9, 0)
    WORK_END = datetime.time(13, 0)
    SLOT_DAYS_AHEAD = 4  # today + next 3 days

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["i_know_this_is_demo_data"]:
            raise CommandError(
                "Refusing to seed demo data: DEBUG is off and "
                "--i-know-this-is-demo-data was not passed.\n"
                "This command wipes demo-scoped accounts and is meant for "
                "local/demo use only."
            )

        self.stdout.write(self.style.NOTICE("Clearing existing demo data..."))
        self._clear_demo_data()

        self.stdout.write(self.style.NOTICE("Creating demo doctors..."))
        doctors = [self._create_doctor(i, d) for i, d in enumerate(self.DOCTORS)]

        self.stdout.write(self.style.NOTICE("Generating working hours / time slots..."))
        for doctor in doctors:
            self._generate_slots(doctor)

        self.stdout.write(self.style.NOTICE("Creating demo patients + medications + intakes..."))
        patients = [self._create_patient(i, p) for i, p in enumerate(self.PATIENTS)]

        self.stdout.write(self.style.NOTICE("Booking appointments..."))
        bookings = self._book_appointments(patients, doctors)

        self._print_summary(doctors, patients, bookings)

    # -- Idempotent cleanup -----------------------------------------------

    def _clear_demo_data(self):
        """Remove all previously seeded demo users (cascades to their data)."""
        demo_qs = User.objects.filter(email__endswith=f"@{self.EMAIL_DOMAIN}")
        count = demo_qs.count()
        if count:
            demo_qs.delete()
            self.stdout.write(f"  Deleted {count} existing demo account(s).")
        else:
            self.stdout.write("  No existing demo accounts found.")

    def _mark_email_verified(self, user):
        EmailAddress.objects.get_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )

    # -- Doctors ----------------------------------------------------------

    def _create_doctor(self, index, data):
        email = f"doctor{index + 1}@{self.EMAIL_DOMAIN}"
        user = User.objects.create_user(
            email=email,
            password=self.PASSWORD,
            first_name=data["first_name"],
            last_name=data["last_name"],
            user_type=User.UserType.DOCTOR,
        )
        self._mark_email_verified(user)
        doctor = Doctor.objects.create(
            user=user,
            specialty=data["specialty"],
            description=data["description"],
            is_verified=True,
        )
        self.stdout.write(f"  Dr. {user.get_full_name()} ({data['specialty']})")
        return doctor

    def _generate_slots(self, doctor):
        today = timezone.now().date()
        total_created = 0
        for day_offset in range(self.SLOT_DAYS_AHEAD):
            slot_date = today + datetime.timedelta(days=day_offset)
            created, dropped = generate_time_slots(
                doctor, slot_date, self.WORK_START, self.WORK_END
            )
            total_created += len(created)
        self.stdout.write(
            f"  Dr. {doctor.user.get_full_name()}: {total_created} slots across "
            f"{self.SLOT_DAYS_AHEAD} day(s)."
        )

    # -- Patients & medications -------------------------------------------

    def _create_patient(self, index, data):
        email = f"patient{index + 1}@{self.EMAIL_DOMAIN}"
        user = User.objects.create_user(
            email=email,
            password=self.PASSWORD,
            first_name=data["first_name"],
            last_name=data["last_name"],
            user_type=User.UserType.PATIENT,
        )
        self._mark_email_verified(user)
        patient = Patient.objects.create(user=user, phone_number=data.get("phone", ""))
        self.stdout.write(f"  {user.get_full_name()}")

        for med_data in data["medications"]:
            self._create_medication(patient, med_data)

        return patient

    def _create_medication(self, patient, data):
        today = timezone.now().date()
        start_date = today + datetime.timedelta(days=data.get("start_date_offset", 0))
        end_date = None
        if "end_date_offset" in data:
            end_date = today + datetime.timedelta(days=data["end_date_offset"])

        medication = Medication.objects.create(
            patient=patient,
            name=data["name"],
            unit=data["unit"],
            dosage=data.get("dosage", ""),
            current_inventory=data.get("current_inventory", 0),
            refill_reminder_threshold=data.get("refill_reminder_threshold"),
            is_active=True,
        )

        schedule = MedicationSchedule(
            medication=medication,
            frequency_type=data["frequency_type"],
            medication_times=data.get("medication_times", []),
            specific_days=data.get("specific_days", []),
            n_days_interval=data.get("n_days_interval"),
            x_hours_interval=data.get("x_hours_interval"),
            start_date=start_date,
            end_date=end_date,
        )

        if data["frequency_type"] == MedicationSchedule.FrequencyType.EVERY_X_HOURS:
            anchor_start = datetime.datetime.combine(
                today + datetime.timedelta(days=1),
                datetime.time(8, 0),
            )
            schedule.anchor_datetime = timezone.make_aware(
                anchor_start, timezone.get_current_timezone()
            )

        schedule.save()

        _generate_intakes_for_schedule(schedule, horizon_days=7)

        intake_count = MedicationIntake.objects.filter(medication=medication).count()
        self.stdout.write(
            f"    - {data['name']} ({data['frequency_type']}): "
            f"{intake_count} intakes generated"
        )

        return medication, schedule

    # -- Appointments -----------------------------------------------------

    def _book_appointments(self, patients, doctors):
        """Book one upcoming, non-overlapping slot per patient (round-robin
        across doctors). Skips gracefully if no slot is available.
        """
        today = timezone.now().date()
        # Start from tomorrow so booked appointments are always in the future
        # and clearly appear on dashboards as "upcoming".
        min_date = today + datetime.timedelta(days=1)

        bookings = []
        for index, patient in enumerate(patients):
            doctor = doctors[index % len(doctors)]
            slot = (
                TimeSlot.objects.filter(
                    doctor=doctor,
                    is_booked=False,
                    date__gte=min_date,
                )
                .order_by("date", "start_time")
                .first()
            )
            if slot is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"  No available slot for {patient.user.get_short_name()} "
                        f"(Dr. {doctor.user.get_full_name()}) — skipped."
                    )
                )
                continue

            try:
                appointment = book_appointment(slot, patient)
            except SlotAlreadyBooked:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Race: slot {slot} was just booked, skipping "
                        f"{patient.user.get_short_name()}."
                    )
                )
                continue

            bookings.append((patient, appointment))
            self.stdout.write(
                f"  {patient.user.get_short_name()} -> Dr. "
                f"{doctor.user.get_full_name()} @ {slot.date} {slot.start_time:%H:%M}"
            )
        return bookings

    # -- Summary ----------------------------------------------------------

    def _print_summary(self, doctors, patients, bookings):
        width = 60
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * width))
        self.stdout.write(self.style.SUCCESS("  DEMO DATA SEEDED SUCCESSFULLY"))
        self.stdout.write(self.style.SUCCESS("=" * width))

        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("DOCTORS"))
        self.stdout.write(f"  Password for all demo accounts: {self.PASSWORD}")
        for i, doctor in enumerate(doctors, start=1):
            email = f"doctor{i}@{self.EMAIL_DOMAIN}"
            self.stdout.write(
                f"  Dr. {doctor.user.get_full_name():<20} {email:<36} "
                f"{doctor.specialty}"
            )

        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("PATIENTS"))
        for i, patient in enumerate(patients, start=1):
            email = f"patient{i}@{self.EMAIL_DOMAIN}"
            meds = Medication.objects.filter(patient=patient)
            med_lines = []
            for med in meds:
                freq = med.schedules.first()
                freq_label = freq.get_frequency_type_display() if freq else "n/a"
                med_lines.append(f"{med.name} ({freq_label})")
            upcoming = (
                patient.appointments.filter(status="active")
                .order_by("time_slot__date", "time_slot__start_time")
                .first()
            )
            appt_str = (
                f"next: Dr. {upcoming.time_slot.doctor.user.last_name} "
                f"@ {upcoming.time_slot.date} {upcoming.time_slot.start_time:%H:%M}"
                if upcoming else "no appointment"
            )
            self.stdout.write(f"  {patient.user.get_full_name():<20} {email}")
            for line in med_lines:
                self.stdout.write(f"      meds: {line}")
            self.stdout.write(f"      {appt_str}")

        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("APPOINTMENTS BOOKED"))
        self.stdout.write(f"  {len(bookings)} appointment(s) created.")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "Reminder: these are demo accounts on @demo.myclinic.test. "
                "Do not use in production."
            )
        )
