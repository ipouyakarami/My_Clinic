from django.urls import path

from .views import (
    AppointmentFeatureView,
    DoctorDetailView,
    DoctorListView,
    HomeView,
    MedicationFeatureView,
)

app_name = "core"

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("doctors/", DoctorListView.as_view(), name="doctor_list"),
    path("doctors/<int:pk>/", DoctorDetailView.as_view(), name="doctor_detail"),
    path("appointments/info/", AppointmentFeatureView.as_view(), name="appointment_info"),
    path("medications/info/", MedicationFeatureView.as_view(), name="medication_info"),
]
