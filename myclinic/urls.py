from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path(
        "accounts/signup/",
        RedirectView.as_view(pattern_name="accounts:patient_signup", permanent=False),
    ),
    path("accounts/", include("allauth.urls")),
    path("", include("core.urls")),
    path("appointments/", include("appointments.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

