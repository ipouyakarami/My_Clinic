from django.conf import settings
from django.test import SimpleTestCase, TestCase

from accounts.models import User


class SettingsConfigTest(SimpleTestCase):
    def test_timezone_is_tehran(self):
        self.assertEqual(settings.TIME_ZONE, "Asia/Tehran")

    def test_utc_storage_enabled(self):
        self.assertTrue(settings.USE_TZ)

    def test_language_is_persian(self):
        self.assertEqual(settings.LANGUAGE_CODE, "fa")

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
