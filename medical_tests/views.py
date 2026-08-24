import os

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, View

from accounts.models import User

from .forms import MedicalTestResultForm
from .models import MedicalTestResult


class PatientRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.user_type == User.UserType.PATIENT


class DoctorRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.user_type == User.UserType.DOCTOR


class MedicalTestListView(PatientRequiredMixin, ListView):
    template_name = "medical_tests/medical_test_list.html"
    context_object_name = "tests"

    def get_queryset(self):
        return MedicalTestResult.objects.filter(patient=self.request.user).order_by("-uploaded_at")


class MedicalTestUploadView(PatientRequiredMixin, CreateView):
    template_name = "medical_tests/medical_test_form.html"
    form_class = MedicalTestResultForm
    success_url = reverse_lazy("medical_tests:medical_test_list")

    def form_valid(self, form):
        form.instance.patient = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, "Test file uploaded successfully.")
        return response


class MedicalTestDeleteView(PatientRequiredMixin, DeleteView):
    template_name = "medical_tests/medical_test_confirm_delete.html"
    success_url = reverse_lazy("medical_tests:medical_test_list")

    def get_queryset(self):
        return MedicalTestResult.objects.filter(patient=self.request.user)

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        filename = os.path.basename(obj.pdf_file.name)
        response = super().delete(request, *args, **kwargs)
        messages.success(request, f"File '{filename}' deleted successfully.")
        return response


class MedicalTestDownloadView(PatientRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        obj = get_object_or_404(MedicalTestResult, pk=kwargs["pk"], patient=request.user)
        if not obj.pdf_file:
            raise Http404
        response = redirect(obj.pdf_file.url)
        return response


class MedicalTestDoctorDownloadView(DoctorRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        from appointments.models import Appointment

        test = get_object_or_404(MedicalTestResult, pk=kwargs["pk"])
        if not Appointment.objects.filter(
            time_slot__doctor=request.user.doctor,
            patient=test.patient.patient,
        ).exists():
            raise Http404
        if not test.pdf_file:
            raise Http404
        return redirect(test.pdf_file.url)
