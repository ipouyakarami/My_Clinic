from django.contrib import admin

from .models import MedicalTestResult


@admin.register(MedicalTestResult)
class MedicalTestResultAdmin(admin.ModelAdmin):
    list_display = ("category", "patient", "uploaded_at")
    list_filter = ("uploaded_at",)
    search_fields = ("category", "patient__email", "patient__first_name", "patient__last_name")
    ordering = ["-uploaded_at"]
