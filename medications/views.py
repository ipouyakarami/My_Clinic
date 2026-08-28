import datetime
import json
import uuid

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.mail import send_mail
from django.db import transaction
from django.http import JsonResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, TemplateView

from accounts.models import Doctor, Patient, User
from appointments.models import Appointment

from .forms import (
    MedicationDeleteForm,
    MedicationIntakeActionForm,
    MedicationStep1Form,
    MedicationStep2Form,
    MedicationStep3Form,
    MedicationStep4Form,
    MedicationStep5Form,
    MedicationStep6Form,
)
from .models import Medication, MedicationIntake, MedicationSchedule


class PatientRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.user_type == User.UserType.PATIENT

    def handle_no_permission(self):
        if self.request.user.is_authenticated and self.request.user.user_type == User.UserType.DOCTOR:
            messages.info(self.request, "Doctors can manage patient medications from the appointment details page.")
            return redirect("appointments:doctor_appointment_list")
        return super().handle_no_permission()


class DoctorRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.user_type == User.UserType.DOCTOR


class AppointmentDoctorRequiredMixin(DoctorRequiredMixin):
    def get_appointment(self):
        return get_object_or_404(
            Appointment,
            pk=self.kwargs["appointment_pk"],
            time_slot__doctor__user=self.request.user,
        )


def _generate_intakes_for_schedule(schedule, horizon_days=7):
    from .tasks import send_refill_reminder_email

    now = timezone.now()
    tz = timezone.get_current_timezone()
    today = now.date()
    horizon = today + datetime.timedelta(days=horizon_days)

    anchor = schedule.anchor_datetime
    times = schedule.medication_times or []

    intakes = []
    medication = schedule.medication

    def make_intake(dt):
        return MedicationIntake(
            medication=medication,
            scheduled_time=dt,
            status=MedicationIntake.Status.PENDING,
            reminder_sent=False,
        )

    if schedule.frequency_type == MedicationSchedule.FrequencyType.DAILY:
        if not times:
            return []
        d = schedule.start_date or today
        end = schedule.end_date or horizon
        while d <= end and d <= horizon:
            for t in times:
                hh, mm = map(int, t.split(":"))
                dt = datetime.datetime.combine(d, datetime.time(hh, mm))
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
                intakes.append(make_intake(dt))
            d += datetime.timedelta(days=1)

    elif schedule.frequency_type == MedicationSchedule.FrequencyType.SPECIFIC_DAYS:
        if not times:
            return []
        d = schedule.start_date or today
        end = schedule.end_date or horizon
        day_map = {
            "sat": 5, "sun": 6, "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4,
        }
        selected_days = set(schedule.specific_days or [])
        while d <= end and d <= horizon:
            if d.weekday() in [day_map[day] for day in selected_days if day in day_map]:
                for t in times:
                    hh, mm = map(int, t.split(":"))
                    dt = datetime.datetime.combine(d, datetime.time(hh, mm))
                    dt = timezone.make_aware(dt, timezone.get_current_timezone())
                    intakes.append(make_intake(dt))
            d += datetime.timedelta(days=1)

    elif schedule.frequency_type == MedicationSchedule.FrequencyType.EVERY_N_DAYS:
        if not times:
            return []
        d = schedule.start_date or today
        end = schedule.end_date or horizon
        n = schedule.n_days_interval or 1
        while d <= end and d <= horizon:
            for t in times:
                hh, mm = map(int, t.split(":"))
                dt = datetime.datetime.combine(d, datetime.time(hh, mm))
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
                intakes.append(make_intake(dt))
            d += datetime.timedelta(days=n)

    elif schedule.frequency_type == MedicationSchedule.FrequencyType.EVERY_X_HOURS:
        if not anchor:
            return []
        x = schedule.x_hours_interval or 1
        end_dt = None
        if schedule.end_date:
            end_dt = datetime.datetime.combine(schedule.end_date, datetime.time(23, 59, 59))
            end_dt = timezone.make_aware(end_dt, timezone.get_current_timezone())
        else:
            end_dt = now + datetime.timedelta(days=horizon_days)
        current = anchor
        while current <= end_dt and current.date() <= horizon:
            intakes.append(make_intake(current))
            current += datetime.timedelta(hours=x)

    MedicationIntake.objects.bulk_create(intakes, ignore_conflicts=True)
    return intakes


