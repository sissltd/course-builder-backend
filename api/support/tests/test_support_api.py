from datetime import datetime, timezone as dt_timezone

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.tests.factories import make_user
from api.notification.models import Notification
from api.support.enums import SupportRequestKind, SupportRequestStatus
from api.support.models import SupportRequest
from api.support.services import support_service
from api.users.enums import AccountStatus, UserRole

CONTACT = {
    "first_name": "Ada",
    "last_name": "Obi",
    "email": "ada@example.com",
    "country": "ng",
    "message": "How do I change my payout bank?",
}
TITLED = {
    "title": "My account was suspended in error",
    "email": "creator@example.com",
    "web_link": "https://example.com/portfolio",
    "description": "The flagged lesson was original work.",
}


class SupportContactApiTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_public_contact_creates_request_without_auth(self):
        response = self.client.post("/api/v1/support/contact/", CONTACT, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["kind"], SupportRequestKind.CONTACT)
        self.assertEqual(response.data["country"], "NG")
        self.assertEqual(response.data["message"], CONTACT["message"])
        row = SupportRequest.objects.get()
        self.assertIsNone(row.submitted_by)

    def test_signed_in_contact_records_the_submitter(self):
        user = make_user()
        self.client.force_authenticate(user)

        self.client.post("/api/v1/support/contact/", CONTACT, format="json")

        self.assertEqual(SupportRequest.objects.get().submitted_by, user)

    def test_every_field_is_required(self):
        for field in CONTACT:
            with self.subTest(field=field):
                payload = {k: v for k, v in CONTACT.items() if k != field}
                response = self.client.post(
                    "/api/v1/support/contact/", payload, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data["errors"][0]["field_name"], field)

    def test_country_must_be_two_letters(self):
        response = self.client.post(
            "/api/v1/support/contact/", {**CONTACT, "country": "Nigeria"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "country")

    def test_contact_is_throttled_per_ip(self):
        for _ in range(5):
            ok = self.client.post("/api/v1/support/contact/", CONTACT, format="json")
            self.assertEqual(ok.status_code, status.HTTP_201_CREATED)

        response = self.client.post("/api/v1/support/contact/", CONTACT, format="json")

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_staff_are_notified_in_app(self):
        admin = make_user(role=UserRole.ADMIN)

        self.client.post("/api/v1/support/contact/", CONTACT, format="json")

        self.assertTrue(Notification.objects.filter(receiver=admin).exists())


class SupportTitledRequestApiTests(APITestCase):
    """Tickets and appeals share one form shape, so they share these checks."""

    URLS = {
        SupportRequestKind.TICKET: "/api/v1/support/tickets/",
        SupportRequestKind.APPEAL: "/api/v1/support/appeals/",
    }

    def setUp(self):
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        for url in self.URLS.values():
            with self.subTest(url=url):
                response = self.client.post(url, TITLED, format="json")
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_full_figma_payload_creates_open_request_of_the_right_kind(self):
        for kind, url in self.URLS.items():
            with self.subTest(kind=kind):
                response = self.client.post(url, TITLED, format="json")

                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                self.assertEqual(response.data["kind"], kind)
                self.assertEqual(response.data["status"], SupportRequestStatus.OPEN)
                self.assertEqual(response.data["title"], TITLED["title"])
                self.assertEqual(response.data["message"], TITLED["description"])
                self.assertEqual(response.data["web_link"], TITLED["web_link"])

    def test_web_link_is_optional(self):
        payload = {k: v for k, v in TITLED.items() if k != "web_link"}
        response = self.client.post(self.URLS["APPEAL"], payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["web_link"], "")

    def test_required_fields(self):
        for field in ("title", "email", "description"):
            with self.subTest(field=field):
                payload = {k: v for k, v in TITLED.items() if k != field}
                response = self.client.post(self.URLS["APPEAL"], payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data["errors"][0]["field_name"], field)

    def test_web_link_must_be_a_full_url(self):
        for value in ("https://", "example.com"):
            with self.subTest(web_link=value):
                response = self.client.post(
                    self.URLS["TICKET"], {**TITLED, "web_link": value}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data["errors"][0]["field_name"], "web_link")

    def test_appeal_gets_a_seven_business_day_deadline_and_ticket_does_not(self):
        appeal = self.client.post(self.URLS["APPEAL"], TITLED, format="json")
        ticket = self.client.post(self.URLS["TICKET"], TITLED, format="json")

        self.assertIsNotNone(appeal.data["due_at"])
        self.assertIsNone(ticket.data["due_at"])

    def test_suspended_creator_can_still_appeal(self):
        self.user.status = AccountStatus.SUSPENDED
        self.user.save(update_fields=["status"])

        response = self.client.post(self.URLS["APPEAL"], TITLED, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_list_returns_only_own_requests_of_that_kind(self):
        self.client.post(self.URLS["APPEAL"], TITLED, format="json")
        self.client.post(self.URLS["TICKET"], TITLED, format="json")
        other = make_user()
        support_service.submit_titled_request(
            user=other,
            kind=SupportRequestKind.APPEAL,
            title="Not mine",
            email=other.email,
            description="Someone else's appeal.",
        )

        response = self.client.get(self.URLS["APPEAL"])

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["paginator"]["count"], 1)
        self.assertEqual(response.data["data"]["results"][0]["title"], TITLED["title"])

    def test_staff_are_notified_in_app(self):
        admin = make_user(role=UserRole.ADMIN)

        self.client.post(self.URLS["APPEAL"], TITLED, format="json")

        self.assertTrue(Notification.objects.filter(receiver=admin).exists())


class BusinessDaysTests(APITestCase):
    def test_skips_weekends(self):
        friday = datetime(2026, 10, 2, 9, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(friday.weekday(), 4)

        due = support_service.add_business_days(friday, 7)

        self.assertEqual(due, datetime(2026, 10, 13, 9, 0, tzinfo=dt_timezone.utc))
        self.assertLess(due.weekday(), 5)


class SupportAdminApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.creator = make_user()
        self.appeal = support_service.submit_titled_request(
            user=self.creator,
            kind=SupportRequestKind.APPEAL,
            title=TITLED["title"],
            email=TITLED["email"],
            description=TITLED["description"],
        )
        self.ticket = support_service.submit_titled_request(
            user=self.creator,
            kind=SupportRequestKind.TICKET,
            title="Ticket",
            email=TITLED["email"],
            description="Help.",
        )

    def _resolve_url(self, request):
        return f"/api/v1/support/requests/{request.id}/resolve/"

    def test_creator_is_forbidden_from_the_queue(self):
        self.client.force_authenticate(self.creator)

        list_response = self.client.get("/api/v1/support/requests/")
        resolve_response = self.client.post(
            self._resolve_url(self.appeal), {}, format="json"
        )

        self.assertEqual(list_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resolve_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_lists_everything_and_filters_by_kind_and_status(self):
        self.client.force_authenticate(self.admin)

        everything = self.client.get("/api/v1/support/requests/")
        appeals = self.client.get("/api/v1/support/requests/?kind=appeal")
        resolved = self.client.get("/api/v1/support/requests/?status=RESOLVED")

        self.assertEqual(everything.data["data"]["paginator"]["count"], 2)
        self.assertEqual(appeals.data["data"]["paginator"]["count"], 1)
        self.assertEqual(
            appeals.data["data"]["results"][0]["kind"], SupportRequestKind.APPEAL
        )
        self.assertEqual(resolved.data["data"]["paginator"]["count"], 0)

    def test_admin_list_query_count_does_not_grow_with_rows(self):
        self.client.force_authenticate(self.admin)
        before = self._list_query_count()

        for _ in range(5):
            support_service.submit_contact(user=None, **_contact_kwargs())
        for _ in range(3):
            support_service.submit_titled_request(
                user=make_user(),
                kind=SupportRequestKind.TICKET,
                title="More",
                email="x@example.com",
                description="Help.",
            )

        self.assertEqual(self._list_query_count(), before)

    def _list_query_count(self):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get("/api/v1/support/requests/")
        return len(ctx)

    def test_admin_retrieves_one_request(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/v1/support/requests/{self.appeal.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(self.appeal.id))

    def test_resolve_closes_request_and_notifies_submitter(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self._resolve_url(self.appeal), {"notes": "Reinstated."}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], SupportRequestStatus.RESOLVED)
        self.assertEqual(response.data["resolution_notes"], "Reinstated.")
        self.appeal.refresh_from_db()
        self.assertEqual(self.appeal.resolved_by, self.admin)
        self.assertIsNotNone(self.appeal.resolved_at)
        self.assertTrue(Notification.objects.filter(receiver=self.creator).exists())

    def test_resolve_is_final(self):
        self.client.force_authenticate(self.admin)
        self.client.post(self._resolve_url(self.appeal), {}, format="json")

        response = self.client.post(self._resolve_url(self.appeal), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_resolve_unknown_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/v1/support/requests/00000000-0000-0000-0000-000000000000/resolve/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


def _contact_kwargs():
    return {
        "first_name": "A",
        "last_name": "B",
        "email": "a@example.com",
        "country": "NG",
        "message": "Hi",
    }
