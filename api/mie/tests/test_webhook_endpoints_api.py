"""A developer's webhook endpoints, and how events reach them.

A developer keeps one or more endpoints, each taking every event or the event
types they choose, and manages them at /mie/v1/webhooks/ with their API key or
a platform session (their developer profile). Every test goes through the
literal URL a developer calls.
"""

from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from api.mie.enums import (
    WEBHOOK_ALL_EVENTS,
    SubmissionStatus,
    WebhookDeliveryStatus,
    WebhookEventType,
)
from api.mie.models import WebhookEndpoint, WebhookEvent
from api.mie.services import (
    dev_token_service,
    submission_admin_service,
    webhook_dispatcher,
    webhook_endpoint_service,
)
from api.mie.services.webhook_endpoint_service import (
    ENDPOINT_REMOVED_ERROR,
    MAX_WEBHOOK_ENDPOINTS,
)
from api.mie.tests.factories import (
    make_approved_developer,
    make_submission,
)
from api.users.enums import UserRole

LIST_URL = "/api/v1/mie/v1/webhooks/"
EVENT_TYPES_URL = "/api/v1/mie/v1/webhooks/event-types/"
INGEST_URL = "/api/v1/mie/v1/submissions/"
ME_URL = "/api/v1/mie/v1/me/"
COURSE_EVENTS = [
    WebhookEventType.COURSE_TEXT_APPROVED.value,
    WebhookEventType.COURSE_REVISION_REQUESTED.value,
    WebhookEventType.COURSE_PUBLISHED.value,
]


def _detail(endpoint_id) -> str:
    return f"{LIST_URL}{endpoint_id}/"


@pytest.fixture
def developer(db):
    """(account, raw_key) for an approved developer with the endpoint their
    registration created."""

    return make_approved_developer(webhook_url="https://hooks.studio.io/mie")


@pytest.fixture
def client(api_client, developer):
    api_client.credentials(HTTP_X_MIE_API_KEY=developer[1])
    return api_client


def _first_endpoint(account) -> WebhookEndpoint:
    return account.webhook_endpoints.get()


def _add(client, url, events):
    return client.post(LIST_URL, {"url": url, "events": events}, format="json")


# --- list ---------------------------------------------------------------------


@pytest.mark.django_db
def test_a_new_account_has_its_registration_url_taking_every_event(client, developer):
    response = client.get(LIST_URL)

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(_first_endpoint(developer[0]).id),
            "url": "https://hooks.studio.io/mie",
            "events": [WEBHOOK_ALL_EVENTS],
            "created_datetime": response.json()[0]["created_datetime"],
            "updated_datetime": response.json()[0]["updated_datetime"],
        }
    ]


@pytest.mark.django_db
def test_the_list_shows_only_the_callers_live_endpoints(client, developer):
    other, _ = make_approved_developer()
    gone = _add(client, "https://hooks.studio.io/old", COURSE_EVENTS).json()["id"]
    client.delete(_detail(gone))

    urls = [row["url"] for row in client.get(LIST_URL).json()]

    assert urls == ["https://hooks.studio.io/mie"]
    assert other.webhook_endpoints.exists()


@pytest.mark.django_db
def test_the_developer_profile_session_manages_endpoints_too(api_client, developer):
    token = dev_token_service.issue_dev_token(developer[0])
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    listed = api_client.get(LIST_URL)
    added = _add(api_client, "https://hooks.studio.io/two", [WEBHOOK_ALL_EVENTS])

    assert listed.status_code == 200
    assert added.status_code == 201


@pytest.mark.django_db
@pytest.mark.parametrize(
    "method, path",
    [
        ("get", LIST_URL),
        ("post", LIST_URL),
        ("get", EVENT_TYPES_URL),
        ("get", f"{LIST_URL}5b1f0c9e-2d4a-4f7e-9a3b-8c6d1e2f3a4b/"),
        ("patch", f"{LIST_URL}5b1f0c9e-2d4a-4f7e-9a3b-8c6d1e2f3a4b/"),
        ("delete", f"{LIST_URL}5b1f0c9e-2d4a-4f7e-9a3b-8c6d1e2f3a4b/"),
    ],
)
def test_every_route_needs_developer_credentials(api_client, method, path):
    response = getattr(api_client, method)(path, {}, format="json")

    assert response.status_code == 401


