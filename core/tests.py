from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Doctor, User


class SettingsConfigTest(SimpleTestCase):
    def test_timezone_is_tehran(self):
        self.assertEqual(settings.TIME_ZONE, "Asia/Tehran")

    def test_utc_storage_enabled(self):
        self.assertTrue(settings.USE_TZ)

    def test_language_is_english(self):
        self.assertEqual(settings.LANGUAGE_CODE, "en")

    def test_database_is_postgresql(self):
        self.assertEqual(
            settings.DATABASES["default"]["ENGINE"],
            "django.db.backends.postgresql",
        )

    def test_custom_user_model_active(self):
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.User")

    def test_secret_key_not_hardcoded_default(self):
        self.assertNotIn("django-insecure-", settings.SECRET_KEY)


class DatabaseSmokeTest(TestCase):
    def test_can_create_and_authenticate_user(self):
        User.objects.create_user(
            email="smoke@example.com",
            password="s3cretpass!",
            user_type=User.UserType.PATIENT,
            first_name="آزمون",
        )
        user = User.objects.get(email="smoke@example.com")
        self.assertEqual(user.user_type, User.UserType.PATIENT)
        self.assertTrue(user.check_password("s3cretpass!"))


class PublicPagesTest(TestCase):
    def setUp(self):
        self.active_user = User.objects.create_user(
            email="dr-active@example.com",
            password="s3cretpass!",
            user_type=User.UserType.DOCTOR,
            first_name="سارا",
            last_name="محمدی",
        )
        self.doctor = Doctor.objects.create(
            user=self.active_user,
            specialty="متخصص داخلی",
            description="پزشک عمومی با ده سال سابقه کاری در مراکز درمانی.",
            is_verified=True,
        )
        self.inactive_user = User.objects.create_user(
            email="dr-inactive@example.com",
            password="s3cretpass!",
            user_type=User.UserType.DOCTOR,
            first_name="رضا",
            last_name="کریمی",
        )
        self.inactive_user.is_active = False
        self.inactive_user.save()
        self.hidden_doctor = Doctor.objects.create(
            user=self.inactive_user,
            specialty="متخصص قلب",
            description="نباید نمایش داده شود.",
        )

    def test_home_page_renders(self):
        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Book Appointment")

    def test_home_page_shows_hero_with_clinic_name(self):
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "MyClinic")
        self.assertContains(response, "heroimage.jpg")

    def test_home_page_shows_doctor_carousel_section(self):
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "Book with one click")
        self.assertContains(response, "Dr. سارا محمدی")
        self.assertContains(response, "متخصص داخلی")

    def test_home_page_shows_footer_with_info(self):
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "Privacy Policy")
        self.assertContains(response, "Terms of Use")

    def test_nav_links_to_doctor_list(self):
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, 'href="/doctors/"')

    def test_doctor_list_shows_only_active(self):
        response = self.client.get(reverse("core:doctor_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dr. سارا محمدی")
        self.assertNotContains(response, "Dr. رضا کریمی")

    def test_specialty_pills_rendered_from_distinct_values(self):
        response = self.client.get(reverse("core:doctor_list"))
        self.assertContains(response, "متخصص داخلی")

    def test_doctor_list_specialty_filter(self):
        response = self.client.get(
            reverse("core:doctor_list"), {"specialty": "متخصص داخلی"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dr. سارا محمدی")

    def test_doctor_list_filter_without_match_shows_empty_state(self):
        response = self.client.get(
            reverse("core:doctor_list"), {"specialty": "تخصص ناموجود"}
        )
        self.assertContains(response, "No doctors registered with this specialty")

    def test_doctor_detail_public(self):
        response = self.client.get(
            reverse("core:doctor_detail", kwargs={"pk": self.doctor.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dr. سارا محمدی")
        self.assertContains(response, "متخصص داخلی")
        self.assertContains(response, "ده سال سابقه")

    def test_inactive_doctor_detail_404(self):
        response = self.client.get(
            reverse("core:doctor_detail", kwargs={"pk": self.hidden_doctor.pk})
        )
        self.assertEqual(response.status_code, 404)
