import json

from django.test import SimpleTestCase


class OpenApiDocumentationTests(SimpleTestCase):
    def get_schema(self):
        response = self.client.get(
            "/api/schema/",
            HTTP_ACCEPT="application/vnd.oai.openapi+json",
        )
        self.assertEqual(response.status_code, 200)
        return response, json.loads(response.content)

    def test_live_documentation_is_uncached_and_exposes_version_routes(self):
        first_response, first_schema = self.get_schema()
        second_response, second_schema = self.get_schema()

        for response in (first_response, second_response):
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(response["Pragma"], "no-cache")

        self.assertEqual(first_schema["paths"], second_schema["paths"])
        self.assertEqual(
            {
                "/api/v1/admin/course-versions/",
                "/api/v1/admin/course-versions/{id}/",
                "/api/v1/admin/course-versions/migrations/",
                "/api/v1/course-versions/",
                "/api/v1/course-versions/{id}/",
            },
            {path for path in first_schema["paths"] if "course-version" in path},
        )

    def test_live_documentation_keeps_both_collaboration_lock_routes(self):
        _, schema = self.get_schema()

        self.assertIn(
            "/api/v1/courses/{course_pk}/modules/{id}/collaboration-lock/",
            schema["paths"],
        )
        self.assertIn(
            "/api/v1/courses/{course_pk}/modules/{id}/collaboration-unlock/",
            schema["paths"],
        )

    def test_swagger_ui_is_uncached_and_points_to_live_schema(self):
        response = self.client.get("/api/v1/docs/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(response, 'url: "/api/schema/"')
