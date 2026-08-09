from django.urls import path

from shared.uploads.views import UploadPresignView

app_name = "uploads"
urlpatterns = [
    path("uploads/presign/", UploadPresignView.as_view(), name="upload-presign"),
]
