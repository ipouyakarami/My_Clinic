from django.urls import path

from . import views

app_name = "medications"

urlpatterns = [
    path("", views.MedicationListView.as_view(), name="medication_list"),
    path("add/", views.MedicationWizardView.as_view(), name="medication_add"),
    path("<int:pk>/edit/", views.MedicationWizardView.as_view(), name="medication_edit"),
    path("<int:pk>/delete/", views.MedicationDeleteView.as_view(), name="medication_delete"),
    path("calendar/", views.MedicationCalendarView.as_view(), name="medication_calendar"),
    path("calendar/<path:date>/", views.MedicationCalendarView.as_view(), name="medication_calendar_date"),
    path("intake/<str:token>/taken/", views.MedicationIntakeActionView.as_view(), kwargs={"action": "taken"}, name="intake_taken"),
    path("intake/<str:token>/skipped/", views.MedicationIntakeActionView.as_view(), kwargs={"action": "skipped"}, name="intake_skipped"),
    path("intake/mark-batch/", views.MedicationIntakeBatchView.as_view(), name="intake_mark_batch"),
    path(
        "appointments/<int:appointment_pk>/medications/",
        views.DoctorMedicationListView.as_view(),
        name="doctor_medication_list",
    ),
    path(
        "appointments/<int:appointment_pk>/medications/add/",
        views.DoctorMedicationWizardView.as_view(),
        name="doctor_medication_add",
    ),
    path(
        "appointments/<int:appointment_pk>/medications/<int:pk>/edit/",
        views.DoctorMedicationWizardView.as_view(),
        name="doctor_medication_edit",
    ),
    path(
        "appointments/<int:appointment_pk>/medications/<int:pk>/delete/",
        views.DoctorMedicationDeleteView.as_view(),
        name="doctor_medication_delete",
    ),
]