@pytest.mark.django_db
def test_a_suspended_developer_cannot_manage_endpoints(api_client):
    from api.mie.enums import DeveloperAccountStatus

    account, key = make_approved_developer()
    account.status = DeveloperAccountStatus.SUSPENDED
    account.save(update_fields=["status"])
    api_client.credentials(HTTP_X_MIE_API_KEY=key)

    assert api_client.get(LIST_URL).status_code == 401


# --- add ----------------------------------------------------------------------


@pytest.mark.django_db
def test_an_endpoint_can_take_chosen_events_returned_in_catalogue_order(client, developer):
    response = _add(
        client,
        "https://hooks.studio.io/courses",
        list(reversed(COURSE_EVENTS)) + [COURSE_EVENTS[0]],
    )

    assert response.status_code == 201
    body = response.json()
    assert body["url"] == "https://hooks.studio.io/courses"
    assert body["events"] == COURSE_EVENTS
    stored = WebhookEndpoint.objects.get(id=body["id"])
    assert (stored.developer_id, stored.all_events, stored.event_types) == (
        developer[0].id,
        False,
        COURSE_EVENTS,
    )


@pytest.mark.django_db
def test_an_endpoint_can_take_every_event(client):
    response = _add(client, "https://hooks.studio.io/all", [WEBHOOK_ALL_EVENTS])

    assert response.status_code == 201
    assert response.json()["events"] == [WEBHOOK_ALL_EVENTS]
    assert WebhookEndpoint.objects.get(id=response.json()["id"]).all_events is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body, field",
    [
        ({"events": [WEBHOOK_ALL_EVENTS]}, "url"),
        ({"url": "https://hooks.studio.io/x"}, "events"),
        ({"url": "not-a-url", "events": [WEBHOOK_ALL_EVENTS]}, "url"),
        ({"url": "https://hooks.studio.io/x", "events": []}, "events"),
        ({"url": "https://hooks.studio.io/x", "events": ["COURSE_DELETED"]}, "events"),
        (
            {
                "url": "https://hooks.studio.io/x",
                "events": [WEBHOOK_ALL_EVENTS, "COURSE_PUBLISHED"],
            },
            "events",
        ),
    ],
)
def test_a_bad_body_is_a_400_on_the_field_and_adds_nothing(client, developer, body, field):
    response = client.post(LIST_URL, body, format="json")

    assert response.status_code == 400
    assert field in {error["field_name"] for error in response.json()["errors"]}
    assert developer[0].webhook_endpoints.count() == 1


@pytest.mark.django_db
def test_the_same_url_twice_is_a_conflict(client, developer):
    response = _add(client, "https://hooks.studio.io/mie", COURSE_EVENTS)

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "webhook_endpoint_duplicate"
    assert developer[0].webhook_endpoints.count() == 1


@pytest.mark.django_db
def test_a_deleted_endpoints_url_can_be_added_again(client):
    added = _add(client, "https://hooks.studio.io/again", COURSE_EVENTS).json()["id"]
    client.delete(_detail(added))

    response = _add(client, "https://hooks.studio.io/again", [WEBHOOK_ALL_EVENTS])

    assert response.status_code == 201


@pytest.mark.django_db
def test_an_account_keeps_at_most_the_limit(client, developer):
    for number in range(1, MAX_WEBHOOK_ENDPOINTS):
        assert _add(client, f"https://hooks.studio.io/{number}", COURSE_EVENTS).status_code == 201

    response = _add(client, "https://hooks.studio.io/one-too-many", COURSE_EVENTS)

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "webhook_endpoint_limit"
    assert developer[0].webhook_endpoints.count() == MAX_WEBHOOK_ENDPOINTS