class MedicationListView(PatientRequiredMixin, ListView):
    template_name = "medications/medication_list.html"
    context_object_name = "medications"

    def get_queryset(self):
        return Medication.objects.filter(
            patient__user=self.request.user,
            is_active=True,
        ).prefetch_related("schedules")


class MedicationWizardView(PatientRequiredMixin, View):
    def _get_patient(self, user):
        return get_object_or_404(Patient, user=user)

    def _get_medication(self, patient, pk):
        return get_object_or_404(Medication, pk=pk, patient=patient)

    def _session_key(self):
        return f"medication_wizard_{self.request.user.id}"

    def _clear_session(self):
        self.request.session.pop(self._session_key(), None)

    def _get_session_data(self):
        return self.request.session.get(self._session_key(), {})

    def _set_session_data(self, data):
        self.request.session[self._session_key()] = data
        self.request.session.modified = True

    @staticmethod
    def _parse_session_date(value):
        if not value:
            return None
        if hasattr(value, "year"):
            return value
        try:
            parts = value.split("/")
            return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, TypeError, IndexError, AttributeError):
            return None

    def _get_step_form_class(self, step):
        forms = {
            1: MedicationStep1Form,
            2: MedicationStep2Form,
            3: MedicationStep3Form,
            4: MedicationStep4Form,
            5: MedicationStep5Form,
            6: MedicationStep6Form,
        }
        return forms.get(step)

    def _build_context(self, step, form=None, medication=None, schedule=None):
        context = {
            "step": step,
            "form": form,
            "medication": medication,
            "schedule": schedule,
            "total_steps": 6,
            "frequency_type": self._get_session_data().get("frequency_type", ""),
        }
        if medication and schedule:
            context["review_data"] = self._build_review_data(medication, schedule)
        elif step == 6:
            context["review_data"] = self._build_review_data_from_session()
        return context

    def _build_review_data_from_session(self):
        session_data = self._get_session_data()
        freq = session_data.get("frequency_type", "")
        freq_display = dict(MedicationSchedule.FrequencyType.choices).get(freq, "")
        anchor_date = session_data.get("anchor_date", "")
        anchor_time = session_data.get("anchor_time", "")
        anchor_datetime = None
        if anchor_date and anchor_time:
            anchor_datetime = f"{anchor_date} {anchor_time}"
        medication_times = []
        if freq != "every_x_hours":
            medication_times = session_data.get("medication_times", [])
        return {
            "name": session_data.get("name", ""),
            "unit": session_data.get("unit", ""),
            "dosage": session_data.get("dosage", ""),
            "frequency_type": freq_display,
            "medication_times": medication_times,
            "specific_days": session_data.get("specific_days", []),
            "n_days_interval": session_data.get("n_days_interval"),
            "x_hours_interval": session_data.get("x_hours_interval"),
            "anchor_datetime": anchor_datetime,
            "start_date": session_data.get("start_date", ""),
            "end_date": session_data.get("end_date", ""),
            "current_inventory": session_data.get("current_inventory", 0),
            "refill_reminder_threshold": session_data.get("refill_reminder_threshold"),
        }

    def _build_review_data(self, medication, schedule):
        anchor_datetime = None
        if schedule.anchor_datetime:
            anchor_date = schedule.anchor_datetime.date().strftime("%Y/%m/%d")
            anchor_time = schedule.anchor_datetime.strftime("%H:%M")
            anchor_datetime = f"{anchor_date} {anchor_time}"
        start_date = ""
        if schedule.start_date:
            start_date = schedule.start_date.strftime("%Y/%m/%d")
        end_date = ""
        if schedule.end_date:
            end_date = schedule.end_date.strftime("%Y/%m/%d")
        return {
            "name": medication.name,
            "unit": medication.unit,
            "dosage": medication.dosage,
            "frequency_type": schedule.get_frequency_type_display(),
            "medication_times": schedule.medication_times,
            "specific_days": schedule.specific_days,
            "n_days_interval": schedule.n_days_interval,
            "x_hours_interval": schedule.x_hours_interval,
            "anchor_datetime": anchor_datetime,
            "start_date": start_date,
            "end_date": end_date,
            "current_inventory": medication.current_inventory,
            "refill_reminder_threshold": medication.refill_reminder_threshold,
        }

    def _save_medication_and_schedule(self, session_data, patient):
        is_edit = session_data.get("medication_pk")
        if is_edit:
            medication = self._get_medication(patient, is_edit)
            schedule = medication.schedules.first()
            if not schedule:
                schedule = MedicationSchedule(medication=medication)
        else:
            medication = Medication(patient=patient)
            schedule = MedicationSchedule()

        medication.name = session_data["name"]
        medication.unit = session_data["unit"]
        medication.dosage = session_data.get("dosage", "")
        medication.current_inventory = session_data.get("current_inventory", 0)
        medication.refill_reminder_threshold = session_data.get("refill_reminder_threshold")
        medication.is_active = True
        medication.save()

        schedule.medication = medication
        schedule.frequency_type = session_data["frequency_type"]
        schedule.medication_times = session_data.get("medication_times", [])
        schedule.specific_days = session_data.get("specific_days", [])
        schedule.n_days_interval = session_data.get("n_days_interval")
        schedule.x_hours_interval = session_data.get("x_hours_interval")

        anchor_date = session_data.get("anchor_date")
        anchor_time = session_data.get("anchor_time")
        if anchor_date and anchor_time:
            anchor_date_obj = self._parse_session_date(anchor_date)
            if anchor_date_obj:
                hh, mm = map(int, anchor_time.split(":"))
                schedule.anchor_datetime = timezone.make_aware(
                    datetime.datetime.combine(anchor_date_obj, datetime.time(hh, mm)),
                    timezone.get_current_timezone(),
                )
            else:
                schedule.anchor_datetime = None
        else:
            schedule.anchor_datetime = None

        schedule.start_date = self._parse_session_date(session_data.get("start_date"))
        schedule.end_date = self._parse_session_date(session_data.get("end_date"))

        schedule.save()

        if is_edit:
            MedicationIntake.objects.filter(
                medication=medication,
                status=MedicationIntake.Status.PENDING,
                scheduled_time__gte=timezone.now(),
            ).delete()

        _generate_intakes_for_schedule(schedule)
        return medication, schedule

    def get(self, request, pk=None):
        patient = self._get_patient(request.user)
        step = int(request.GET.get("step", 1))
        session_data = self._get_session_data()
        medication = None
        schedule = None

        if pk:
            medication = self._get_medication(patient, pk)
            schedule = medication.schedules.first()
            if not session_data:
                session_data = {
                    "medication_pk": medication.pk,
                    "name": medication.name,
                    "unit": medication.unit,
                    "dosage": medication.dosage,
                    "current_inventory": medication.current_inventory,
                    "refill_reminder_threshold": medication.refill_reminder_threshold,
                    "frequency_type": schedule.frequency_type if schedule else "",
                    "medication_times": schedule.medication_times if schedule else [],
                    "specific_days": schedule.specific_days if schedule else [],
                    "n_days_interval": schedule.n_days_interval if schedule else None,
                    "x_hours_interval": schedule.x_hours_interval if schedule else None,
                    "start_date": schedule.start_date.strftime("%Y/%m/%d") if schedule and schedule.start_date else "",
                    "end_date": schedule.end_date.strftime("%Y/%m/%d") if schedule and schedule.end_date else "",
                }
                if schedule and schedule.anchor_datetime:
                    session_data["anchor_date"] = schedule.anchor_datetime.date().strftime("%Y/%m/%d")
                    session_data["anchor_time"] = schedule.anchor_datetime.strftime("%H:%M")
                self._set_session_data(session_data)

        form_class = self._get_step_form_class(step)
        form = None
        if form_class:
            initial = self._build_initial_for_step(step, session_data)
            form = form_class(initial=initial, data=None)

        context = self._build_context(step, form=form, medication=medication, schedule=schedule)
        context["editing"] = bool(pk)
        return render(request, "medications/medication_form.html", context)

    def _build_initial_for_step(self, step, session_data):
        if step == 1:
            return {"name": session_data.get("name", ""), "unit": session_data.get("unit", "")}
        if step == 2:
            return {"frequency_type": session_data.get("frequency_type", "")}
        if step == 3:
            times_str = "\n".join(session_data.get("medication_times", []))
            return {
                "medication_times": times_str,
                "medication_times_specific": times_str,
                "medication_times_n": times_str,
                "specific_days": session_data.get("specific_days", []),
                "n_days_interval": session_data.get("n_days_interval", ""),
                "x_hours_interval": session_data.get("x_hours_interval", ""),
                "anchor_date": session_data.get("anchor_date", ""),
                "anchor_time": session_data.get("anchor_time", ""),
                "start_date": session_data.get("start_date", ""),
                "start_date_specific": session_data.get("start_date", ""),
                "start_date_n": session_data.get("start_date", ""),
                "end_date": session_data.get("end_date", ""),
                "end_date_specific": session_data.get("end_date", ""),
                "end_date_n": session_data.get("end_date", ""),
                "end_date_x": session_data.get("end_date", ""),
            }
        if step == 4:
            return {"dosage": session_data.get("dosage", "")}
        if step == 5:
            return {
                "current_inventory": session_data.get("current_inventory", 0),
                "refill_reminder_threshold": session_data.get("refill_reminder_threshold", ""),
            }
        return {}

    def _next_step_url(self, next_step):
        return f"{reverse('medications:medication_add')}?step={next_step}"

    def _list_url(self):
        return reverse("medications:medication_list")

    def post(self, request, pk=None):
        step = int(request.POST.get("step", 1))
        session_data = self._get_session_data()
        form_class = self._get_step_form_class(step)

        existing_schedule = None
        if pk:
            patient = self._get_patient(request.user)
            medication = self._get_medication(patient, pk)
            existing_schedule = medication.schedules.first()

        if step == 3:
            form = form_class(request.POST, existing_schedule=existing_schedule)
        else:
            form = form_class(request.POST)

        if form.is_valid():
            if step == 1:
                session_data.update({
                    "name": form.cleaned_data["name"],
                    "unit": form.cleaned_data["unit"],
                })
            elif step == 2:
                session_data["frequency_type"] = form.cleaned_data["frequency_type"]
            elif step == 3:
                start_date = form.cleaned_data.get("start_date") or form.cleaned_data.get("start_date_specific") or form.cleaned_data.get("start_date_n")
                end_date = form.cleaned_data.get("end_date") or form.cleaned_data.get("end_date_specific") or form.cleaned_data.get("end_date_n") or form.cleaned_data.get("end_date_x")
                anchor_date = form.cleaned_data.get("anchor_date")
                session_data.update({
                    "medication_times": form.cleaned_data.get("medication_times", []),
                    "specific_days": form.cleaned_data.get("specific_days", []),
                    "n_days_interval": form.cleaned_data.get("n_days_interval"),
                    "x_hours_interval": form.cleaned_data.get("x_hours_interval"),
                    "anchor_date": anchor_date.strftime("%Y/%m/%d") if anchor_date else None,
                    "anchor_time": form.cleaned_data.get("anchor_time"),
                    "start_date": start_date.strftime("%Y/%m/%d") if start_date else None,
                    "end_date": end_date.strftime("%Y/%m/%d") if end_date else None,
                })
            elif step == 4:
                session_data["dosage"] = form.cleaned_data["dosage"]
            elif step == 5:
                session_data["current_inventory"] = form.cleaned_data["current_inventory"]
                session_data["refill_reminder_threshold"] = form.cleaned_data["refill_reminder_threshold"]
            elif step == 6:
                patient = self._get_patient(request.user)
                medication, schedule = self._save_medication_and_schedule(session_data, patient)
                self._clear_session()
                messages.success(request, "Medication saved successfully.")
                return redirect(self._list_url())

            self._set_session_data(session_data)
            next_step = step + 1
            return redirect(self._next_step_url(next_step))

        patient = self._get_patient(request.user)
        medication = None
        schedule = None
        if pk:
            medication = self._get_medication(patient, pk)
            schedule = medication.schedules.first()
        context = self._build_context(step, form=form, medication=medication, schedule=schedule)
        context["editing"] = bool(pk)
        return render(request, "medications/medication_form.html", context)


