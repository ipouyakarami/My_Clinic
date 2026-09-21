import io

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from accounts.services import create_patient_with_profile
from medical_tests.forms import MedicalTestResultForm, validate_pdf_file
from medical_tests.models import MedicalTestResult


STRONG_PASSWORD = "Correct-Horse-9x"


class MedicalTestCase(TestCase):
    def _create_patient(self, email="pat@example.com"):
        user = create_patient_with_profile(
            {
                "email": email,
                "first_name": "مریم",
                "last_name": "صادقی",
                "password1": STRONG_PASSWORD,
                "password2": STRONG_PASSWORD,
            }
        )
        user.is_active = True
        user.save()
        return user

    def _login(self, user):
        self.client.force_login(user)


class UploadValidationTests(MedicalTestCase):
    def test_oversized_file_rejected(self):
        user = self._create_patient()
        self._login(user)
        with io.BytesIO(b"%PDF-1.4 fake pdf content" * 200000) as f:
            from django.core.files.uploadedfile import InMemoryUploadedFile
            uploaded = InMemoryUploadedFile(
                f, None, "big.pdf", "application/pdf", 6 * 1024 * 1024, None
            )
            with self.assertRaisesMessage(ValidationError, "5 MB"):
                validate_pdf_file(uploaded)

    def test_non_pdf_disguised_as_pdf_rejected(self):
        user = self._create_patient(email="pat2@example.com")
        self._login(user)
        with io.BytesIO(b"This is not a PDF file content") as f:
            from django.core.files.uploadedfile import InMemoryUploadedFile
            uploaded = InMemoryUploadedFile(
                f, None, "fake.pdf", "application/pdf", f.tell(), None
            )
            with self.assertRaisesMessage(ValidationError, "PDF"):
                validate_pdf_file(uploaded)

    def test_valid_pdf_accepted(self):
        user = self._create_patient(email="pat3@example.com")
        self._login(user)
        with io.BytesIO(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n") as f:
            from django.core.files.uploadedfile import InMemoryUploadedFile
            uploaded = InMemoryUploadedFile(
                f, None, "valid.pdf", "application/pdf", f.tell(), None
            )
            result = validate_pdf_file(uploaded)
            self.assertIsNone(result)


class OwnershipTests(MedicalTestCase):
    def test_patient_can_only_see_own_results(self):
        user1 = self._create_patient(email="pat1@example.com")
        user2 = self._create_patient(email="pat2@example.com")
        self._login(user1)
        MedicalTestResult.objects.create(patient=user2, category="blood", pdf_file=None)
        response = self.client.get(reverse("medical_tests:medical_test_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Blood Test")

    def test_download_enforces_ownership(self):
        user1 = self._create_patient(email="pat3@example.com")
        user2 = self._create_patient(email="pat4@example.com")
        test_obj = MedicalTestResult.objects.create(patient=user2, category="blood", pdf_file=None)
        self._login(user1)
        response = self.client.get(reverse("medical_tests:medical_test_download", args=[test_obj.pk]))
        self.assertEqual(response.status_code, 404)

    def test_delete_enforces_ownership(self):
        user1 = self._create_patient(email="pat5@example.com")
        user2 = self._create_patient(email="pat6@example.com")
        test_obj = MedicalTestResult.objects.create(patient=user2, category="blood", pdf_file=None)
        self._login(user1)
        response = self.client.post(reverse("medical_tests:medical_test_delete", args=[test_obj.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(MedicalTestResult.objects.filter(pk=test_obj.pk).exists())


class CategoryDropdownTests(MedicalTestCase):
    def test_upload_form_uses_select_for_category(self):
        form = MedicalTestResultForm()
        self.assertEqual(form.fields["category"].widget.input_type, "select")

    def test_upload_page_renders_category_dropdown_options(self):
        user = self._create_patient(email="form@example.com")
        self._login(user)
        response = self.client.get(reverse("medical_tests:medical_test_add"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<select")
        self.assertContains(response, "Blood Test")
        self.assertContains(response, "Vitamin &amp; Mineral Test")

    def test_invalid_category_rejected_by_validation(self):
        user = self._create_patient(email="invalid@example.com")
        test = MedicalTestResult(patient=user, category="not-a-real-category", pdf_file=None)
        with self.assertRaises(ValidationError):
            test.full_clean()


class NameFieldTests(MedicalTestCase):
    def test_upload_with_name_shows_in_list(self):
        user = self._create_patient(email="name1@example.com")
        self._login(user)
        MedicalTestResult.objects.create(
            patient=user, category="blood", name="CBC", pdf_file=None
        )
        response = self.client.get(reverse("medical_tests:medical_test_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CBC")

    def test_upload_without_name_shows_category_in_list(self):
        user = self._create_patient(email="name2@example.com")
        self._login(user)
        MedicalTestResult.objects.create(
            patient=user, category="blood", name="", pdf_file=None
        )
        response = self.client.get(reverse("medical_tests:medical_test_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Blood Test")


class TestNameEnglishOnlyValidation(MedicalTestCase):
    def _pdf(self):
        import io

        from django.core.files.uploadedfile import InMemoryUploadedFile

        content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        f = io.BytesIO(content)
        return InMemoryUploadedFile(f, None, "test.pdf", "application/pdf", len(content), None)

    def _form(self, name):
        return MedicalTestResultForm(
            data={"category": "blood", "name": name},
            files={"pdf_file": self._pdf()},
        )

    def test_non_english_test_name_rejected(self):
        form = self._form("دارو تقریبا کامل")
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)
        self.assertIn("English", str(form.errors["name"]))

    def test_english_test_name_accepted(self):
        form = self._form("CBC 2024 - Thyroid panel (2)")
        self.assertTrue(form.is_valid(), form.errors)

    def test_blank_test_name_accepted(self):
        form = self._form("")
        self.assertTrue(form.is_valid(), form.errors)
