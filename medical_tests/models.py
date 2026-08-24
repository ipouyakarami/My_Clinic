import os

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import User


def medical_test_pdf_upload_to(instance, filename):
    return os.path.join("medical_tests", str(instance.patient_id), filename)


class MedicalTestResult(models.Model):
    class TestCategory(models.TextChoices):
        BLOOD = "blood", _("Blood Test")
        URINE = "urine", _("Urine Test")
        STOOL = "stool", _("Stool Test")
        THYROID = "thyroid", _("Thyroid Test")
        GLUCOSE = "glucose", _("Glucose Test")
        LIPID = "lipid", _("Lipid Test")
        HORMONE = "hormone", _("Hormone Test")
        LIVER = "liver", _("Liver Test")
        KIDNEY = "kidney", _("Kidney Test")
        VITAMIN = "vitamin", _("Vitamin & Mineral Test")

    patient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="medical_tests",
        verbose_name=_("patient"),
        limit_choices_to={"user_type": User.UserType.PATIENT},
    )
    category = models.CharField(
        _("test category"),
        max_length=20,
        choices=TestCategory.choices,
    )
    name = models.CharField(_("test name"), max_length=200, blank=True)
    pdf_file = models.FileField(_("PDF file"), upload_to=medical_test_pdf_upload_to)
    uploaded_at = models.DateTimeField(_("uploaded at"), auto_now_add=True)

    class Meta:
        verbose_name = _("medical test result")
        verbose_name_plural = _("medical test results")
        ordering = ["-uploaded_at"]

    def __str__(self):
        label = self.name or self.get_category_display()
        return f"{label} — {self.patient}"