class MedicationDeleteView(PatientRequiredMixin, View):
    def get(self, request, pk):
        patient = get_object_or_404(Patient, user=request.user)
        medication = get_object_or_404(Medication, pk=pk, patient=patient)
        return render(
            request,
            "medications/medication_confirm_delete.html",
            {"medication": medication},
        )

    def post(self, request, pk):
        patient = get_object_or_404(Patient, user=request.user)
        medication = get_object_or_404(Medication, pk=pk, patient=patient)
        form = MedicationDeleteForm(request.POST)
        if form.is_valid():
            MedicationIntake.objects.filter(
                medication=medication,
                status=MedicationIntake.Status.PENDING,
                scheduled_time__gte=timezone.now(),
            ).delete()
            medication.delete()
            messages.success(request, "Medication deleted successfully.")
            return redirect("medications:medication_list")
        return render(
            request,
            "medications/medication_confirm_delete.html",
            {"medication": medication},
        )


class MedicationIntakeActionView(View):
    def get(self, request, token, action):
        intake = get_object_or_404(MedicationIntake, token=token)
        if not request.user.is_authenticated:
            return redirect(f"{reverse('account_login')}?next={request.path}")
        if intake.medication.patient.user != request.user:
            messages.error(request, "You are not allowed to perform this action.")
            return redirect("accounts:dashboard")

        if action == "taken":
            if intake.status == MedicationIntake.Status.PENDING:
                intake.status = MedicationIntake.Status.TAKEN
                intake.recorded_at = timezone.now()
                intake.save(update_fields=["status", "recorded_at"])
                from .tasks import handle_intake_taken
                handle_intake_taken(intake.pk)
            messages.success(request, "Medication intake recorded.")
        elif action == "skipped":
            if intake.status == MedicationIntake.Status.PENDING:
                intake.status = MedicationIntake.Status.SKIPPED
                intake.recorded_at = timezone.now()
                intake.save(update_fields=["status", "recorded_at"])
            messages.success(request, "Medication intake skipped.")
        return redirect("accounts:dashboard")


