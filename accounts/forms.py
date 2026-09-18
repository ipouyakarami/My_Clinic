from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from allauth.account.forms import LoginForm, ResetPasswordForm, ResetPasswordKeyForm

from .models import Doctor
from .services import send_activation_email

User = get_user_model()


class FormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (
                css + " w-full rounded-lg border border-slate-300 px-3 py-2 "
                "focus:border-teal-600 focus:outline-none focus:ring-2 focus:ring-teal-100"
            ).strip()


class MyClinicLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("login", "password"):
            self.fields[name].widget.attrs.pop("placeholder", None)

    def clean(self):
        try:
            return super().clean()
        except forms.ValidationError:
            email = self.cleaned_data.get("login", "").strip().lower()
            user = User.objects.filter(email__iexact=email).first()
            if user and not user.is_active:
                send_activation_email(self.request, user)
                raise forms.ValidationError(
                    "If this email is registered, an activation link has been resent "
                    "to you; please check your inbox."
                )
            raise


class MyClinicResetPasswordForm(ResetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.pop("placeholder", None)


class MyClinicResetPasswordKeyForm(ResetPasswordKeyForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("password1", "password2"):
            self.fields[name].widget.attrs.pop("placeholder", None)


class PatientSignupForm(FormMixin, forms.Form):
    email = forms.EmailField(label=_("Email"))
    first_name = forms.CharField(label=_("First Name"), max_length=150)
    last_name = forms.CharField(label=_("Last Name"), max_length=150)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "This email is already registered. If you have forgotten your password, "
                "use the password recovery option."
            )
        return email


class SetPasswordForm(FormMixin, forms.Form):
    password1 = forms.CharField(label=_("Password"), widget=forms.PasswordInput)
    password2 = forms.CharField(label=_("Confirm Password"), widget=forms.PasswordInput)

    def clean_password1(self):
        password = self.cleaned_data["password1"]
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Password and confirmation do not match.")
        return cleaned


class DoctorCreationForm(FormMixin, forms.ModelForm):
    specialty = forms.CharField(label=_("Specialty"), max_length=100)
    description = forms.CharField(
        label=_("Description"),
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name"]
        labels = {
            "email": _("Email"),
            "first_name": _("First Name"),
            "last_name": _("Last Name"),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.user_type = User.UserType.DOCTOR
        user.is_active = False
        user.set_unusable_password()
        if commit:
            user.save()
            Doctor.objects.create(
                user=user,
                specialty=self.cleaned_data["specialty"],
                description=self.cleaned_data["description"],
            )
        return user


MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


def validate_image_file(value):
    """Image type validation is handled by Django's ImageField, which uses PIL
    to read the file header and verify the image (magic-byte rigor matching the
    PDF validator in medical_tests/forms.py). This validator covers the one
    thing PIL does not: an explicit maximum file size."""
    if not value:
        raise ValidationError(_("No file selected."))
    if value.size > MAX_IMAGE_SIZE:
        raise ValidationError(_("File size must not exceed 5 MB."))


class TelegramConnectForm(FormMixin, forms.Form):
    activation_code = forms.CharField(
        label=_("Activation Code"),
        max_length=64,
        help_text=_("The code you received from the MyClinic Telegram bot."),
    )


class DoctorProfileForm(forms.ModelForm):
    first_name = forms.CharField(label=_("First Name"), max_length=150)
    last_name = forms.CharField(label=_("Last Name"), max_length=150)

    class Meta:
        model = Doctor
        fields = ["specialty", "description", "profile_picture"]
        labels = {
            "specialty": _("Specialty"),
            "description": _("Description"),
            "profile_picture": _("Profile Picture"),
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name

    def clean_profile_picture(self):
        picture = self.cleaned_data.get("profile_picture")
        if picture is False:
            return picture
        if picture:
            validate_image_file(picture)
        return picture

    def save(self, commit=True):
        doctor = super().save(commit=False)
        doctor.user.first_name = self.cleaned_data["first_name"]
        doctor.user.last_name = self.cleaned_data["last_name"]
        if commit:
            doctor.user.save()
            doctor.save()
        return doctor
