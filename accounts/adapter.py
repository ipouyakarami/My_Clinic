from django.urls import reverse

from allauth.account.adapter import DefaultAccountAdapter


class AccountAdapter(DefaultAccountAdapter):
    def get_email_confirmation_url(self, request, emailconfirmation):
        return request.build_absolute_uri(
            reverse("accounts:activate_account", args=[emailconfirmation.key])
        )

    def get_login_redirect_url(self, request):
        return reverse("accounts:dashboard")
