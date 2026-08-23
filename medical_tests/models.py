import os

from django.db import models
from django.utils import timezone

from accounts.models import User


def medical_test_pdf_upload_to(instance, filename):
    return os.path.join("medical_tests", str(instance.patient_id), filename)


class MedicalTestResult(models.Model):
    class TestCategory(models.TextChoices):
        BLOOD = "blood", "آزمایش خون"
        URINE = "urine", "آزمایش ادرار"
        STOOL = "stool", "آزمایش مدفوع"
        THYROID = "thyroid", "آزمایش تیروئید"
        GLUCOSE = "glucose", "آزمایش قند خون"
        LIPID = "lipid", "آزمایش چربی خون"
        HORMONE = "hormone", "آزمایش هورمونی"
        LIVER = "liver", "آزمایش کبد"
        KIDNEY = "kidney", "آزمایش کلیه"
        VITAMIN = "vitamin", "آزمایش ویتامین‌ها و مواد معدنی"

    patient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="medical_tests",
        verbose_name="بیمار",
        limit_choices_to={"user_type": User.UserType.PATIENT},
    )
    category = models.CharField(
        "نوع آزمایش",
        max_length=20,
        choices=TestCategory.choices,
    )
    name = models.CharField("نام آزمایش", max_length=200, blank=True)
    pdf_file = models.FileField("فایل PDF", upload_to=medical_test_pdf_upload_to)
    uploaded_at = models.DateTimeField("زمان آپلود", auto_now_add=True)

    class Meta:
        verbose_name = "نتیجه آزمایش"
        verbose_name_plural = "نتیجه آزمایش‌ها"
        ordering = ["-uploaded_at"]

    def __str__(self):
        label = self.name or self.get_category_display()
        return f"{label} — {self.patient}"
