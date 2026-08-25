from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from accounts.views import LoginView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path(
        "accounts/signup/",
        RedirectView.as_view(pattern_name="accounts:patient_signup", permanent=False),
    ),
    path("accounts/login/", LoginView.as_view(), name="account_login"),
    path("accounts/", include("allauth.urls")),
    path("", include("core.urls")),
    path("appointments/", include("appointments.urls")),
    path("medical-tests/", include("medical_tests.urls")),
    path("medications/", include("medications.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

