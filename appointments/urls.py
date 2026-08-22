from django.urls import path

from .views import (
    BookAppointmentView,
    CancelAppointmentView,
    DoctorAppointmentDetailView,
    DoctorAppointmentListView,
    DoctorTodayAppointmentsView,
    PatientAppointmentListView,
    TimeSlotDeleteView,
    WorkingHoursCreateView,
)

app_name = "appointments"

urlpatterns = [
    path("working-hours/", WorkingHoursCreateView.as_view(), name="working_hours_create"),
    path(
        "working-hours/<int:pk>/delete/",
        TimeSlotDeleteView.as_view(),
        name="time_slot_delete",
    ),
    path("", PatientAppointmentListView.as_view(), name="patient_appointment_list"),
    path("doctor/", DoctorAppointmentListView.as_view(), name="doctor_appointment_list"),
    path("doctor/today/", DoctorTodayAppointmentsView.as_view(), name="doctor_today_appointments"),
    path(
        "<int:pk>/",
        DoctorAppointmentDetailView.as_view(),
        name="appointment_detail",
    ),
    path(
        "<int:pk>/cancel/",
        CancelAppointmentView.as_view(),
        name="appointment_cancel",
    ),
    path(
        "book/<int:doctor_id>/",
        BookAppointmentView.as_view(),
        name="book_appointment",
    ),
]
