from django.contrib import admin

from .models import (
    Medication, MedicationIntake, MedicationSchedule,
    TelegramActivationCode,
)


@admin.register(Medication)
class MedicationAdmin(admin.ModelAdmin):
    list_display = ["name", "patient", "is_active", "current_inventory", "refill_reminder_threshold", "refill_reminder_sent"]
    list_filter = ["is_active", "patient"]
    search_fields = ["name", "patient__user__email"]


@admin.register(MedicationSchedule)
class MedicationScheduleAdmin(admin.ModelAdmin):
    list_display = ["medication", "frequency_type", "start_date", "end_date"]
    list_filter = ["frequency_type"]


@admin.register(MedicationIntake)
class MedicationIntakeAdmin(admin.ModelAdmin):
    list_display = ["medication", "scheduled_time", "status", "reminder_sent"]
    list_filter = ["status", "reminder_sent", "scheduled_time"]
    search_fields = ["medication__name", "token"]


@admin.register(TelegramActivationCode)
class TelegramActivationCodeAdmin(admin.ModelAdmin):
    list_display = ["code", "telegram_chat_id", "telegram_username", "created_at", "expires_at", "used_at", "used_by_patient"]
    list_filter = ["created_at", "expires_at", "used_at"]
    search_fields = ["code", "telegram_chat_id", "telegram_username"]
    readonly_fields = ["code", "telegram_chat_id", "telegram_username", "created_at", "expires_at"]
