from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import FormView, TemplateView

from allauth.account.adapter import get_adapter
from allauth.account.models import EmailConfirmation

from .forms import PatientSignupForm, SetPasswordForm
from .services import activate_account, create_patient_with_profile, send_activation_email

from medications.models import MedicationIntake


class PatientSignupView(FormView):
    template_name = "accounts/patient_signup.html"
    form_class = PatientSignupForm
    success_url = reverse_lazy("accounts:check_inbox")

    def form_valid(self, form):
        user = create_patient_with_profile(form.cleaned_data)
        sent = send_activation_email(self.request, user)
        if not sent:
            messages.error(
                self.request,
                "Failed to send the activation email. Please log in later "
                "to have the activation link resent.",
            )
        return super().form_valid(form)


class CheckInboxView(TemplateView):
    template_name = "accounts/check_inbox.html"


class ActivateAccountView(FormView):
    template_name = "accounts/activate_set_password.html"
    form_class = SetPasswordForm
    success_url = reverse_lazy("accounts:dashboard")

    def dispatch(self, request, *args, **kwargs):
        self.confirmation = EmailConfirmation.from_key(kwargs["key"])
        if self.confirmation is None:
            messages.error(
                request,
                "The activation link is invalid or has expired.",
            )
            return redirect(reverse("account_login"))
        if not request.user.is_anonymous and request.user.is_authenticated:
            get_adapter().logout(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["key"] = self.kwargs["key"]
        return context

    def form_valid(self, form):
        user = activate_account(self.confirmation, form.cleaned_data["password1"])
        self.confirmation.confirm(self.request)
        get_adapter().login(self.request, user)
        messages.success(
            self.request,
            "Your account has been activated successfully. Welcome!",
        )
        return redirect(self.get_success_url())


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.user_type == "patient":
            from accounts.models import Patient
            patient = Patient.objects.get(user=self.request.user)
            today = timezone.now().date()
            start_dt = timezone.make_aware(
                timezone.datetime.combine(today, timezone.datetime.min.time()),
                timezone.get_current_timezone(),
            )
            end_dt = start_dt + timezone.timedelta(days=1)
            today_intakes = MedicationIntake.objects.filter(
                medication__patient=patient,
                scheduled_time__gte=start_dt,
                scheduled_time__lt=end_dt,
            ).select_related("medication").order_by("scheduled_time")
            groups = {}
            for intake in today_intakes:
                key = intake.scheduled_time
                groups.setdefault(key, []).append(intake)
            context["today_medication_groups"] = list(groups.items())
            context["upcoming_appointments_count"] = 0
            context["active_medications_count"] = MedicationIntake.objects.filter(
                medication__patient=patient,
                medication__is_active=True,
                status=MedicationIntake.Status.PENDING,
                scheduled_time__gte=timezone.now(),
            ).values("medication").distinct().count()
        return context
