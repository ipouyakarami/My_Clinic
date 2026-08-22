import re

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from allauth.account.models import EmailAddress, EmailConfirmation

from .forms import DoctorCreationForm
from .models import Doctor, Patient, User

ACTIVATION_KEY_RE = re.compile(r"/accounts/activate/([a-z0-9]{32,64})")
RESET_KEY_URL_RE = re.compile(r"/accounts/password/reset/key/([\w-]+-[\w-]+)/")

STRONG_PASSWORD = "Correct-Horse-9x"


class PatientSignupTests(TestCase):
    def _signup(self, email="sara@example.com", **overrides):
        data = {
            "email": email,
            "first_name": "سارا",
            "last_name": "محمدی",
            "phone_number": "09121234567",
            **overrides,
        }
        return self.client.post(reverse("accounts:patient_signup"), data)

    def test_signup_creates_inactive_user_with_profile_and_sends_email(self):
        response = self._signup()
        self.assertRedirects(
            response, reverse("accounts:check_inbox"), fetch_redirect_response=False
        )
        user = User.objects.get(email="sara@example.com")
        self.assertEqual(user.user_type, User.UserType.PATIENT)
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        profile = Patient.objects.get(user=user)
        self.assertEqual(profile.phone_number, "09121234567")
        email_address = EmailAddress.objects.get(user=user)
        self.assertFalse(email_address.verified)
        self.assertTrue(email_address.primary)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("فعال‌سازی", mail.outbox[0].subject)

    def test_duplicate_email_is_rejected(self):
        self._signup()
        response = self._signup(email="SARA@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "قبلاً ثبت شده")
        self.assertEqual(User.objects.count(), 1)

    def test_invalid_email_is_rejected(self):
        response = self._signup(email="not-an-email")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())
        self.assertEqual(len(mail.outbox), 0)