# --- show / change ------------------------------------------------------------


@pytest.mark.django_db
def test_another_developers_endpoint_is_a_404_everywhere(client):
    other, _ = make_approved_developer()
    theirs = _first_endpoint(other)

    shown = client.get(_detail(theirs.id))
    changed = client.patch(_detail(theirs.id), {"events": COURSE_EVENTS}, format="json")
    deleted = client.delete(_detail(theirs.id))

    assert (shown.status_code, changed.status_code, deleted.status_code) == (404, 404, 404)
    theirs.refresh_from_db()
    assert (theirs.all_events, theirs.is_deleted) == (True, False)


@pytest.mark.django_db
def test_an_endpoint_is_shown_by_id(client, developer):
    endpoint = _first_endpoint(developer[0])

    response = client.get(_detail(endpoint.id))

    assert response.status_code == 200
    assert response.json()["url"] == endpoint.url


@pytest.mark.django_db
def test_events_can_be_narrowed_and_widened_again(client, developer):
    endpoint = _first_endpoint(developer[0])

    narrowed = client.patch(_detail(endpoint.id), {"events": COURSE_EVENTS}, format="json")
    widened = client.patch(_detail(endpoint.id), {"events": [WEBHOOK_ALL_EVENTS]}, format="json")

    assert narrowed.status_code == 200
    assert narrowed.json()["events"] == COURSE_EVENTS
    assert widened.json()["events"] == [WEBHOOK_ALL_EVENTS]
    endpoint.refresh_from_db()
    assert (endpoint.all_events, endpoint.event_types) == (True, [])


@pytest.mark.django_db
def test_the_url_can_change_on_its_own(client, developer):
    endpoint = _first_endpoint(developer[0])

    response = client.patch(_detail(endpoint.id), {"url": "https://hooks.studio.io/v2"}, format="json")

    assert response.status_code == 200
    assert response.json()["url"] == "https://hooks.studio.io/v2"
    assert response.json()["events"] == [WEBHOOK_ALL_EVENTS]


@pytest.mark.django_db
def test_an_empty_change_is_a_400(client, developer):
    response = client.patch(_detail(_first_endpoint(developer[0]).id), {}, format="json")

    assert response.status_code == 400


@pytest.mark.django_db
def test_changing_to_another_endpoints_url_is_a_conflict(client, developer):
    second = _add(client, "https://hooks.studio.io/second", COURSE_EVENTS).json()["id"]

    response = client.patch(_detail(second), {"url": "https://hooks.studio.io/mie"}, format="json")

    assert response.status_code == 409
    assert WebhookEndpoint.objects.get(id=second).url == "https://hooks.studio.io/second"


# --- delete -------------------------------------------------------------------


@pytest.mark.django_db
def test_the_last_endpoint_cannot_be_deleted(client, developer):
    response = client.delete(_detail(_first_endpoint(developer[0]).id))

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "last_webhook_endpoint"
    assert developer[0].webhook_endpoints.filter(is_deleted=False).count() == 1


@pytest.mark.django_db
def test_deleting_an_endpoint_drops_its_queued_deliveries_only(client, developer):
    account = developer[0]
    first = _first_endpoint(account)
    second_id = _add(client, "https://hooks.studio.io/second", [WEBHOOK_ALL_EVENTS]).json()["id"]
    submission = make_submission(developer=account)
    webhook_endpoint_service.record_event(
        submission=submission,
        event_type=WebhookEventType.SUBMISSION_QUEUED,
        payload={"submission": {}},
    )

    response = client.delete(_detail(second_id))

    assert response.status_code == 204
    assert WebhookEndpoint.objects.get(id=second_id).is_deleted is True
    dropped = WebhookEvent.objects.get(endpoint_id=second_id)
    kept = WebhookEvent.objects.get(endpoint=first)
    assert (dropped.delivery_status, dropped.last_error) == (
        WebhookDeliveryStatus.FAILED,
        ENDPOINT_REMOVED_ERROR,
    )
    assert kept.delivery_status == WebhookDeliveryStatus.PENDING