class MedicationIntakeBatchView(LoginRequiredMixin, View):
    def post(self, request):
        if request.user.user_type != User.UserType.PATIENT:
            messages.error(request, "You are not allowed to perform this action.")
            return redirect("accounts:dashboard")

        scheduled_time_str = request.POST.get("scheduled_time")
        if not scheduled_time_str:
            messages.error(request, "Missing scheduled time.")
            return redirect("accounts:dashboard")

        scheduled_time = self._parse_scheduled_time(scheduled_time_str)
        if scheduled_time is None:
            messages.error(request, "Invalid scheduled time.")
            return redirect("accounts:dashboard")

        intakes = MedicationIntake.objects.filter(
            medication__patient__user=request.user,
            scheduled_time=scheduled_time,
            status=MedicationIntake.Status.PENDING,
        )

        count = 0
        for intake in intakes:
            if intake.status == MedicationIntake.Status.PENDING:
                intake.status = MedicationIntake.Status.TAKEN
                intake.recorded_at = timezone.now()
                intake.save(update_fields=["status", "recorded_at"])
                from .tasks import handle_intake_taken
                handle_intake_taken(intake.pk)
                count += 1

        if count:
            messages.success(request, f"{count} medication(s) marked as taken.")
        else:
            messages.info(request, "No pending medications to mark as taken.")

        return redirect("accounts:dashboard")

    @staticmethod
    def _parse_scheduled_time(value):
        try:
            dt = datetime.datetime.fromisoformat(value)
        except ValueError:
            try:
                dt = datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt


