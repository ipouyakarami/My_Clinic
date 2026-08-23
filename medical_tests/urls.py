from django.urls import path

from .views import (
    MedicalTestDeleteView,
    MedicalTestDoctorDownloadView,
    MedicalTestDownloadView,
    MedicalTestListView,
    MedicalTestUploadView,
)

app_name = "medical_tests"

urlpatterns = [
    path("", MedicalTestListView.as_view(), name="medical_test_list"),
    path("add/", MedicalTestUploadView.as_view(), name="medical_test_add"),
    path("<int:pk>/download/", MedicalTestDownloadView.as_view(), name="medical_test_download"),
    path("<int:pk>/download-doctor/", MedicalTestDoctorDownloadView.as_view(), name="medical_test_download_doctor"),
    path("<int:pk>/delete/", MedicalTestDeleteView.as_view(), name="medical_test_delete"),
]
