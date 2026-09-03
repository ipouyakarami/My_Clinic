from django.urls import path

from .views import (
    ActivateAccountView, CheckInboxView, DashboardView, DoctorProfileView,
    PatientSignupView, TelegramConnectView, TelegramLinkView, TelegramDisconnectView,
)

app_name = "accounts"

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("register/patient/", PatientSignupView.as_view(), name="patient_signup"),
    path(
        "register/patient/check-inbox/",
        CheckInboxView.as_view(),
        name="check_inbox",
    ),
    path("activate/<str:key>/", ActivateAccountView.as_view(), name="activate_account"),
    path("profile/", DoctorProfileView.as_view(), name="doctor_profile"),
    path("telegram/connect/", TelegramConnectView.as_view(), name="telegram_connect"),
    path("telegram/link/", TelegramLinkView.as_view(), name="telegram_link"),
    path("telegram/disconnect/", TelegramDisconnectView.as_view(), name="telegram_disconnect"),
]
