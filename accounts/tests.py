import re
from urllib.parse import urlparse

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
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
        self.assertIn("Activate", mail.outbox[0].subject)

    def test_duplicate_email_is_rejected(self):
        self._signup()
        response = self._signup(email="SARA@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already registered")
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
            "invalid",
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
        self.assertContains(response, "do not match")

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
        self.assertContains(response, "check")
        self.assertEqual(len(mail.outbox), self.initial_outbox_count + 1)
        self.assertIn("/accounts/activate/", mail.outbox[-1].body)

    def test_unknown_email_login_shows_generic_error_without_sending_email(self):
        response = self.client.post(
            self.login_url,
            {"login": "ghost@example.com", "password": "whatever-Pass-1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "activation link has been resent")
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
        self.assertContains(response, "Patient Dashboard")


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
        self.assertContains(response, "Patient Dashboard")

    def test_doctor_login_redirects_to_dashboard(self):
        self._doctor_with_password()
        response = self.client.post(
            reverse("account_login"),
            {"login": "doc@example.com", "password": STRONG_PASSWORD},
            follow=True,
        )
        self.assertContains(response, "Doctor Dashboard")


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
        self.assertContains(response, "Doctor Dashboard")


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
        set_password_path = urlparse(response["Location"]).path
        page = self.client.get(set_password_path)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Set New Password")
        self.assertContains(page, 'name="password1"')
        self.assertNotContains(page, "Invalid Link")

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
        self.assertContains(login, "Patient Dashboard")

    def test_set_password_page_without_session_shows_invalid_link(self):
        self.client.post(
            reverse("account_reset_password"),
            {"email": "resetme@example.com"},
        )
        reset_match = RESET_KEY_URL_RE.search(mail.outbox[-1].body)
        response = self.client.get(reset_match.group(0))
        set_password_path = urlparse(response["Location"]).path

        fresh_client = Client()
        page = fresh_client.get(set_password_path)
        self.assertContains(page, "Invalid Link", status_code=200)

    def test_bogus_reset_key_shows_invalid_link(self):
        page = self.client.get(
            "/accounts/password/reset/key/1-totallyboguskey123456/"
        )
        self.assertContains(page, "Invalid Link", status_code=200)


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


class NavBarTests(TestCase):
    def _activated_patient(self, email="navp@example.com"):
        self.client.post(
            reverse("accounts:patient_signup"),
            {
                "email": email,
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
        return User.objects.get(email=email)

    def _doctor_with_password(self, email="navd@example.com"):
        form = DoctorCreationForm(
            data={
                "email": email,
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

    def _login_as(self, email, password):
        self.client.post(
            reverse("account_login"),
            {"login": email, "password": password},
        )

    def test_logged_out_nav_has_no_role_links(self):
        response = self.client.get(reverse("core:doctor_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Working Hours")
        self.assertNotContains(response, "My Appointments")
        self.assertContains(response, "Doctors")
        self.assertContains(response, "Log In")
        self.assertContains(response, "Register")

    def test_doctor_nav_excludes_patient_links(self):
        user = self._doctor_with_password()
        self._login_as(user.email, STRONG_PASSWORD)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Working Hours")
        self.assertNotContains(response, "My Appointments")
        self.assertNotContains(response, "My Tests")
        self.assertNotContains(response, "Medication Calendar")

    def test_patient_nav_excludes_doctor_links(self):
        user = self._activated_patient()
        self._login_as(user.email, STRONG_PASSWORD)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Appointments")
        self.assertNotContains(response, "Working Hours")
        self.assertNotContains(response, "Today&#x27;s Appointments")
        self.assertNotContains(response, "All Appointments")


def _build_test_image(format="PNG", color=(255, 0, 0), size=(100, 100)):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format=format)
    buf.seek(0)
    ext = "jpg" if format == "JPEG" else format.lower()
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(f"photo.{ext}", buf.getvalue(), content_type=f"image/{ext}")


def _build_oversized_valid_image():
    import io
    import random

    from PIL import Image

    random.seed(42)
    side = 1500
    raw = bytes(random.randint(0, 255) for _ in range(side * side * 3))
    img = Image.frombytes("RGB", (side, side), raw)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("huge.png", buf.getvalue(), content_type="image/png")


class DoctorProfileViewTests(TestCase):
    def setUp(self):
        form = DoctorCreationForm(
            data={
                "email": "doc@example.com",
                "first_name": "علی",
                "last_name": "رضایی",
                "specialty": "قلب و عروق",
                "description": "متخصص قلب",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.doctor_user = form.save()
        self.doctor_user.is_active = True
        self.doctor_user.set_password(STRONG_PASSWORD)
        self.doctor_user.save()
        EmailAddress.objects.update_or_create(
            user=self.doctor_user,
            defaults={"email": self.doctor_user.email, "verified": True, "primary": True},
        )
        self.profile_url = reverse("accounts:doctor_profile")

    def _login(self):
        self.client.force_login(self.doctor_user)

    def test_profile_page_renders_form(self):
        self._login()
        response = self.client.get(self.profile_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Profile")
        self.assertContains(response, "قلب و عروق")

    def test_successful_update_of_fields_and_picture(self):
        self._login()
        image = _build_test_image()
        response = self.client.post(
            self.profile_url,
            {
                "first_name": "سارا",
                "last_name": "محمدی",
                "specialty": "پوست",
                "description": "متخصص پوست و مو",
                "profile_picture": image,
            },
        )
        self.assertRedirects(response, self.profile_url)
        self.doctor_user.refresh_from_db()
        doctor = self.doctor_user.doctor
        self.assertEqual(self.doctor_user.first_name, "سارا")
        self.assertEqual(self.doctor_user.last_name, "محمدی")
        self.assertEqual(doctor.specialty, "پوست")
        self.assertEqual(doctor.description, "متخصص پوست و مو")
        self.assertTrue(doctor.profile_picture)
        self.assertIn("doctor_pictures", doctor.profile_picture.name)

    def test_updated_info_shows_on_public_profile(self):
        self._login()
        image = _build_test_image(format="JPEG", color=(0, 128, 0))
        self.client.post(
            self.profile_url,
            {
                "first_name": "مریم",
                "last_name": "صادقی",
                "specialty": "چشم",
                "description": "new description",
                "profile_picture": image,
            },
        )
        self.doctor_user.doctor.refresh_from_db()
        public = self.client.get(
            reverse("core:doctor_detail", args=[self.doctor_user.doctor.pk])
        )
        self.assertEqual(public.status_code, 200)
        self.assertContains(public, "مریم")
        self.assertContains(public, "چشم")
        self.assertContains(public, "new description")
        self.assertContains(public, self.doctor_user.doctor.profile_picture.url)

    def test_invalid_image_type_rejected(self):
        self._login()
        fake = SimpleUploadedFile(
            "hack.png", b"This is not a real image", content_type="image/png"
        )
        response = self.client.post(
            self.profile_url,
            {
                "first_name": "علی",
                "last_name": "رضایی",
                "specialty": "قلب و عروق",
                "description": "متخصص قلب",
                "profile_picture": fake,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.doctor_user.refresh_from_db()
        self.assertFalse(self.doctor_user.doctor.profile_picture)
        form = response.context["form"]
        self.assertIn("profile_picture", form.errors)
        self.assertContains(response, "valid image")

    def test_oversized_image_rejected(self):
        self._login()
        oversized = _build_oversized_valid_image()
        self.assertGreater(oversized.size, 5 * 1024 * 1024)
        response = self.client.post(
            self.profile_url,
            {
                "first_name": "علی",
                "last_name": "رضایی",
                "specialty": "قلب و عروق",
                "description": "متخصص قلب",
                "profile_picture": oversized,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.doctor_user.refresh_from_db()
        self.assertFalse(self.doctor_user.doctor.profile_picture)
        self.assertContains(response, "5 MB")

    def test_other_field_edits_preserved_when_image_invalid(self):
        self._login()
        fake = SimpleUploadedFile("hack.png", b"not an image", content_type="image/png")
        response = self.client.post(
            self.profile_url,
            {
                "first_name": "پریسا",
                "last_name": "نوری",
                "specialty": "داخلی",
                "description": "دکتر داخلی",
                "profile_picture": fake,
            },
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form["first_name"].value(), "پریسا")
        self.assertEqual(form["specialty"].value(), "داخلی")

    def test_remove_existing_picture(self):
        self._login()
        image = _build_test_image()
        self.client.post(
            self.profile_url,
            {
                "first_name": "علی",
                "last_name": "رضایی",
                "specialty": "قلب و عروق",
                "description": "متخصص قلب",
                "profile_picture": image,
            },
        )
        self.doctor_user.refresh_from_db()
        self.assertTrue(self.doctor_user.doctor.profile_picture)
        response = self.client.post(
            self.profile_url,
            {
                "first_name": "علی",
                "last_name": "رضایی",
                "specialty": "قلب و عروق",
                "description": "متخصص قلب",
                "profile_picture-clear": "clear",
            },
        )
        self.assertRedirects(response, self.profile_url)
        self.doctor_user.refresh_from_db()
        self.assertFalse(self.doctor_user.doctor.profile_picture)

    def test_patient_cannot_access_doctor_profile(self):
        from accounts.services import create_patient_with_profile

        patient = create_patient_with_profile(
            {
                "email": "pat@example.com",
                "first_name": "مریم",
                "last_name": "صادقی",
                "password1": STRONG_PASSWORD,
                "password2": STRONG_PASSWORD,
            }
        )
        patient.is_active = True
        patient.save()
        self.client.force_login(patient)
        response = self.client.get(self.profile_url)
        self.assertEqual(response.status_code, 403)

    def test_requires_login(self):
        response = self.client.get(self.profile_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_doctor_edits_only_own_profile(self):
        other_form = DoctorCreationForm(
            data={
                "email": "other@example.com",
                "first_name": "زهرا",
                "last_name": "موسوی",
                "specialty": "چشم",
                "description": "چشم‌پزشک",
            }
        )
        self.assertTrue(other_form.is_valid(), other_form.errors)
        other_user = other_form.save()
        other_user.is_active = True
        other_user.set_password(STRONG_PASSWORD)
        other_user.save()
        self._login()
        response = self.client.post(
            self.profile_url,
            {
                "first_name": "HACKED",
                "last_name": "HACKED",
                "specialty": "HACKED",
                "description": "HACKED",
            },
        )
        self.assertRedirects(response, self.profile_url)
        self.doctor_user.refresh_from_db()
        self.assertEqual(self.doctor_user.first_name, "HACKED")
        other_user.refresh_from_db()
        self.assertEqual(other_user.first_name, "زهرا")
        self.assertEqual(other_user.doctor.specialty, "چشم")
