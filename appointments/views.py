import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django import forms
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, FormView, ListView, View

from accounts.models import Doctor, Patient, User

from medical_tests.models import MedicalTestResult

from .forms import GregorianDateField, VisitSummaryForm, WorkingHoursForm
from .models import Appointment, TimeSlot
from .services import book_appointment, generate_time_slots, SlotAlreadyBooked


class DoctorRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.user_type == User.UserType.DOCTOR


class PatientRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.user_type == User.UserType.PATIENT


class WorkingHoursCreateView(DoctorRequiredMixin, FormView):
    template_name = "appointments/working_hours_create.html"
    form_class = WorkingHoursForm

    def _cleanup_expired_slots(self, doctor, exclude_pks=None):
        now = timezone.now()
        today = timezone.localdate()
        expired_qs = TimeSlot.objects.filter(
            doctor=doctor,
            date__lte=today,
            is_booked=False,
        )
        if exclude_pks:
            expired_qs = expired_qs.exclude(pk__in=exclude_pks)
        expired_count = 0
        for slot in expired_qs:
            if slot.start_datetime <= now:
                slot.delete()
                expired_count += 1
        return expired_count

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        doctor = self.request.user.doctor
        self._cleanup_expired_slots(doctor)
        context["doctor"] = doctor

        filter_date = self.request.GET.get("date")
        slots_qs = TimeSlot.objects.filter(doctor=doctor, is_booked=False)
        if filter_date:
            slots_qs = slots_qs.filter(date=filter_date)
            context["selected_date"] = filter_date
        else:
            slots_qs = slots_qs.filter(date__gte=timezone.localdate())
            context["selected_date"] = ""
        context["upcoming_slots"] = slots_qs.order_by("date", "start_time")[:50]

        context["available_dates"] = [
            {"gregorian": d, "display": d.strftime("%Y/%m/%d")}
            for d in TimeSlot.objects.filter(doctor=doctor, is_booked=False)
            .order_by("date")
            .values_list("date", flat=True)
            .distinct()
        ]
        return context

    def form_valid(self, form):
        doctor = self.request.user.doctor
        slot_date = form.cleaned_data["date"]
        start_time_str = form.cleaned_data["start_time"]
        end_time_str = form.cleaned_data["end_time"]

        def parse_time(value):
            if isinstance(value, datetime.time):
                return value
            if isinstance(value, str):
                parts = value.split(":")
                if len(parts) == 2:
                    return datetime.time(int(parts[0]), int(parts[1]))
            raise ValueError(f"Invalid time format: {value}")

        start_time = parse_time(start_time_str)
        end_time = parse_time(end_time_str)

        overlapping_slots = TimeSlot.objects.filter(
            doctor=doctor,
            date=slot_date,
        ).filter(
            start_time__lt=end_time,
            end_time__gt=start_time,
        )

        if overlapping_slots.exists():
            form.add_error(
                "__all__",
                f"Time conflict: {overlapping_slots.count()} slot(s) on this date overlap with your selection. Please choose a different range.",
            )
            return self.form_invalid(form)

        created, dropped_minutes = generate_time_slots(
            doctor, slot_date, start_time, end_time
        )

        now = timezone.now()
        expired_count = self._cleanup_expired_slots(
            doctor,
            exclude_pks=[slot.pk for slot in created],
        )

        message = f"{len(created)} new slot(s) created."
        if dropped_minutes:
            message += f" {dropped_minutes} minute(s) could not be scheduled."
        if expired_count:
            message += f" {expired_count} expired slot(s) removed."
        messages.success(self.request, message)
        return redirect("appointments:working_hours_create")


