from django.urls import path

from .views import ActivateAccountView, CheckInboxView, DashboardView, PatientSignupView

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
]