class ActivationFlowTests(TestCase):
    def _signup_and_get_key(self):
        self.client.post(
            reverse("accounts:patient_signup"),
            {
                "email": "reza@example.com",
                "first_name": "رضا",
                "last_name": "کریمی",
                "phone_number": "",
            },
        )
        match = ACTIVATION_KEY_RE.search(mail.outbox[-1].body)
        self.assertIsNotNone(match, "activation URL missing from email body")
        return match.group(1)

    def test_activation_page_rejects_invalid_key(self):
        response = self.client.get(
            reverse("accounts:activate_account", args=["deadbeef" * 8])
        )
        self.assertRedirects(
            response,
            reverse("account_login"),
            fetch_redirect_response=False,
        )
        self.assertContains(
            self.client.get(reverse("account_login")),
            "نامعتبر است",
            status_code=200,
        )

    def test_weak_password_rejected(self):
        key = self._signup_and_get_key()
        url = reverse("accounts:activate_account", args=[key])
        response = self.client.post(url, {"password1": "123", "password2": "123"})
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="reza@example.com")
        self.assertFalse(user.is_active)

    def test_mismatched_password_rejected(self):
        key = self._signup_and_get_key()
        url = reverse("accounts:activate_account", args=[key])
        response = self.client.post(
            url,
            {"password1": STRONG_PASSWORD, "password2": "Different-Pass-1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "یکسان نیستند")

    def test_successful_activation_sets_password_verifies_and_logs_in(self):
        key = self._signup_and_get_key()
        url = reverse("accounts:activate_account", args=[key])
        response = self.client.post(
            url,
            {"password1": STRONG_PASSWORD, "password2": STRONG_PASSWORD},
        )
        self.assertRedirects(
            response,
            reverse("accounts:dashboard"),
            fetch_redirect_response=False,
        )
        user = User.objects.get(email="reza@example.com")
        self.assertTrue(user.is_active)
        self.assertTrue(user.has_usable_password())
        self.assertTrue(EmailAddress.objects.get(user=user).verified)
        self.assertEqual(
            EmailConfirmation.objects.filter(email_address__user=user).count(), 0
        )
        self.assertTrue(user.check_password(STRONG_PASSWORD))
        session = self.client.session
        self.assertIn("_auth_user_id", session)

    def test_activation_key_cannot_be_reused_after_activation(self):
        key = self._signup_and_get_key()
        url = reverse("accounts:activate_account", args=[key])
        self.client.post(
            url, {"password1": STRONG_PASSWORD, "password2": STRONG_PASSWORD}
        )
        response = self.client.get(url, follow=False)
        self.assertRedirects(
            response, reverse("account_login"), fetch_redirect_response=False
        )


class UnverifiedLoginTests(TestCase):
    """plan.md Phase 1 required test: unverified user cannot log in and gets
    a resend-verification prompt."""

    def setUp(self):
        self.login_url = reverse("account_login")
        self.client.post(
            reverse("accounts:patient_signup"),
            {
                "email": "mina@example.com",
                "first_name": "مینا",
                "last_name": "احمدی",
                "phone_number": "",
            },
        )
        self.initial_outbox_count = len(mail.outbox)

    def test_unverified_user_cannot_log_in_and_gets_resend_prompt(self):
        response = self.client.post(
            self.login_url,
            {"login": "mina@example.com", "password": "some-password"},
        )
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="mina@example.com")
        self.assertFalse(user.is_active)
        session = self.client.session
        self.assertNotIn("_auth_user_id", session)
        self.assertContains(response, "بررسی کنید")
        self.assertEqual(len(mail.outbox), self.initial_outbox_count + 1)
        self.assertIn("/accounts/activate/", mail.outbox[-1].body)

    def test_unknown_email_login_shows_generic_error_without_sending_email(self):
        response = self.client.post(
            self.login_url,
            {"login": "ghost@example.com", "password": "whatever-Pass-1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "فعال‌سازی حساب برای شما مجدداً ارسال شد")
        self.assertEqual(len(mail.outbox), self.initial_outbox_count)

    def test_verified_user_can_log_in(self):
        body = mail.outbox[0].body
        key = ACTIVATION_KEY_RE.search(body).group(1)
        self.client.post(
            reverse("accounts:activate_account", args=[key]),
            {"password1": STRONG_PASSWORD, "password2": STRONG_PASSWORD},
        )
        self.client.logout()
        response = self.client.post(
            self.login_url,
            {"login": "mina@example.com", "password": STRONG_PASSWORD},
            follow=True,
        )
        self.assertContains(response, "داشبورد بیمار")


class RoleBasedDashboardTests(TestCase):
    def _activated_patient(self):
        self.client.post(
            reverse("accounts:patient_signup"),
            {
                "email": "p@example.com",
                "first_name": "پریسا",
                "last_name": "نوری",
                "phone_number": "",
            },
        )
        key = ACTIVATION_KEY_RE.search(mail.outbox[-1].body).group(1)
        self.client.post(
            reverse("accounts:activate_account", args=[key]),
            {"password1": STRONG_PASSWORD, "password2": STRONG_PASSWORD},
        )
        return User.objects.get(email="p@example.com")

    def _doctor_with_password(self):
        form = DoctorCreationForm(
            data={
                "email": "doc@example.com",
                "first_name": "علی",
                "last_name": "رضایی",
                "specialty": "قلب و عروق",
                "description": "متخصص قلب با ۱۰ سال سابقه",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        doctor_user = form.save()
        doctor_user.is_active = True
        doctor_user.set_password(STRONG_PASSWORD)
        doctor_user.save()
        EmailAddress.objects.update_or_create(
            user=doctor_user,
            defaults={"email": doctor_user.email, "verified": True, "primary": True},
        )
        return doctor_user

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertRedirects(
            response,
            f"{reverse('account_login')}?next={reverse('accounts:dashboard')}",
            fetch_redirect_response=False,
        )

    def test_patient_login_redirects_to_dashboard(self):
        self._activated_patient()
        self.client.logout()
        response = self.client.post(
            reverse("account_login"),
            {"login": "p@example.com", "password": STRONG_PASSWORD},
            follow=True,
        )
        self.assertContains(response, "داشبورد بیمار")

    def test_doctor_login_redirects_to_dashboard(self):
        self._doctor_with_password()
        response = self.client.post(
            reverse("account_login"),
            {"login": "doc@example.com", "password": STRONG_PASSWORD},
            follow=True,
        )
        self.assertContains(response, "داشبورد پزشک")


class DoctorCreationFormTests(TestCase):
    def _valid_data(self):
        return {
            "email": "newdoc@example.com",
            "first_name": "مریم",
            "last_name": "صادقی",
            "specialty": "پوست",
            "description": "متخصص پوست و مو",
        }

    def test_creates_inactive_doctor_with_unusable_password(self):
        user = DoctorCreationForm(data=self._valid_data()).save()
        self.assertEqual(user.user_type, User.UserType.DOCTOR)
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        doctor = Doctor.objects.get(user=user)
        self.assertEqual(doctor.specialty, "پوست")
        self.assertFalse(doctor.is_verified)

    def test_duplicate_email_rejected(self):
        DoctorCreationForm(data=self._valid_data()).save()
        form = DoctorCreationForm(data=self._valid_data())
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)


class AdminDoctorCreationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="admin@myclinic.local", password="Admin-Secret-9"
        )
        self.client.force_login(self.admin)
        self.add_url = reverse("admin:accounts_user_add")

    def test_adding_doctor_via_admin_sends_activation_email(self):
        response = self.client.post(
            self.add_url,
            {
                "email": "admindoc@example.com",
                "first_name": "حسن",
                "last_name": "قاسمی",
                "specialty": "ارتوپدی",
                "description": "جراح ارتوپد",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="admindoc@example.com")
        self.assertEqual(user.user_type, User.UserType.DOCTOR)
        self.assertFalse(user.is_active)
        self.assertIsNotNone(Doctor.objects.filter(user=user).first())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/activate/", mail.outbox[0].body)

    def test_doctor_activation_via_emailed_link_works_end_to_end(self):
        self.client.post(
            self.add_url,
            {
                "email": "e2edoc@example.com",
                "first_name": "زهرا",
                "last_name": "موسوی",
                "specialty": "چشم",
                "description": "متخصص چشم",
            },
        )
        key = ACTIVATION_KEY_RE.search(mail.outbox[-1].body).group(1)
        response = self.client.post(
            reverse("accounts:activate_account", args=[key]),
            {"password1": STRONG_PASSWORD, "password2": STRONG_PASSWORD},
            follow=True,
        )
        user = User.objects.get(email="e2edoc@example.com")
        self.assertTrue(user.is_active)
        self.assertTrue(EmailAddress.objects.get(user=user).verified)
        self.assertContains(response, "داشبورد پزشک")


class PasswordResetFlowTests(TestCase):
    def setUp(self):
        self.client.post(
            reverse("accounts:patient_signup"),
            {
                "email": "resetme@example.com",
                "first_name": "کاوه",
                "last_name": "شریفی",
                "phone_number": "",
            },
        )
        key = ACTIVATION_KEY_RE.search(mail.outbox[-1].body).group(1)
        self.client.post(
            reverse("accounts:activate_account", args=[key]),
            {"password1": STRONG_PASSWORD, "password2": STRONG_PASSWORD},
        )
        self.client.logout()

    def test_full_password_reset_flow(self):
        self.client.post(
            reverse("account_reset_password"),
            {"email": "resetme@example.com"},
        )
        self.assertEqual(len(mail.outbox), 2)
        reset_match = RESET_KEY_URL_RE.search(mail.outbox[-1].body)
        self.assertIsNotNone(reset_match, "reset URL missing from email body")

        response = self.client.get(reset_match.group(0))
        self.assertEqual(response.status_code, 302)
        from urllib.parse import urlparse

        set_password_path = urlparse(response["Location"]).path
        page = self.client.get(set_password_path)
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            set_password_path,
            {"password1": "Brand-New-Pass-7", "password2": "Brand-New-Pass-7"},
        )
        self.assertEqual(response.status_code, 302)

        user = User.objects.get(email="resetme@example.com")
        self.assertFalse(user.check_password(STRONG_PASSWORD))
        self.assertTrue(user.check_password("Brand-New-Pass-7"))

        login = self.client.post(
            reverse("account_login"),
            {"login": "resetme@example.com", "password": "Brand-New-Pass-7"},
            follow=True,
        )
        self.assertIn("_auth_user_id", self.client.session)
        self.assertContains(login, "داشبورد بیمار")


class LogoutTests(TestCase):
    def test_logout_clears_session(self):
        self.client.post(
            reverse("accounts:patient_signup"),
            {
                "email": "out@example.com",
                "first_name": "نام",
                "last_name": "خانوادگی",
                "phone_number": "",
            },
        )
        key = ACTIVATION_KEY_RE.search(mail.outbox[-1].body).group(1)
        self.client.post(
            reverse("accounts:activate_account", args=[key]),
            {"password1": STRONG_PASSWORD, "password2": STRONG_PASSWORD},
        )
        self.assertIn("_auth_user_id", self.client.session)
        response = self.client.post(reverse("account_logout"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