class TimeSlotDeleteView(DoctorRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        slot = get_object_or_404(TimeSlot, pk=kwargs["pk"], doctor__user=request.user)
        if slot.is_booked:
            messages.error(request, "This slot is already booked and cannot be deleted.")
        else:
            slot.delete()
            messages.success(request, "Slot deleted successfully.")
        return redirect("appointments:working_hours_create")


class BookAppointmentView(PatientRequiredMixin, FormView):
    template_name = "appointments/book_confirm.html"
    form_class = forms.Form

    def get_slot(self):
        slot_id = self.request.GET.get("slot") or self.kwargs.get("slot_id")
        return get_object_or_404(TimeSlot, pk=slot_id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["slot"] = self.get_slot()
        return context

    def form_valid(self, form):
        slot = self.get_slot()
        patient = Patient.objects.get(user=self.request.user)
        try:
            appointment = book_appointment(slot, patient)
        except SlotAlreadyBooked:
            messages.error(self.request, "This slot has just been booked. Please choose another.")
            return redirect("core:doctor_detail", pk=slot.doctor.pk)

        messages.success(self.request, "Your appointment has been booked successfully. A confirmation email has been sent to you.")
        return redirect("appointments:patient_appointment_list")

class PatientAppointmentListView(PatientRequiredMixin, ListView):
    template_name = "appointments/patient_appointment_list.html"
    context_object_name = "appointments"

    def get_queryset(self):
        return Appointment.objects.filter(
            patient__user=self.request.user
        ).select_related("time_slot", "time_slot__doctor", "time_slot__doctor__user").order_by(
            "-time_slot__date", "-time_slot__start_time"
        )


class CancelAppointmentView(PatientRequiredMixin, FormView):
    template_name = "appointments/appointment_cancel_confirm.html"
    form_class = forms.Form

    def dispatch(self, request, *args, **kwargs):
        self.appointment = get_object_or_404(
            Appointment,
            pk=self.kwargs["pk"],
            patient__user=request.user,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["appointment"] = self.appointment
        return context

    def form_valid(self, form):
        appointment = self.appointment
        slot = appointment.time_slot
        now = timezone.now().astimezone(timezone.get_current_timezone())
        start_dt = slot.start_datetime
        if start_dt - now < timezone.timedelta(hours=12):
            messages.error(
                self.request,
                "Cancellations are only allowed up to 12 hours before the appointment. Please contact your doctor to cancel this appointment.",
            )
            return redirect("appointments:patient_appointment_list")

        with transaction.atomic():
            slot.is_booked = False
            slot.save(update_fields=["is_booked"])
            appointment.status = Appointment.Status.CANCELLED
            appointment.cancelled_at = now
            appointment.save(update_fields=["status", "cancelled_at"])

        messages.success(self.request, "Your appointment has been cancelled successfully.")
        return redirect("appointments:patient_appointment_list")


class DoctorAppointmentListView(DoctorRequiredMixin, ListView):
    template_name = "appointments/doctor_appointment_list.html"
    context_object_name = "appointments"

    def get_queryset(self):
        return Appointment.objects.filter(
            time_slot__doctor__user=self.request.user
        ).select_related("time_slot", "patient", "patient__user").order_by(
            "-time_slot__date", "-time_slot__start_time"
        )


class DoctorTodayAppointmentsView(DoctorRequiredMixin, ListView):
    template_name = "appointments/doctor_today_appointments.html"
    context_object_name = "appointments"

    def get_queryset(self):
        today = timezone.localdate()
        return Appointment.objects.filter(
            time_slot__doctor__user=self.request.user,
            time_slot__date=today,
        ).select_related("time_slot", "patient", "patient__user").order_by(
            "time_slot__start_time"
        )


class DoctorAppointmentDetailView(DoctorRequiredMixin, DetailView):
    model = Appointment
    template_name = "appointments/appointment_detail.html"
    context_object_name = "appointment"

    def get_queryset(self):
        return Appointment.objects.filter(
            time_slot__doctor__user=self.request.user
        ).select_related("time_slot", "patient", "patient__user")

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = VisitSummaryForm(request.POST)
        if form.is_valid():
            self.object.doctor_summary = form.cleaned_data["doctor_summary"]
            self.object.status = Appointment.Status.COMPLETED
            self.object.save(update_fields=["doctor_summary", "status"])
            messages.success(request, "Visit summary saved successfully.")
            return redirect("appointments:appointment_detail", pk=self.object.pk)
        messages.error(request, "Error saving visit summary.")
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = VisitSummaryForm(
            initial={"doctor_summary": self.object.doctor_summary}
        )
        context["test_categories"] = MedicalTestResult.TestCategory.choices
        selected_test_type = self.request.GET.get("test_type", "")
        context["selected_test_type"] = selected_test_type
        patient_tests = MedicalTestResult.objects.filter(
            patient=self.object.patient.user
        )
        if selected_test_type:
            patient_tests = patient_tests.filter(category=selected_test_type)
        context["patient_tests"] = patient_tests.order_by("-uploaded_at")
        return context