# --- event types --------------------------------------------------------------


@pytest.mark.django_db
def test_the_event_types_list_every_type_once_in_catalogue_order(client):
    response = client.get(EVENT_TYPES_URL)

    assert response.status_code == 200
    assert [row["event"] for row in response.json()] == WebhookEventType.values
    assert all(row["label"] and row["fires_when"] for row in response.json())


# --- fan-out: what each endpoint receives -------------------------------------


@pytest.mark.django_db
def test_an_event_reaches_only_the_endpoints_that_take_it(client, developer, make_user):
    account = developer[0]
    everything = _first_endpoint(account)
    decisions = WebhookEndpoint.objects.get(
        id=_add(
            client,
            "https://hooks.studio.io/decisions",
            [WebhookEventType.SUBMISSION_APPROVED.value],
        ).json()["id"]
    )

    submitted = client.post(INGEST_URL, {"title": "Rust for backend engineers"}, format="json")
    submission = account.submissions.get(id=submitted.json()["id"])
    submission_admin_service.decide_submission(
        actor=make_user(role=UserRole.SUPER_ADMIN), submission=submission, approve=True
    )

    def received(endpoint):
        return sorted(
            WebhookEvent.objects.filter(endpoint=endpoint).values_list("event_type", flat=True)
        )

    assert received(everything) == sorted(
        [WebhookEventType.SUBMISSION_QUEUED, WebhookEventType.SUBMISSION_APPROVED]
    )
    assert received(decisions) == [WebhookEventType.SUBMISSION_APPROVED]


@pytest.mark.django_db
def test_a_change_applies_to_events_recorded_after_it(client, developer):
    account = developer[0]
    endpoint = _first_endpoint(account)
    client.patch(_detail(endpoint.id), {"events": COURSE_EVENTS}, format="json")

    client.post(INGEST_URL, {"title": "Go concurrency in practice"}, format="json")

    assert not WebhookEvent.objects.filter(endpoint=endpoint).exists()


@pytest.mark.django_db
def test_a_bulk_decision_fans_out_per_endpoint(client, developer, make_user):
    account = developer[0]
    _add(client, "https://hooks.studio.io/second", [WebhookEventType.SUBMISSION_REJECTED.value])
    ideas = [make_submission(developer=account) for _ in range(3)]

    submission_admin_service.decide_submissions_bulk(
        actor=make_user(role=UserRole.SUPER_ADMIN),
        submission_ids=[idea.id for idea in ideas],
        approve=True,
    )

    # Approval: the all-events endpoint takes it, the rejected-only one does not.
    assert WebhookEvent.objects.filter(
        event_type=WebhookEventType.SUBMISSION_APPROVED
    ).count() == 3
    assert not WebhookEvent.objects.filter(endpoint__url="https://hooks.studio.io/second").exists()


def _record_cost(make_developers):
    items = []
    for account in make_developers:
        submission = make_submission(developer=account, status=SubmissionStatus.PENDING_REVIEW)
        items.append((submission, WebhookEventType.SUBMISSION_QUEUED, {"submission": {}}))
    with CaptureQueriesContext(connection) as queries:
        webhook_endpoint_service.record_events(items)
    return len(queries)


@pytest.mark.django_db
def test_recording_costs_the_same_however_many_events_and_endpoints():
    def accounts(count, endpoints_each):
        made = []
        for _ in range(count):
            account, _ = make_approved_developer()
            for number in range(endpoints_each - 1):
                WebhookEndpoint.objects.create(
                    developer=account, url=f"https://hooks.example.com/{account.id}/{number}"
                )
            made.append(account)
        return made

    one = _record_cost(accounts(1, 1))
    many = _record_cost(accounts(5, 3))

    assert one == many
    assert WebhookEvent.objects.count() == 1 + 5 * 3


