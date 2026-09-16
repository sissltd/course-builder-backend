import json

from django.test import SimpleTestCase


class ReviewerSettingsSwaggerTests(SimpleTestCase):
    def _schema(self):
        response = self.client.get(
            "/api/schema/reviewer-settings/",
            HTTP_ACCEPT="application/vnd.oai.openapi+json",
        )
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def test_reviewer_settings_schema_and_swagger_routes_resolve(self):
        schema_response = self.client.get(
            "/api/schema/reviewer-settings/",
            HTTP_ACCEPT="application/vnd.oai.openapi+json",
        )
        docs_response = self.client.get("/api/v1/docs/reviewer-settings/")

        self.assertEqual(schema_response.status_code, 200)
        self.assertEqual(docs_response.status_code, 200)

    def test_schema_contains_only_reviewer_settings_paths(self):
        schema = self._schema()

        self.assertEqual(
            set(schema["paths"]),
            {
                "/api/v1/users/me/",
                "/api/v1/users/me/availability/",
                "/api/v1/users/me/queue-preferences/",
                "/api/v1/users/me/notification-preferences/",
                "/api/v1/auth/change-email/",
                "/api/v1/auth/change-email/confirm/",
                "/api/v1/auth/change-password/",
                "/api/v1/users/me/activity-log/export/",
                "/api/v1/users/me/audit-log/export/",
            },
        )

    def test_schema_uses_figma_reviewer_settings_tags(self):
        schema = self._schema()
        operation_tags = {
            tag
            for path_item in schema["paths"].values()
            for operation in path_item.values()
            for tag in operation["tags"]
        }

        self.assertEqual(
            operation_tags,
            {
                "Reviewer Settings — Account",
                "Reviewer Settings — Availability",
                "Reviewer Settings — Queue Behaviour",
                "Reviewer Settings — Notification Settings",
                "Reviewer Settings — Log in & Security",
                "Reviewer Settings — Data & Privacy",
            },
        )
        self.assertFalse(any(tag.startswith("Creator") for tag in operation_tags))

    def test_main_schema_includes_reviewer_settings_tags(self):
        response = self.client.get(
            "/api/schema/",
            HTTP_ACCEPT="application/vnd.oai.openapi+json",
        )

        self.assertEqual(response.status_code, 200)
        schema = json.loads(response.content)

        expected_tags_by_operation = {
            ("/api/v1/users/me/", "get"): "Reviewer Settings — Account",
            ("/api/v1/users/me/", "patch"): "Reviewer Settings — Account",
            (
                "/api/v1/users/me/availability/",
                "get",
            ): "Reviewer Settings — Availability",
            (
                "/api/v1/users/me/availability/",
                "patch",
            ): "Reviewer Settings — Availability",
            (
                "/api/v1/users/me/queue-preferences/",
                "get",
            ): "Reviewer Settings — Queue Behaviour",
            (
                "/api/v1/users/me/queue-preferences/",
                "patch",
            ): "Reviewer Settings — Queue Behaviour",
            (
                "/api/v1/users/me/notification-preferences/",
                "get",
            ): "Reviewer Settings — Notification Settings",
            (
                "/api/v1/users/me/notification-preferences/",
                "patch",
            ): "Reviewer Settings — Notification Settings",
            (
                "/api/v1/auth/change-email/",
                "post",
            ): "Reviewer Settings — Log in & Security",
            (
                "/api/v1/auth/change-email/confirm/",
                "post",
            ): "Reviewer Settings — Log in & Security",
            (
                "/api/v1/auth/change-password/",
                "post",
            ): "Reviewer Settings — Log in & Security",
            (
                "/api/v1/users/me/activity-log/export/",
                "get",
            ): "Reviewer Settings — Data & Privacy",
            (
                "/api/v1/users/me/audit-log/export/",
                "get",
            ): "Reviewer Settings — Data & Privacy",
        }

        for (path, method), tag in expected_tags_by_operation.items():
            with self.subTest(path=path, method=method):
                self.assertIn(tag, schema["paths"][path][method]["tags"])
