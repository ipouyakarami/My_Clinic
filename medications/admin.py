from django.contrib import admin

from .models import Medication, MedicationIntake, MedicationSchedule


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
