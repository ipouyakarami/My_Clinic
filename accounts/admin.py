from django.contrib import admin
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .forms import DoctorCreationForm
from .models import Doctor, Patient
from .services import send_activation_email

User = get_user_model()


class PatientProfileInline(admin.StackedInline):
    model = Patient
    can_delete = False
    verbose_name_plural = "Patient Profile"


class DoctorProfileInline(admin.StackedInline):
    model = Doctor
    can_delete = False
    verbose_name_plural = "Doctor Profile"


@admin.register(User)
class MyClinicUserAdmin(DjangoUserAdmin):
    add_form = DoctorCreationForm
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "first_name",
                    "last_name",
                    "specialty",
                    "description",
                ),
                "description": "Submitting this form creates a doctor account and sends an activation email.",
            },
        ),
    )

    fieldsets = (
        ("User Info", {"fields": ("email", "first_name", "last_name", "user_type")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (
            "Dates",
            {"fields": ("last_login", "date_joined"), "classes": ("collapse",)},
        ),
    )
    readonly_fields = ("last_login", "date_joined")

    list_display = (
        "email",
        "get_full_name",
        "user_type",
        "is_active",
        "is_staff",
        "date_joined",
    )
    list_filter = ("user_type", "is_active", "is_staff")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("-date_joined",)
    filter_horizontal = ("groups", "user_permissions")

    @admin.display(description="Full Name")
    def get_full_name(self, obj):
        return obj.get_full_name()

    def get_inlines(self, request, obj=None):
        if obj is None:
            return []
        if obj.user_type == User.UserType.DOCTOR:
            return [DoctorProfileInline]
        return [PatientProfileInline]

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = DoctorCreationForm
        kwargs.pop("fields", None)
        return super().get_form(request, obj, **kwargs)

    def save_model(self, request, obj, form, change):
        creating_doctor = not change and obj.user_type == User.UserType.DOCTOR
        super().save_model(request, obj, form, change)
        if creating_doctor:
            Doctor.objects.get_or_create(
                user=obj,
                defaults={
                    "specialty": form.cleaned_data["specialty"],
                    "description": form.cleaned_data["description"],
                },
            )
            sent = send_activation_email(request, obj)
            if sent:
                self.message_user(
                    request,
                    f"Doctor account created and activation email sent to {obj.email}.",
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    "Doctor account created but activation email failed to send.",
                    level=messages.ERROR,
                )


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("__str__", "specialty", "is_verified")
    list_filter = ("specialty", "is_verified")
    search_fields = ("user__email", "user__first_name", "user__last_name", "specialty")


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("__str__", "phone_number", "telegram_username", "is_telegram_linked")
    list_filter = ("telegram_chat_id",)
    search_fields = ("user__email", "user__first_name", "user__last_name", "telegram_username")
    readonly_fields = ("is_telegram_linked",)