# --- delivery -----------------------------------------------------------------


@pytest.mark.django_db
def test_each_delivery_goes_to_its_own_endpoint(client, developer):
    account = developer[0]
    _add(client, "https://hooks.studio.io/second", [WEBHOOK_ALL_EVENTS])
    webhook_endpoint_service.record_event(
        submission=make_submission(developer=account),
        event_type=WebhookEventType.SUBMISSION_QUEUED,
        payload={"submission": {}},
    )

    with mock.patch.object(
        webhook_dispatcher, "_post_once", return_value=(True, None, "")
    ) as post:
        report = webhook_dispatcher.dispatch_due_events()

    assert sorted(call.args[0] for call in post.call_args_list) == [
        "https://hooks.studio.io/mie",
        "https://hooks.studio.io/second",
    ]
    assert report.delivered == 2


@pytest.mark.django_db
def test_a_pending_delivery_for_a_deleted_endpoint_is_failed_not_sent(developer):
    account = developer[0]
    first = _first_endpoint(account)
    second = WebhookEndpoint.objects.create(developer=account, url="https://hooks.studio.io/2")
    webhook_endpoint_service.record_event(
        submission=make_submission(developer=account),
        event_type=WebhookEventType.SUBMISSION_QUEUED,
        payload={"submission": {}},
    )
    # Deleted without the service, as if in the same instant as recording.
    WebhookEndpoint.objects.filter(id=second.id).update(is_deleted=True)

    with mock.patch.object(
        webhook_dispatcher, "_post_once", return_value=(True, None, "")
    ) as post:
        report = webhook_dispatcher.dispatch_due_events()

    assert [call.args[0] for call in post.call_args_list] == [first.url]
    assert report.failed == 1
    assert WebhookEvent.objects.get(endpoint=second).last_error == ENDPOINT_REMOVED_ERROR


# --- account surfaces ---------------------------------------------------------


@pytest.mark.django_db
def test_registration_creates_the_first_endpoint_taking_every_event(api_client):
    response = api_client.post(
        "/api/v1/mie/v1/register/",
        {"email": "new@studio.io", "webhook_url": "https://hooks.studio.io/new"},
        format="json",
    )

    assert response.status_code == 201
    assert [(row["url"], row["events"]) for row in response.json()["webhook_endpoints"]] == [
        ("https://hooks.studio.io/new", [WEBHOOK_ALL_EVENTS])
    ]
    assert "webhook_url" not in response.json()


@pytest.mark.django_db
def test_the_profile_lists_every_endpoint(client):
    _add(client, "https://hooks.studio.io/courses", COURSE_EVENTS)

    response = client.get(ME_URL)

    assert response.status_code == 200
    assert [(row["url"], row["events"]) for row in response.json()["webhook_endpoints"]] == [
        ("https://hooks.studio.io/mie", [WEBHOOK_ALL_EVENTS]),
        ("https://hooks.studio.io/courses", COURSE_EVENTS),
    ]


@pytest.mark.django_db
def test_the_admin_developer_list_shows_endpoints_at_a_flat_cost(api_client, make_user):
    api_client.force_authenticate(make_user(role=UserRole.SUPER_ADMIN))

    make_approved_developer()
    with CaptureQueriesContext(connection) as one:
        first = api_client.get("/api/v1/mie/admin/developers/")
    for _ in range(4):
        account, _ = make_approved_developer()
        WebhookEndpoint.objects.create(developer=account, url=f"https://x.example.com/{account.id}")
    with CaptureQueriesContext(connection) as five:
        api_client.get("/api/v1/mie/admin/developers/")

    assert first.status_code == 200
    rows = first.json()
    rows = rows.get("data", rows)
    rows = rows.get("results", rows) if isinstance(rows, dict) else rows
    assert rows[0]["webhook_endpoints"][0]["events"] == [WEBHOOK_ALL_EVENTS]
    assert len(one) == len(five)
