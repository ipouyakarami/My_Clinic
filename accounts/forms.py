from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from allauth.account.forms import LoginForm

from .models import Doctor
from .services import send_activation_email

User = get_user_model()


class PersianFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (
                css + " w-full rounded-lg border border-slate-300 px-3 py-2 "
                "focus:border-teal-600 focus:outline-none focus:ring-2 focus:ring-teal-100"
            ).strip()


class MyClinicLoginForm(LoginForm):
    def clean(self):
        try:
            return super().clean()
        except forms.ValidationError:
            email = self.cleaned_data.get("login", "").strip().lower()
            user = User.objects.filter(email__iexact=email).first()
            if user and not user.is_active:
                send_activation_email(self.request, user)
                raise forms.ValidationError(
                    "اگر این ایمیل در سیستم ثبت شده باشد، لینک فعال‌سازی حساب "
                    "برای شما مجدداً ارسال شد؛ لطفاً ایمیل خود را بررسی کنید."
                )
            raise


class PatientSignupForm(PersianFormMixin, forms.Form):
    email = forms.EmailField(label="ایمیل")
    first_name = forms.CharField(label="نام", max_length=150)
    last_name = forms.CharField(label="نام خانوادگی", max_length=150)
    phone_number = forms.CharField(
        label="شماره تماس (اختیاری)",
        max_length=15,
        required=False,
    )

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "این ایمیل قبلاً ثبت شده است. اگر رمز خود را فراموش کرده‌اید، "
                "از بازیابی رمز عبور استفاده کنید."
            )
        return email


class SetPasswordForm(PersianFormMixin, forms.Form):
    password1 = forms.CharField(label="رمز عبور", widget=forms.PasswordInput)
    password2 = forms.CharField(label="تکرار رمز عبور", widget=forms.PasswordInput)

    def clean_password1(self):
        password = self.cleaned_data["password1"]
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("رمز عبور و تکرار آن یکسان نیستند.")
        return cleaned


class DoctorCreationForm(PersianFormMixin, forms.ModelForm):
    specialty = forms.CharField(label="تخصص", max_length=100)
    description = forms.CharField(
        label="توضیحات",
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name"]
        labels = {
            "email": "ایمیل",
            "first_name": "نام",
            "last_name": "نام خانوادگی",
        }

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("کاربری با این ایمیل وجود دارد.")
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
