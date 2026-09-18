from django.utils import timezone
from django.views.generic import DetailView, ListView, TemplateView

from accounts.models import Doctor

from appointments.models import TimeSlot


class HomeView(TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["featured_doctors"] = (
            Doctor.objects.filter(user__is_active=True, is_verified=True)
            .select_related("user")
            .order_by("user__last_name", "user__first_name")[:6]
        )
        return context


class DoctorListView(ListView):
    template_name = "doctors/doctor_list.html"
    context_object_name = "doctors"

    def get_queryset(self):
        queryset = (
            Doctor.objects.filter(user__is_active=True)
            .select_related("user")
            .order_by("user__last_name", "user__first_name")
        )
        specialty = self.request.GET.get("specialty")
        if specialty:
            queryset = queryset.filter(specialty=specialty)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["specialties"] = list(
            Doctor.objects.filter(user__is_active=True)
            .order_by()
            .values_list("specialty", flat=True)
            .distinct()
        )
        context["active_specialty"] = self.request.GET.get("specialty", "")
        return context


class DoctorDetailView(DetailView):
    template_name = "doctors/doctor_detail.html"
    context_object_name = "doctor"

    def get_queryset(self):
        return Doctor.objects.filter(user__is_active=True).select_related("user")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["available_slots"] = (
            TimeSlot.objects.filter(doctor=self.object, is_booked=False, date__gte=timezone.localdate())
            .order_by("date", "start_time")
            .select_related("doctor", "doctor__user")
        )
        return context


class AppointmentFeatureView(TemplateView):
    template_name = "core/appointment_feature.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["featured_doctors"] = (
            Doctor.objects.filter(user__is_active=True, is_verified=True)
            .select_related("user")
            .order_by("user__last_name", "user__first_name")[:6]
        )
        return context


class MedicationFeatureView(TemplateView):
    template_name = "core/medication_feature.html"
