from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from api.authentication.tests.factories import make_user


class UploadPresignApiTests(APITestCase):
    def test_requires_authentication(self):
        response = self.client.post(
            "/api/v1/uploads/presign/",
            {
                "filename": "avatar.jpg",
                "content_type": "image/jpeg",
                "folder": "profiles",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("shared.services.storage_service._get_s3_client")
    def test_valid_request_returns_upload_details(self, mock_get_client):
        mock_get_client.return_value.generate_presigned_url.return_value = (
            "https://bucket.example.com/uploads/profiles/abc123.jpg?signature=xyz"
        )
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v1/uploads/presign/",
            {
                "filename": "avatar.jpg",
                "content_type": "image/jpeg",
                "folder": "profiles",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("upload_url", response.data)
        self.assertEqual(
            response.data["upload_headers"], {"Content-Type": "image/jpeg"}
        )
        self.assertIn("file_url", response.data)
        self.assertIn("file_key", response.data)
        self.assertIn("expires_in", response.data)
        self.assertTrue(response.data["file_key"].startswith("uploads/profiles/"))

        params = mock_get_client.return_value.generate_presigned_url.call_args.kwargs[
            "Params"
        ]
        self.assertNotIn("ACL", params)

    def test_invalid_content_type_rejected(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v1/uploads/presign/",
            {
                "filename": "malware.exe",
                "content_type": "application/x-msdownload",
                "folder": "profiles",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_folder_choice_rejected(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v1/uploads/presign/",
            {
                "filename": "avatar.jpg",
                "content_type": "image/jpeg",
                "folder": "not-a-real-folder",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