class MedicationCalendarView(PatientRequiredMixin, TemplateView):
    template_name = "medications/medication_calendar.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        date_str = self.request.GET.get("date") or self.kwargs.get("date")
        selected_date = None
        intakes = []
        intakes_by_time = []
        date_display = None
        date_error = None
        if date_str:
            try:
                parts = date_str.split("/")
                if len(parts) != 3:
                    raise ValueError("invalid format")
                y, m, d = map(int, parts)
                from datetime import date
                selected_date = date(y, m, d)
                date_display = selected_date.strftime("%B %d, %Y")
                context["year"] = y
                context["month"] = m
                context["day"] = d
                tz = timezone.get_current_timezone()
                start_dt = timezone.make_aware(
                    datetime.datetime.combine(selected_date, datetime.time(0, 0)),
                    tz,
                )
                end_dt = start_dt + datetime.timedelta(days=1)
                intakes = MedicationIntake.objects.filter(
                    medication__patient__user=self.request.user,
                    scheduled_time__gte=start_dt,
                    scheduled_time__lt=end_dt,
                ).select_related("medication").order_by("scheduled_time")
                intakes_by_time = self._group_intakes_by_time(intakes)
            except (ValueError, TypeError):
                date_error = "Invalid date. Please select a valid Gregorian date."
        context["selected_date"] = date_str
        context["intakes"] = intakes
        context["intakes_by_time"] = intakes_by_time
        context["date_display"] = date_display
        context["date_error"] = date_error
        return context

    @staticmethod
    def _group_intakes_by_time(intakes):
        from itertools import groupby
        groups = []
        local_tz = timezone.get_current_timezone()
        for time_key, group in groupby(
            intakes,
            key=lambda i: timezone.localtime(i.scheduled_time, local_tz).strftime("%H:%M"),
        ):
            groups.append((time_key, list(group)))
        return groups


