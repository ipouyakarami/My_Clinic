import datetime
import jdatetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django import forms
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, FormView, ListView, View

from accounts.models import Doctor, Patient, User

from .forms import JalaliDateField, VisitSummaryForm, WorkingHoursForm
from .models import Appointment, TimeSlot


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
        today = now.date()
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
            slots_qs = slots_qs.filter(date__gte=timezone.now().date())
            context["selected_date"] = ""
        context["upcoming_slots"] = slots_qs.order_by("date", "start_time")[:50]

        context["available_dates"] = [
            {
                "gregorian": d,
                "jalali": jdatetime.date.fromgregorian(date=d).strftime("%Y/%m/%d"),
            }
            for d in TimeSlot.objects.filter(doctor=doctor, is_booked=False)
            .order_by("date")
            .values_list("date", flat=True)
            .distinct()
        ]
        return context

    def form_valid(self, form):
        doctor = self.request.user.doctor
        gregorian_date = form.cleaned_data["jalali_date"]
        start_time = form.cleaned_data["start_time"]
        end_time = form.cleaned_data["end_time"]

        overlapping_slots = TimeSlot.objects.filter(
            doctor=doctor,
            date=gregorian_date,
        ).filter(
            start_time__lt=end_time,
            end_time__gt=start_time,
        )

        if overlapping_slots.exists():
            form.add_error(
                "__all__",
                f"تعارض زمانی: در این تاریخ {overlapping_slots.count()} نوبت با بازه انتخابی شما همپوشانی دارد. لطفاً بازه دیگری انتخاب کنید.",
            )
            return self.form_invalid(form)

        existing_slots = TimeSlot.objects.filter(
            doctor=doctor,
            date=gregorian_date,
            start_time__gte=start_time,
            start_time__lt=end_time,
        )
        existing_starts = set(existing_slots.values_list("start_time", flat=True))

        created = []
        dropped_minutes = 0
        current = datetime.datetime.combine(gregorian_date, start_time)
        end_dt = datetime.datetime.combine(gregorian_date, end_time)

        while current + datetime.timedelta(minutes=30) <= end_dt:
            slot_start = current.time()
            if slot_start not in existing_starts:
                slot = TimeSlot.objects.create(
                    doctor=doctor,
                    date=gregorian_date,
                    start_time=slot_start,
                    end_time=(current + datetime.timedelta(minutes=30)).time(),
                )
                created.append(slot)
            current += datetime.timedelta(minutes=30)

        if current < end_dt:
            dropped_minutes = int((end_dt - current).total_seconds() // 60)

        now = timezone.now()
        expired_count = self._cleanup_expired_slots(
            doctor,
            exclude_pks=[slot.pk for slot in created],
        )

        message = f"{len(created)} نوبت جدید ایجاد شد."
        if dropped_minutes:
            message += f" {dropped_minutes} دقیقه باقی‌مانده به دلیل عدم تقسیم‌پذیری بر ۳۰ دقیقه حذف شد."
        if expired_count:
            message += f" {expired_count} نوبت منقضی حذف شد."
        messages.success(self.request, message)
        return redirect("appointments:working_hours_create")


class TimeSlotDeleteView(DoctorRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        slot = get_object_or_404(TimeSlot, pk=kwargs["pk"], doctor__user=request.user)
        if slot.is_booked:
            messages.error(request, "این نوبت قبلاً رزرو شده و قابل حذف نیست.")
        else:
            slot.delete()
            messages.success(request, "نوبت با موفقیت حذف شد.")
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

    @transaction.atomic
    def form_valid(self, form):
        slot = self.get_slot()
        locked = TimeSlot.objects.select_for_update().get(pk=slot.pk)
        if locked.is_booked:
            messages.error(self.request, "این نوبت تازه رزرو شده است. لطفاً نوبت دیگری انتخاب کنید.")
            return redirect("core:doctor_detail", pk=locked.doctor.pk)

        patient = Patient.objects.get(user=self.request.user)
        appointment = Appointment.objects.create(
            patient=patient,
            time_slot=locked,
            status=Appointment.Status.ACTIVE,
        )
        locked.is_booked = True
        locked.save(update_fields=["is_booked"])

        messages.success(self.request, "نوبت شما با موفقیت رزرو شد. اطلاعات تاییدیه به ایمیل شما ارسال شد.")
        self._send_confirmation_email(appointment)
        return redirect("appointments:patient_appointment_list")

    def _send_confirmation_email(self, appointment):
        from django.core.mail import send_mail
        from django.template.loader import render_to_string

        slot = appointment.time_slot
        subject = "تایید رزرو نوبت — مای‌کلینیک"
        body = render_to_string(
            "emails/appointment_confirmation.txt",
            {
                "appointment": appointment,
                "slot": slot,
            },
        )
        html_body = render_to_string(
            "emails/appointment_confirmation.html",
            {
                "appointment": appointment,
                "slot": slot,
            },
        )
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
                "تا ۱۲ ساعت قبل از نوبت امکان لغو وجود دارد. برای لغو این نوبت با پزشک تماس بگیرید.",
            )
            return redirect("appointments:patient_appointment_list")

        with transaction.atomic():
            slot.is_booked = False
            slot.save(update_fields=["is_booked"])
            appointment.status = Appointment.Status.CANCELLED
            appointment.cancelled_at = now
            appointment.save(update_fields=["status", "cancelled_at"])

        messages.success(self.request, "نوبت شما با موفقیت لغو شد.")
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
        today = timezone.now().date()
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
            messages.success(request, "ویزیت با موفقیت ثبت شد.")
            return redirect("appointments:appointment_detail", pk=self.object.pk)
        messages.error(request, "خطا در ثبت خلاصه ویزیت.")
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = VisitSummaryForm(
            initial={"doctor_summary": self.object.doctor_summary}
        )
        return context
