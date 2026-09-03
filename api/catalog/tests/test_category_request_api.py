"""The category-request flow: model and email template already existed,
only the API surface was missing."""

from decimal import Decimal
from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from api.catalog.enums import CategoryRequestStatus
from api.catalog.models import Category, CategoryRequest
from api.courses.tests.factories import make_user
from api.users.enums import UserRole

URL = "/api/v1/category-requests/"


class CategoryRequestFlowTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.other = make_user(role=UserRole.COURSE_CREATOR)
        self.admin = make_user(role=UserRole.ADMIN)

    def _file(self, user=None, name="Data Science"):
        self.client.force_authenticate(user or self.creator)
        return self.client.post(
            URL,
            {"name": name, "description": "Analysis and ML courses."},
            format="json",
        )

    def test_creator_can_file_a_request(self):
        response = self._file()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], CategoryRequestStatus.PENDING)
        self.assertIsNone(response.data["resulting_category"])
        # Nothing enters the catalog until an admin approves.
        self.assertFalse(Category.objects.filter(name="Data Science").exists())

    def test_creator_sees_only_their_own_requests(self):
        self._file()
        self._file(user=self.other, name="Cybersecurity")

        self.client.force_authenticate(self.creator)
        response = self.client.get(URL)

        names = [row["name"] for row in response.data["data"]["results"]]
        self.assertEqual(names, ["Data Science"])

    def test_admin_sees_every_request(self):
        self._file()
        self._file(user=self.other, name="Cybersecurity")

        self.client.force_authenticate(self.admin)
        response = self.client.get(URL)

        names = {row["name"] for row in response.data["data"]["results"]}
        self.assertEqual(names, {"Data Science", "Cybersecurity"})

    @patch("api.catalog.services.category_request_service.send_templated_email")
    def test_approval_creates_the_category_and_emails_the_requester(self, mail):
        request_id = self._file().data["id"]
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"{URL}{request_id}/approve/",
            {"creator_price": "150000.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], CategoryRequestStatus.APPROVED)

        category = Category.objects.get(name="Data Science")
        # The approving admin supplies one rate; every tier starts there.
        self.assertEqual(category.creator_price_beginner, Decimal("150000.00"))
        self.assertEqual(category.creator_price_intermediate, Decimal("150000.00"))
        self.assertEqual(category.creator_price_advanced, Decimal("150000.00"))
        self.assertEqual(category.description, "Analysis and ML courses.")
        self.assertEqual(category.slug, "data-science")

        row = CategoryRequest.objects.get(id=request_id)
        self.assertEqual(row.resulting_category_id, category.id)
        self.assertEqual(row.reviewed_by, self.admin)

        mail.assert_called_once()
        self.assertEqual(
            mail.call_args.kwargs["receivers"], [self.creator.email]
        )

    @patch(
        "api.catalog.services.category_request_service.send_templated_email",
        side_effect=RuntimeError("smtp down"),
    )
    def test_email_failure_does_not_undo_the_approval(self, _mail):
        request_id = self._file().data["id"]
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"{URL}{request_id}/approve/",
            {"creator_price": "150000.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(Category.objects.filter(name="Data Science").exists())

    def test_approval_requires_a_price(self):
        request_id = self._file().data["id"]
        self.client.force_authenticate(self.admin)

        response = self.client.post(f"{URL}{request_id}/approve/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Category.objects.filter(name="Data Science").exists())

    def test_double_approval_is_refused(self):
        request_id = self._file().data["id"]
        self.client.force_authenticate(self.admin)
        body = {"creator_price": "150000.00"}
        self.client.post(f"{URL}{request_id}/approve/", body, format="json")

        again = self.client.post(f"{URL}{request_id}/approve/", body, format="json")

        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejection_creates_nothing(self):
        request_id = self._file().data["id"]
        self.client.force_authenticate(self.admin)

        response = self.client.post(f"{URL}{request_id}/reject/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], CategoryRequestStatus.REJECTED)
        self.assertFalse(Category.objects.filter(name="Data Science").exists())

    def test_creator_cannot_approve(self):
        request_id = self._file().data["id"]
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"{URL}{request_id}/approve/",
            {"creator_price": "150000.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Category.objects.filter(name="Data Science").exists())

    def test_creator_cannot_read_another_creators_request(self):
        request_id = self._file(user=self.other, name="Cybersecurity").data["id"]
        self.client.force_authenticate(self.creator)

        response = self.client.get(f"{URL}{request_id}/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_duplicate_category_name_is_refused_at_approval(self):
        Category.objects.create(
            name="Data Science",
            slug="data-science",
            creator_price_beginner=Decimal("1.00"),
            creator_price_intermediate=Decimal("1.00"),
            creator_price_advanced=Decimal("1.00"),
        )
        request_id = self._file().data["id"]
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"{URL}{request_id}/approve/",
            {"creator_price": "150000.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            CategoryRequest.objects.get(id=request_id).status,
            CategoryRequestStatus.PENDING,
        )


class CategoryRequestEmailTemplateTests(APITestCase):
    """The approval email is swallowed on failure, so a broken template
    would fail silently in production. Render it for real."""

    def test_approval_email_template_renders(self):
        from django.template.loader import render_to_string

        context = {"first_name": "Ada", "category_name": "Data Science"}
        for suffix in ("txt", "html"):
            with self.subTest(suffix=suffix):
                body = render_to_string(
                    f"emails/category_request_approved.{suffix}", context
                )
                self.assertIn("Data Science", body)
                self.assertIn("Ada", body)

    def test_approval_sends_a_real_email(self):
        from django.core import mail

        creator = make_user(role=UserRole.COURSE_CREATOR)
        admin = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(creator)
        request_id = self.client.post(
            URL, {"name": "Robotics", "description": "Bots."}, format="json"
        ).data["id"]

        self.client.force_authenticate(admin)
        mail.outbox.clear()
        response = self.client.post(
            f"{URL}{request_id}/approve/",
            {"creator_price": "1000.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [creator.email])
        self.assertIn("Robotics", mail.outbox[0].subject)