class DoctorMedicationListView(AppointmentDoctorRequiredMixin, ListView):
    template_name = "medications/doctor_medication_list.html"
    context_object_name = "medications"

    def get_appointment(self):
        return get_object_or_404(
            Appointment,
            pk=self.kwargs["appointment_pk"],
            time_slot__doctor__user=self.request.user,
        )

    def get_queryset(self):
        appointment = self.get_appointment()
        return Medication.objects.filter(
            patient=appointment.patient,
            is_active=True,
        ).prefetch_related("schedules")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["appointment"] = self.get_appointment()
        return context


class DoctorMedicationWizardView(AppointmentDoctorRequiredMixin, MedicationWizardView):
    def _get_patient(self, user):
        appointment = self.get_appointment()
        return appointment.patient

    def _next_step_url(self, next_step):
        appointment_pk = self.kwargs.get("appointment_pk")
        if self._is_edit():
            pk = self.kwargs.get("pk")
            return f"{reverse('medications:doctor_medication_edit', args=[appointment_pk, pk])}?step={next_step}"
        return f"{reverse('medications:doctor_medication_add', args=[appointment_pk])}?step={next_step}"

    def _list_url(self):
        appointment_pk = self.kwargs.get("appointment_pk")
        return reverse("medications:doctor_medication_list", args=[appointment_pk])

    def _is_edit(self):
        return bool(self.kwargs.get("pk"))

    def post(self, request, appointment_pk, pk=None):
        self.kwargs["appointment_pk"] = appointment_pk
        self.kwargs["pk"] = pk
        return super().post(request, pk=pk)

    def get(self, request, appointment_pk, pk=None):
        self.kwargs["appointment_pk"] = appointment_pk
        self.kwargs["pk"] = pk
        return super().get(request, pk=pk)


class DoctorMedicationDeleteView(AppointmentDoctorRequiredMixin, View):
    def get_appointment(self):
        return get_object_or_404(
            Appointment,
            pk=self.kwargs["appointment_pk"],
            time_slot__doctor__user=self.request.user,
        )

    def get(self, request, appointment_pk, pk):
        appointment = self.get_appointment()
        medication = get_object_or_404(Medication, pk=pk, patient=appointment.patient)
        return render(
            request,
            "medications/medication_confirm_delete.html",
            {"medication": medication, "appointment": appointment},
        )

    def post(self, request, appointment_pk, pk):
        appointment = self.get_appointment()
        medication = get_object_or_404(Medication, pk=pk, patient=appointment.patient)
        MedicationIntake.objects.filter(
            medication=medication,
            status=MedicationIntake.Status.PENDING,
            scheduled_time__gte=timezone.now(),
        ).delete()
        medication.delete()
        messages.success(request, "Medication deleted successfully.")
        return redirect("medications:doctor_medication_list", appointment_pk=appointment.pk)
