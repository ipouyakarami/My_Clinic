from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.conf import settings
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("ایمیل الزامی است")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("سوپریوزر باید is_staff=True داشته باشد")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("سوپریوزر باید is_superuser=True داشته باشد")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class UserType(models.TextChoices):
        PATIENT = "patient", "بیمار"
        DOCTOR = "doctor", "پزشک"

    email = models.EmailField("ایمیل", unique=True)
    first_name = models.CharField("نام", max_length=150, blank=True)
    last_name = models.CharField("نام خانوادگی", max_length=150, blank=True)
    user_type = models.CharField(
        "نوع کاربر",
        max_length=10,
        choices=UserType.choices,
        default=UserType.PATIENT,
    )
    is_active = models.BooleanField("فعال", default=True)
    is_staff = models.BooleanField("کاربر ادمین", default=False)
    date_joined = models.DateTimeField("تاریخ عضویت", auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name or self.email

    def get_full_name(self):
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name or self.email

    def get_short_name(self):
        return self.first_name or self.email


class Patient(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="patient",
        verbose_name="کاربر",
    )
    phone_number = models.CharField("شماره تماس", max_length=15, blank=True)

    class Meta:
        verbose_name = "بیمار"
        verbose_name_plural = "بیماران"

    def __str__(self):
        return str(self.user)


class Doctor(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="doctor",
        verbose_name="کاربر",
    )
    specialty = models.CharField("تخصص", max_length=100)
    description = models.TextField("توضیحات")
    profile_picture = models.ImageField(
        "تصویر پروفایل",
        upload_to="doctor_pictures/",
        blank=True,
    )
    is_verified = models.BooleanField("تایید شده", default=False)

    class Meta:
        verbose_name = "پزشک"
        verbose_name_plural = "پزشکان"

    def __str__(self):
        return f"دکتر {self.user}"
