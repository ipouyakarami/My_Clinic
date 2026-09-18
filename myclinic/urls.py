from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from django.views.static import serve as serve_media

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

# Serve user-uploaded files (doctor profile pictures, test PDFs) in prod as
# well as dev. Files live on a Railway volume mounted at MEDIA_ROOT.
# (django.conf.urls.static.static() is a DEBUG-only no-op in Django 6, so the
# pattern is added explicitly.)
urlpatterns += [
    re_path(
        r"^media/(?P<path>.*)$",
        serve_media,
        kwargs={"document_root": settings.MEDIA_ROOT},
    ),
]

