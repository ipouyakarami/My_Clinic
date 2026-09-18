import os

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import FormView, TemplateView, UpdateView

from allauth.account.adapter import get_adapter
from allauth.account.models import EmailConfirmation
from allauth.account.views import LoginView as AllauthLoginView

from medications.models import Medication, MedicationIntake

from appointments.models import Appointment

from .forms import DoctorProfileForm, PatientSignupForm, SetPasswordForm, TelegramConnectForm
from .models import Doctor, Patient, User
from .services import activate_account, create_patient_with_profile, send_activation_email


DEMO_EMAIL_DOMAIN = "demo.myclinic.test"


class LoginView(AllauthLoginView):
    """Allauth login view that also exposes demo accounts on the login page.

    Demo accounts are only surfaced when the seed command's guard condition is
    met (DEBUG=True or DEMO_MODE env set). When off, the panel is entirely
    absent from the rendered HTML. Credentials are derived from the seeded
    data so they always reflect what ``seed_demo`` actually created.
    """

    def _demo_enabled(self):
        from django.conf import settings

        return settings.DEBUG or os.environ.get("DEMO_MODE", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from django.conf import settings

        enabled = self._demo_enabled()
        context["demo_enabled"] = enabled
        context["demo_password"] = settings.DEMO_PASSWORD
        context["demo_accounts"] = self._get_demo_accounts() if enabled else []
        return context

    def _get_demo_accounts(self):
        from django.conf import settings

        password = settings.DEMO_PASSWORD
        suffix = f"@{DEMO_EMAIL_DOMAIN}"

        doctors = (
            Doctor.objects.filter(user__email__endswith=suffix, user__is_active=True)
            .select_related("user")
            .order_by("user__email")[:2]
        )
        patients = (
            Patient.objects.filter(user__email__endswith=suffix, user__is_active=True)
            .select_related("user")
            .order_by("user__email")[:2]
        )

        accounts = []
        for doctor in doctors:
            accounts.append(
                {
                    "role": "Doctor",
                    "identifier": doctor.specialty,
                    "name": f"Dr. {doctor.user.get_full_name()}",
                    "email": doctor.user.email,
                    "password": password,
                }
            )
        for patient in patients:
            med_names = list(
                Medication.objects.filter(patient=patient)
                .order_by("name")
                .values_list("name", flat=True)[:2]
            )
            identifier = ", ".join(med_names) if med_names else "no medications"
            accounts.append(
                {
                    "role": "Patient",
                    "identifier": identifier,
                    "name": patient.user.get_full_name(),
                    "email": patient.user.email,
                    "password": password,
                }
            )
        return accounts


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

    def get_template_names(self):
        if self.request.user.user_type == User.UserType.DOCTOR:
            return ["accounts/doctor_dashboard.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        now = timezone.now()
        today = timezone.localdate()

        if user.user_type == "patient":
            from accounts.models import Patient

            patient = Patient.objects.get(user=user)

            upcoming_appointments = (
                patient.appointments.filter(
                    status=Appointment.Status.ACTIVE,
                    time_slot__date__gte=today,
                )
                .select_related("time_slot", "time_slot__doctor", "time_slot__doctor__user")
                .order_by("time_slot__date", "time_slot__start_time")[:5]
            )

            start_dt = timezone.make_aware(
                timezone.datetime.combine(today, timezone.datetime.min.time()),
                timezone.get_current_timezone(),
            )
            end_dt = start_dt + timezone.timedelta(days=1)
            today_intakes = (
                MedicationIntake.objects.filter(
                    medication__patient=patient,
                    scheduled_time__gte=start_dt,
                    scheduled_time__lt=end_dt,
                )
                .select_related("medication")
                .order_by("scheduled_time")
            )
            groups = {}
            for intake in today_intakes:
                key = intake.scheduled_time
                groups.setdefault(key, []).append(intake)

            context["upcoming_appointments"] = upcoming_appointments
            context["today_medication_groups"] = list(groups.items())
            context["active_medications_count"] = (
                Medication.objects.filter(patient=patient, is_active=True).count()
            )
            context["tests_count"] = user.medical_tests.count()

        elif user.user_type == "doctor":
            from accounts.models import Doctor

            doctor = Doctor.objects.get(user=user)

            start_dt = timezone.make_aware(
                timezone.datetime.combine(today, timezone.datetime.min.time()),
                timezone.get_current_timezone(),
            )
            end_dt = start_dt + timezone.timedelta(days=1)
            todays_appointments = (
                Appointment.objects.filter(
                    time_slot__doctor=doctor,
                    time_slot__date=today,
                    status=Appointment.Status.ACTIVE,
                )
                .select_related("patient", "patient__user", "time_slot")
                .order_by("time_slot__start_time")
            )

            context["todays_appointments"] = todays_appointments
            context["today_total"] = todays_appointments.count()
            context["today_completed"] = Appointment.objects.filter(
                time_slot__doctor=doctor,
                time_slot__date=today,
                status=Appointment.Status.COMPLETED,
            ).count()
            context["upcoming_count"] = Appointment.objects.filter(
                time_slot__doctor=doctor,
                time_slot__date__gt=today,
                status=Appointment.Status.ACTIVE,
            ).count()
            context["patients_seen"] = (
                Appointment.objects.filter(
                    time_slot__doctor=doctor,
                    status=Appointment.Status.COMPLETED,
                )
                .values("patient")
                .distinct()
                .count()
            )

        return context


class DoctorRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return (
            self.request.user.is_authenticated
            and self.request.user.user_type == User.UserType.DOCTOR
        )


class DoctorProfileView(DoctorRequiredMixin, UpdateView):
    template_name = "accounts/doctor_profile.html"
    form_class = DoctorProfileForm

    def get_object(self, queryset=None):
        return self.request.user.doctor

    def form_valid(self, form):
        messages.success(self.request, _("Profile updated successfully."))
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("accounts:doctor_profile")


class TelegramConnectView(LoginRequiredMixin, TemplateView):
    """
    Page where a logged-in patient can:
      - See their Telegram connection status
      - Enter an activation code received from the bot
      - Unlink their Telegram account

    Only accessible to patients. Each patient can only manage their own link.
    """

    template_name = "accounts/telegram_connect.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["bot_username"] = settings.TELEGRAM_BOT_USERNAME
        context["bot_link"] = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}"

        if self.request.user.user_type == "patient":
            patient = self.request.user.patient
            context["patient"] = patient
            context["is_linked"] = patient.is_telegram_linked
        else:
            context["is_linked"] = False

        context["form"] = TelegramConnectForm()
        return context


class TelegramLinkView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    """
    Process the activation code form submission to link a patient's
    Telegram account. Only patients can use this view.
    """

    template_name = "accounts/telegram_connect.html"
    form_class = TelegramConnectForm

    def test_func(self):
        return self.request.user.user_type == "patient"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["initial"] = self.get_initial()
        return kwargs

    def get_initial(self):
        return {}

    def form_valid(self, form):
        code = form.cleaned_data["activation_code"].strip().upper()
        patient = self.request.user.patient

        from medications.models import TelegramActivationCode

        try:
            with transaction.atomic():
                try:
                    activation = TelegramActivationCode.objects.select_for_update().get(code=code)
                except TelegramActivationCode.DoesNotExist:
                    form.add_error("activation_code", "This activation code is invalid.")
                    return self.form_invalid(form)

                # Check not already used
                if activation.is_used:
                    form.add_error("activation_code", "This activation code has already been used.")
                    return self.form_invalid(form)

                # Check not expired
                if activation.is_expired:
                    form.add_error("activation_code", "This activation code has expired. Please request a new one from the bot.")
                    return self.form_invalid(form)

                # Check if another patient already has this chat_id
                existing = Patient.objects.filter(telegram_chat_id=activation.telegram_chat_id).first()
                if existing and existing != patient:
                    form.add_error("activation_code", "This Telegram account is already linked to another MyClinic account.")
                    return self.form_invalid(form)

                # Link the patient's account to this Telegram chat
                patient.telegram_chat_id = activation.telegram_chat_id
                patient.telegram_username = activation.telegram_username
                patient.save(update_fields=["telegram_chat_id", "telegram_username"])

                activation.used_at = timezone.now()
                activation.used_by_patient = patient
                activation.save(update_fields=["used_at", "used_by_patient"])

        except Exception:
            form.add_error("activation_code", "Failed to link your account. Please try again.")
            return self.form_invalid(form)

        messages.success(self.request, "Your Telegram account has been connected successfully!")
        return redirect("accounts:telegram_connect")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["bot_username"] = settings.TELEGRAM_BOT_USERNAME
        context["bot_link"] = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}"
        patient = self.request.user.patient
        context["patient"] = patient
        context["is_linked"] = patient.is_telegram_linked
        return context


class TelegramDisconnectView(LoginRequiredMixin, UserPassesTestMixin, View):
    """
    Unlink the logged-in patient's Telegram account.
    """

    def test_func(self):
        return self.request.user.user_type == "patient"

    def post(self, request, *args, **kwargs):
        patient = request.user.patient
        patient.telegram_chat_id = None
        patient.telegram_username = None
        patient.save(update_fields=["telegram_chat_id", "telegram_username"])
        messages.success(request, "Your Telegram account has been disconnected.")
        return redirect("accounts:telegram_connect")
