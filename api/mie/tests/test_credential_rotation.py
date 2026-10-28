import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APITestCase

from api.courses.tests.factories import make_user
from api.mie.enums import DeveloperAccountStatus
from api.mie.models import DeveloperAccount
from api.mie.tests.factories import make_approved_developer
from api.users.enums import UserRole


class CredentialRotationTests(APITestCase):
    def setUp(self):
        self.actor = make_user(role=UserRole.SUPER_ADMIN)
        self.account, self.old_key = make_approved_developer()
        self.other, self.other_key = make_approved_developer()
        self.url = f"/api/v1/mie/admin/developers/{self.account.id}/rotate-credentials/"

    def test_admin_rotation_invalidates_only_selected_account_and_is_not_cached(self):
        self.client.force_authenticate(self.actor)
        response = self.client.post(
            self.url, {"confirm_account_id": str(self.account.id)}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.account.refresh_from_db()
        self.assertEqual(response.data["signing_secret"], self.account.signing_secret)
        self.client.force_authenticate(None)
        me = "/api/v1/mie/v1/me/"
        self.assertEqual(
            self.client.get(me, HTTP_X_MIE_API_KEY=self.old_key).status_code, 401
        )
        self.assertEqual(
            self.client.get(
                me, HTTP_X_MIE_API_KEY=response.data["one_time_api_key"]
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(me, HTTP_X_MIE_API_KEY=self.other_key).status_code, 200
        )
        self.assertNotEqual(response.data["one_time_api_key"], self.old_key)

    def test_denies_anonymous_developer_and_mismatched_confirmation(self):
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 401)
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 403)
        self.client.force_authenticate(self.actor)
        self.assertEqual(
            self.client.post(
                self.url, {"confirm_account_id": str(uuid4())}, format="json"
            ).status_code,
            400,
        )
        self.client.force_authenticate(None)
        self.assertEqual(
            self.client.get(
                "/api/v1/mie/v1/me/", HTTP_X_MIE_API_KEY=self.old_key
            ).status_code,
            200,
        )

    def test_suspended_account_cannot_be_rotated_or_implicitly_approved(self):
        DeveloperAccount.objects.filter(pk=self.account.id).update(
            status=DeveloperAccountStatus.SUSPENDED
        )
        self.client.force_authenticate(self.actor)
        self.assertEqual(
            self.client.post(
                self.url, {"confirm_account_id": str(self.account.id)}, format="json"
            ).status_code,
            400,
        )

    def test_export_writes_private_bundle_without_printing_secrets_or_overwriting(self):
        callback = "https://studio.example.com/studio/api/webhooks/soludesk/demo"
        self.account.webhook_endpoints.update(url=callback)
        with TemporaryDirectory() as folder:
            output = Path(folder) / "connection.json"
            stdout = StringIO()
            args = dict(
                account_id=str(self.account.id),
                actor_email=self.actor.email,
                api_base_url="https://api.example.com/api/v1/mie/v1/",
                studio_origin="https://studio.example.com",
                environment="demo",
                output=str(output),
                stdout=stdout,
            )
            call_command("export_studio_connection", **args)
            bundle = json.loads(output.read_text())
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertEqual(bundle["webhook_url"], callback)
            self.assertNotIn(bundle["api_key"], stdout.getvalue())
            self.assertNotIn(bundle["signing_secret"], stdout.getvalue())
            with self.assertRaises(CommandError):
                call_command("export_studio_connection", **args)
            self.assertEqual(json.loads(output.read_text()), bundle)
