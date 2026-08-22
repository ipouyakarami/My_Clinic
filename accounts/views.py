from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import FormView, TemplateView

from allauth.account.adapter import get_adapter
from allauth.account.models import EmailConfirmation

from .forms import PatientSignupForm, SetPasswordForm
from .services import activate_account, create_patient_with_profile, send_activation_email


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
                "ارسال ایمیل فعال‌سازی با خطا مواجه شد. لطفاً بعداً دوباره وارد شوید "
                "تا لینک فعال‌سازی مجدداً ارسال شود.",
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
                "لینک فعال‌سازی نامعتبر است یا منقضی شده است.",
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
            "حساب شما با موفقیت فعال شد. خوش آمدید!",
        )
        return redirect(self.get_success_url())


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/dashboard.html"
