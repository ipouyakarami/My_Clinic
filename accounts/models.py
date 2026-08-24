from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError(_("Email is required"))
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Superuser must have is_staff=True"))
        if extra_fields.get("is_superuser") is not True:
            raise ValueError(_("Superuser must have is_superuser=True"))
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class UserType(models.TextChoices):
        PATIENT = "patient", _("Patient")
        DOCTOR = "doctor", _("Doctor")

    email = models.EmailField(_("email"), unique=True)
    first_name = models.CharField(_("first name"), max_length=150, blank=True)
    last_name = models.CharField(_("last name"), max_length=150, blank=True)
    user_type = models.CharField(
        _("user type"),
        max_length=10,
        choices=UserType.choices,
        default=UserType.PATIENT,
    )
    is_active = models.BooleanField(_("active"), default=True)
    is_staff = models.BooleanField(_("staff status"), default=False)
    date_joined = models.DateTimeField(_("date joined"), auto_now_add=True)

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
        verbose_name=_("user"),
    )
    phone_number = models.CharField(_("phone number"), max_length=15, blank=True)

    class Meta:
        verbose_name = _("patient")
        verbose_name_plural = _("patients")

    def __str__(self):
        return str(self.user)


class Doctor(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="doctor",
        verbose_name=_("user"),
    )
    specialty = models.CharField(_("specialty"), max_length=100)
    description = models.TextField(_("description"))
    profile_picture = models.ImageField(
        _("profile picture"),
        upload_to="doctor_pictures/",
        blank=True,
    )
    is_verified = models.BooleanField(_("verified"), default=False)

    class Meta:
        verbose_name = _("doctor")
        verbose_name_plural = _("doctors")

    def __str__(self):
        return f"Dr. {self.user}"
