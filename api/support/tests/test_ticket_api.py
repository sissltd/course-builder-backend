from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.tests.factories import make_user
from api.notification.models import Notification
from api.support.enums import (
    SupportRequestKind,
    SupportRequestStatus,
    SupportTicketMessageKind,
    SupportTicketPriority,
)
from api.support.models import SupportRequest, SupportTicketMessage
from api.support.services import support_service, ticket_service
from api.users.enums import UserRole

TICKETS_URL = "/api/v1/support/tickets/"
ADMIN_URL = "/api/v1/support/admin/tickets/"

TICKET = {
    "subject": "KYC failure during log in",
    "email": "creator@example.com",
    "priority": "HIGH",
    "description": "I keep failing KYC; please help.",
}


def make_ticket(user, **overrides):
    kwargs = {
        "title": "KYC Not Verifying",
        "email": user.email,
        "description": "Help.",
        "priority": SupportTicketPriority.HIGH,
        **overrides,
    }
    return support_service.submit_titled_request(
        user=user, kind=SupportRequestKind.TICKET, **kwargs
    )


class CreatorTicketFormTests(APITestCase):
    """Figma 'Submit a ticket': Subject, Email, Priority, Description."""

    def setUp(self):
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def test_figma_payload_creates_open_ticket_with_priority(self):
        response = self.client.post(TICKETS_URL, TICKET, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["kind"], SupportRequestKind.TICKET)
        self.assertEqual(response.data["status"], SupportRequestStatus.OPEN)
        self.assertEqual(response.data["priority"], SupportTicketPriority.HIGH)
        self.assertEqual(response.data["title"], TICKET["subject"])
        self.assertEqual(response.data["message"], TICKET["description"])
        self.assertIsNone(response.data["due_at"])

    def test_every_field_is_required(self):
        for field in TICKET:
            with self.subTest(field=field):
                payload = {k: v for k, v in TICKET.items() if k != field}
                response = self.client.post(TICKETS_URL, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data["errors"][0]["field_name"], field)

    def test_appeal_only_fields_are_not_part_of_the_ticket(self):
        response = self.client.post(
            TICKETS_URL,
            {**TICKET, "web_link": "https://example.com", "title": "Ignored"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["web_link"], "")
        self.assertEqual(response.data["title"], TICKET["subject"])

    def test_ticket_body_is_rejected_by_the_appeal_form(self):
        response = self.client.post("/api/v1/support/appeals/", TICKET, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "title")

    def test_priority_must_be_a_known_level(self):
        response = self.client.post(
            TICKETS_URL, {**TICKET, "priority": "URGENT"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "priority")

    def test_creation_emails_the_typed_address(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(TICKETS_URL, TICKET, format="json")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [TICKET["email"]])
        self.assertIn(TICKET["subject"], mail.outbox[0].subject)

    def test_appeal_ignores_priority(self):
        response = self.client.post(
            "/api/v1/support/appeals/",
            {
                "title": "Appeal",
                "email": "creator@example.com",
                "description": "x",
                "priority": "HIGH",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["priority"], "")

    def test_list_filters_by_status(self):
        make_ticket(self.user)
        closed = make_ticket(self.user)
        SupportRequest.objects.filter(pk=closed.pk).update(
            status=SupportRequestStatus.CLOSED
        )

        response = self.client.get(f"{TICKETS_URL}?status=CLOSED")

        self.assertEqual(response.data["data"]["paginator"]["count"], 1)

    def test_list_filters_by_priority(self):
        make_ticket(self.user, priority=SupportTicketPriority.LOW)
        make_ticket(self.user, priority=SupportTicketPriority.HIGH)

        response = self.client.get(f"{TICKETS_URL}?priority=LOW")

        self.assertEqual(response.data["data"]["paginator"]["count"], 1)
        self.assertEqual(response.data["data"]["results"][0]["priority"], "LOW")

    def test_unknown_or_lower_case_filter_value_is_400(self):
        for query, field in (
            ("status=done", "status"),
            ("status=closed", "status"),
            ("priority=URGENT", "priority"),
        ):
            with self.subTest(query=query):
                response = self.client.get(f"{TICKETS_URL}?{query}")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                error = response.data["errors"][0]
                self.assertEqual(error["code"], "invalid_choice")
                self.assertEqual(error["field_name"], field)


class CreatorTicketThreadTests(APITestCase):
    def setUp(self):
        self.creator = make_user()
        self.admin = make_user(role=UserRole.ADMIN)
        self.ticket = make_ticket(self.creator)
        self.client.force_authenticate(self.creator)

    def _detail(self, ticket=None):
        return f"{TICKETS_URL}{(ticket or self.ticket).id}/"

    def test_creator_sees_own_ticket_with_thread(self):
        ticket_service.add_reply(ticket=self.ticket, author=self.admin, body="On it.")

        response = self.client.get(self._detail())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["submitted_by"]["role"], "Course Creator")
        [reply] = response.data["messages"]
        self.assertEqual(reply["body"], "On it.")
        self.assertTrue(reply["from_support"])
        self.assertEqual(reply["author_id"], str(self.admin.id))
        self.assertFalse(reply["is_mine"])

    def test_creator_cannot_see_or_reply_to_someone_elses_ticket(self):
        other = make_ticket(make_user())

        detail = self.client.get(self._detail(other))
        reply = self.client.post(
            f"{self._detail(other)}messages/", {"body": "Hi"}, format="json"
        )

        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(reply.status_code, status.HTTP_404_NOT_FOUND)

    def test_creator_reply_notifies_assigned_officer_only(self):
        other_admin = make_user(role=UserRole.ADMIN)
        ticket_service.add_reply(ticket=self.ticket, author=self.admin, body="On it.")
        Notification.objects.all().delete()

        response = self.client.post(
            f"{self._detail()}messages/", {"body": "Ok. Thanks"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data["from_support"])
        self.assertTrue(response.data["is_mine"])
        self.assertTrue(Notification.objects.filter(receiver=self.admin).exists())
        self.assertFalse(Notification.objects.filter(receiver=other_admin).exists())

    def test_closed_ticket_rejects_replies(self):
        ticket_service.change_status(
            ticket=self.ticket, actor=self.admin, status=SupportRequestStatus.CLOSED
        )

        response = self.client.post(
            f"{self._detail()}messages/", {"body": "Hello?"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["errors"][0]["message"],
            "This ticket is closed and no longer accepts replies.",
        )
        self.assertFalse(
            self.ticket.messages.filter(kind=SupportTicketMessageKind.REPLY).exists()
        )


class AdminTicketPageTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.creator = make_user(first_name="Osaite", last_name="Emmanuel")
        self.ticket = make_ticket(self.creator)
        self.client.force_authenticate(self.admin)

    def _status(self, ticket, value):
        return self.client.post(
            f"{ADMIN_URL}{ticket.id}/status/", {"status": value}, format="json"
        )

    def test_creator_is_forbidden(self):
        self.client.force_authenticate(self.creator)
        for method, url in (
            ("get", ADMIN_URL),
            ("get", f"{ADMIN_URL}stats/"),
            ("post", f"{ADMIN_URL}{self.ticket.id}/status/"),
            ("post", f"{ADMIN_URL}bulk-status/"),
        ):
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, {}, format="json")
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_shows_only_tickets_with_creator_name(self):
        support_service.submit_titled_request(
            user=self.creator,
            kind=SupportRequestKind.APPEAL,
            title="Appeal",
            email="a@example.com",
            description="x",
        )

        response = self.client.get(ADMIN_URL)

        self.assertEqual(response.data["data"]["paginator"]["count"], 1)
        row = response.data["data"]["results"][0]
        self.assertEqual(row["submitted_by_name"], "Osaite Emmanuel")
        self.assertEqual(row["priority"], SupportTicketPriority.HIGH)

    def test_search_by_partial_ticket_id_and_subject(self):
        make_ticket(self.creator, title="Account Termination")
        id_fragment = str(self.ticket.id)[:8]

        by_id = self.client.get(f"{ADMIN_URL}?search={id_fragment}")
        by_subject = self.client.get(f"{ADMIN_URL}?search=termination")

        self.assertEqual(by_id.data["data"]["results"][0]["id"], str(self.ticket.id))
        self.assertEqual(by_subject.data["data"]["paginator"]["count"], 1)

    def test_filters_by_status_priority_and_date(self):
        make_ticket(self.creator, priority=SupportTicketPriority.LOW)
        self._status(self.ticket, "IN_PROGRESS")
        today = self.ticket.created_datetime.date().isoformat()

        in_progress = self.client.get(f"{ADMIN_URL}?status=in_progress")
        low = self.client.get(f"{ADMIN_URL}?priority=low")
        dated = self.client.get(f"{ADMIN_URL}?date_from={today}&date_to={today}")
        future = self.client.get(f"{ADMIN_URL}?date_from=2999-01-01")

        self.assertEqual(in_progress.data["data"]["paginator"]["count"], 1)
        self.assertEqual(low.data["data"]["paginator"]["count"], 1)
        self.assertEqual(dated.data["data"]["paginator"]["count"], 2)
        self.assertEqual(future.data["data"]["paginator"]["count"], 0)

    def test_date_to_alone_and_unknown_status_filter(self):
        past = self.client.get(f"{ADMIN_URL}?date_to=2000-01-01")
        unknown = self.client.get(f"{ADMIN_URL}?status=archived")

        self.assertEqual(past.data["data"]["paginator"]["count"], 0)
        self.assertEqual(unknown.status_code, status.HTTP_200_OK)
        self.assertEqual(unknown.data["data"]["paginator"]["count"], 0)

    def test_invalid_date_is_400(self):
        for field in ("date_from", "date_to"):
            with self.subTest(field=field):
                response = self.client.get(f"{ADMIN_URL}?{field}=19-10-2026")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                error = response.data["errors"][0]
                self.assertEqual(error["code"], "invalid")
                self.assertEqual(error["field_name"], field)

    def test_search_by_email_and_submitter_name(self):
        other = make_user(first_name="Chidi", last_name="Nwosu")
        make_ticket(other, email="chidi@example.com")

        by_email = self.client.get(f"{ADMIN_URL}?search=CHIDI@example")
        by_last_name = self.client.get(f"{ADMIN_URL}?search=nwosu")
        by_first_name = self.client.get(f"{ADMIN_URL}?search=osaite")

        for response, expected in (
            (by_email, "Chidi Nwosu"),
            (by_last_name, "Chidi Nwosu"),
            (by_first_name, "Osaite Emmanuel"),
        ):
            self.assertEqual(response.data["data"]["paginator"]["count"], 1)
            self.assertEqual(
                response.data["data"]["results"][0]["submitted_by_name"], expected
            )

    def test_ordering_by_created_and_updated(self):
        newer = make_ticket(self.creator, title="Newer")
        # Touch the older ticket so it is the most recently updated.
        ticket_service.add_reply(ticket=self.ticket, author=self.admin, body="Hi")

        def ids(ordering):
            response = self.client.get(f"{ADMIN_URL}?ordering={ordering}")
            return [row["id"] for row in response.data["data"]["results"]]

        self.assertEqual(ids("created_datetime"), [str(self.ticket.id), str(newer.id)])
        self.assertEqual(ids("-created_datetime"), [str(newer.id), str(self.ticket.id)])
        self.assertEqual(ids("-updated_datetime"), [str(self.ticket.id), str(newer.id)])

    def test_ordering_by_priority_follows_severity_not_the_alphabet(self):
        SupportRequest.objects.filter(pk=self.ticket.pk).delete()
        for level in ("LOW", "HIGH", "MEDIUM"):
            make_ticket(self.creator, priority=level)

        def priorities(ordering):
            response = self.client.get(f"{ADMIN_URL}?ordering={ordering}")
            return [row["priority"] for row in response.data["data"]["results"]]

        self.assertEqual(priorities("priority"), ["LOW", "MEDIUM", "HIGH"])
        self.assertEqual(priorities("-priority"), ["HIGH", "MEDIUM", "LOW"])

    def test_ordering_by_status_follows_the_lifecycle(self):
        closed = make_ticket(self.creator)
        in_progress = make_ticket(self.creator)
        self._status(closed, "CLOSED")
        self._status(in_progress, "IN_PROGRESS")

        response = self.client.get(f"{ADMIN_URL}?ordering=status")

        self.assertEqual(
            [row["status"] for row in response.data["data"]["results"]],
            ["OPEN", "IN_PROGRESS", "CLOSED"],
        )

    def test_unknown_ticket_id_is_404_on_every_detail_action(self):
        url = f"{ADMIN_URL}00000000-0000-0000-0000-000000000000/"
        for method, path, body in (
            ("get", "", None),
            ("post", "status/", {"status": "CLOSED"}),
            ("post", "messages/", {"body": "Hi"}),
        ):
            with self.subTest(path=path or "retrieve"):
                response = getattr(self.client, method)(
                    f"{url}{path}", body, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(response.data["errors"][0]["message"], "Not found.")

    def test_list_query_count_does_not_grow_with_rows(self):
        before = self._query_count(ADMIN_URL)
        for _ in range(4):
            ticket = make_ticket(make_user())
            ticket_service.add_reply(ticket=ticket, author=self.admin, body="Hi")
        self.assertEqual(self._query_count(ADMIN_URL), before)

    def test_detail_query_count_does_not_grow_with_thread(self):
        url = f"{ADMIN_URL}{self.ticket.id}/"
        ticket_service.add_reply(ticket=self.ticket, author=self.admin, body="1")
        before = self._query_count(url)
        for n in range(4):
            ticket_service.add_reply(
                ticket=self.ticket, author=self.creator, body=str(n)
            )
        self.assertEqual(self._query_count(url), before)

    def _query_count(self, url):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(url)
        return len(ctx)

    def test_stats_counts_every_status(self):
        make_ticket(self.creator)
        self._status(self.ticket, "RESOLVED")

        response = self.client.get(f"{ADMIN_URL}stats/")

        self.assertEqual(
            response.data,
            {"open": 1, "in_progress": 0, "resolved": 1, "closed": 0},
        )

    def test_status_change_records_thread_assigns_officer_and_notifies(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self._status(self.ticket, "IN_PROGRESS")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], SupportRequestStatus.IN_PROGRESS)
        self.assertEqual(response.data["assigned_to_name"], self.admin.get_full_name())
        event = SupportTicketMessage.objects.get(ticket=self.ticket)
        self.assertEqual(event.kind, SupportTicketMessageKind.STATUS_CHANGE)
        self.assertEqual(event.from_status, SupportRequestStatus.OPEN)
        self.assertEqual(event.to_status, SupportRequestStatus.IN_PROGRESS)
        self.assertTrue(Notification.objects.filter(receiver=self.creator).exists())
        self.assertEqual(mail.outbox[-1].to, [self.ticket.email])

    def test_resolve_then_close_follows_figma_flow(self):
        self._status(self.ticket, "IN_PROGRESS")
        resolved = self._status(self.ticket, "RESOLVED")
        closed = self._status(self.ticket, "CLOSED")

        self.assertEqual(resolved.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(resolved.data["resolved_at"])
        self.assertEqual(closed.data["status"], SupportRequestStatus.CLOSED)
        detail = self.client.get(f"{ADMIN_URL}{self.ticket.id}/")
        self.assertEqual(
            [m["to_status"] for m in detail.data["messages"]],
            ["IN_PROGRESS", "RESOLVED", "CLOSED"],
        )

    def test_invalid_transitions_are_rejected(self):
        self._status(self.ticket, "CLOSED")
        for value, message in (
            ("IN_PROGRESS", "Ticket cannot move from 'Closed' to 'In-progress'."),
            ("RESOLVED", "Ticket cannot move from 'Closed' to 'Resolved'."),
            ("CLOSED", "Ticket is already 'Closed'."),
        ):
            with self.subTest(status=value):
                response = self._status(self.ticket, value)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                error = response.data["errors"][0]
                self.assertEqual(error["field_name"], "status")
                self.assertEqual(error["message"], message)

    def test_open_is_not_a_settable_status(self):
        response = self._status(self.ticket, "OPEN")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "status")

    def test_status_change_on_a_non_ticket_is_404(self):
        appeal = support_service.submit_titled_request(
            user=self.creator,
            kind=SupportRequestKind.APPEAL,
            title="Appeal",
            email="a@example.com",
            description="x",
        )
        self.assertEqual(
            self._status(appeal, "CLOSED").status_code, status.HTTP_404_NOT_FOUND
        )

    def test_bulk_updates_eligible_and_skips_the_rest(self):
        second = make_ticket(self.creator)
        already_closed = make_ticket(self.creator)
        self._status(already_closed, "CLOSED")
        unknown = "00000000-0000-0000-0000-000000000000"

        response = self.client.post(
            f"{ADMIN_URL}bulk-status/",
            {
                "ids": [
                    str(self.ticket.id),
                    str(second.id),
                    str(already_closed.id),
                    unknown,
                ],
                "status": "CLOSED",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {row["id"] for row in response.data["updated"]},
            {str(self.ticket.id), str(second.id)},
        )
        self.assertEqual(
            {row["id"] for row in response.data["skipped"]},
            {str(already_closed.id), unknown},
        )
        self.assertEqual(
            SupportRequest.objects.filter(status=SupportRequestStatus.CLOSED).count(), 3
        )

    def test_bulk_requires_ids(self):
        response = self.client.post(
            f"{ADMIN_URL}bulk-status/", {"ids": [], "status": "CLOSED"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_reply_assigns_officer_and_emails_submitter(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{ADMIN_URL}{self.ticket.id}/messages/",
                {"body": "Resolved shortly."},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["from_support"])
        self.assertTrue(response.data["is_mine"])
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_to, self.admin)
        self.assertIn("Resolved shortly.", mail.outbox[-1].body)
        self.assertTrue(Notification.objects.filter(receiver=self.creator).exists())

    def test_thread_marks_only_the_viewing_admins_entries_as_mine(self):
        colleague = make_user(role=UserRole.ADMIN)
        ticket_service.add_reply(ticket=self.ticket, author=colleague, body="Hi")
        ticket_service.add_reply(ticket=self.ticket, author=self.admin, body="Me")
        ticket_service.add_reply(ticket=self.ticket, author=self.creator, body="Ok")

        response = self.client.get(f"{ADMIN_URL}{self.ticket.id}/")

        self.assertEqual(
            [
                (m["body"], m["author_id"], m["from_support"], m["is_mine"])
                for m in response.data["messages"]
            ],
            [
                ("Hi", str(colleague.id), True, False),
                ("Me", str(self.admin.id), True, True),
                ("Ok", str(self.creator.id), False, False),
            ],
        )

    def test_reply_body_is_required(self):
        response = self.client.post(
            f"{ADMIN_URL}{self.ticket.id}/messages/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "body")
