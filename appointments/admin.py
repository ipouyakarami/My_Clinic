from django.contrib import admin

from .models import Appointment, TimeSlot


@admin.register(TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = (
        "doctor",
        "date",
        "start_time",
        "end_time",
        "is_booked",
        "created_at",
    )
    list_filter = ("doctor", "date", "is_booked")
    search_fields = ("doctor__user__email", "doctor__user__first_name", "doctor__user__last_name")
    ordering = ["-date", "-start_time"]


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        "patient",
        "time_slot",
        "status",
        "created_at",
        "cancelled_at",
    )
    list_filter = ("status", "time_slot__date")
    search_fields = (
        "patient__user__email",
        "patient__user__first_name",
        "patient__user__last_name",
    )
    ordering = ["-created_at"]
