from django.urls import path

from shared.uploads.views import UploadAccessView, UploadPresignView

app_name = "uploads"
urlpatterns = [
    path("uploads/presign/", UploadPresignView.as_view(), name="upload-presign"),
    path("uploads/access/", UploadAccessView.as_view(), name="upload-access"),
]
