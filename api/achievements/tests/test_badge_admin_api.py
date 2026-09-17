"""The Achievement award screen: list, add, edit, configure, delete.

Everything goes through the URLs the settings screen calls.
"""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from api.achievements.enums import AwardSource, BadgeCriterion
from api.achievements.models import Badge, CreatorBadge
from api.achievements.tests.factories import make_award, make_badge
from api.courses.tests.factories import make_draft_course, make_user
from api.notification.models import Notification
from api.users.enums import UserRole
from api.users.models import UserActivityLog

URL = "/api/v1/admin/achievements/badges/"


def detail_url(badge):
    return f"{URL}{badge.id}/"


def _valid_payload(**overrides):
    payload = {
        "title": "Top",
        "icon": "diamond",
        "color": "#F2994A",
        "criterion": BadgeCriterion.COURSES_CREATED,
        "required_count": 3,
        "auto_award": False,
    }
    payload.update(overrides)
    return payload


class BadgeAccessTests(APITestCase):
    """Writer, Admin and Super Admin manage badges; nobody else does."""

    def test_managing_roles_can_list_and_create(self):
        for role in (UserRole.STAFF_WRITER, UserRole.ADMIN, UserRole.SUPER_ADMIN):
            with self.subTest(role=role):
                self.client.force_authenticate(make_user(role=role))
                self.assertEqual(self.client.get(URL).status_code, status.HTTP_200_OK)
                response = self.client.post(
                    URL,
                    _valid_payload(title=f"Top {role}", required_count=len(role)),
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_other_roles_are_refused(self):
        badge = make_badge()
        for role in (
            UserRole.STAFF_APPROVER,
            UserRole.COURSE_CREATOR,
            UserRole.CREATOR_REVIEWER,
            UserRole.STAFF_VERIFIER,
            UserRole.QA_REVIEWER,
        ):
            with self.subTest(role=role):
                self.client.force_authenticate(make_user(role=role))
                self.assertEqual(
                    self.client.get(URL).status_code, status.HTTP_403_FORBIDDEN
                )
                self.assertEqual(
                    self.client.post(URL, _valid_payload(), format="json").status_code,
                    status.HTTP_403_FORBIDDEN,
                )
                self.assertEqual(
                    self.client.delete(detail_url(badge)).status_code,
                    status.HTTP_403_FORBIDDEN,
                )
        self.assertFalse(Badge.objects.get(id=badge.id).is_deleted)

    def test_unauthenticated_is_refused(self):
        self.assertEqual(self.client.get(URL).status_code, status.HTTP_401_UNAUTHORIZED)


class BadgeListTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

    def test_rows_carry_what_the_list_and_analytics_cards_draw(self):
        top = make_badge(
            title="Top", required_count=100, icon="diamond", color="#F2994A"
        )
        rising = make_badge(title="Rising", required_count=10)
        creators = [make_user() for _ in range(2)]
        for creator in creators:
            make_award(badge=top, creator=creator)
        make_award(badge=rising, creator=creators[0], is_deleted=True)

        response = self.client.get(URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("paginator", response.data["data"])
        rows = response.data["data"]["results"]
        self.assertEqual([row["title"] for row in rows], ["Top", "Rising"])
        self.assertEqual(rows[0]["holder_count"], 2)
        # A revoked award is not a holder.
        self.assertEqual(rows[1]["holder_count"], 0)
        self.assertEqual(rows[0]["icon"], "diamond")
        self.assertEqual(rows[0]["color"], "#F2994A")
        self.assertEqual(rows[0]["criterion"], BadgeCriterion.COURSES_CREATED)
        self.assertEqual(rows[0]["criterion_label"], "Courses created")
        self.assertEqual(
            rows[0]["requirement_summary"], "For creators who have created 100 courses"
        )

    def test_deleted_badges_are_hidden(self):
        make_badge(title="Gone", is_deleted=True)

        response = self.client.get(URL)

        self.assertEqual(response.data["data"]["results"], [])

    def test_page_size_param_is_honoured(self):
        for _ in range(3):
            make_badge()

        response = self.client.get(URL, {"size": 2, "page": 2})

        self.assertEqual(response.data["data"]["paginator"]["count"], 3)
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_query_count_does_not_grow_with_badges(self):
        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(URL)
            return len(ctx.captured_queries)

        badge = make_badge()
        make_award(badge=badge, creator=make_user())
        one = count_queries()
        for _ in range(4):
            make_award(badge=make_badge(), creator=make_user())
        self.assertEqual(count_queries(), one)


class BadgeCreateTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(self.admin)

    def test_create_returns_the_badge_and_audits_it(self):
        response = self.client.post(URL, _valid_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        data = response.data["data"]
        self.assertEqual(data["title"], "Top")
        self.assertEqual(data["required_count"], 3)
        self.assertFalse(data["auto_award"])
        self.assertEqual(data["holder_count"], 0)
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.admin, action="BADGE_CREATED"
            ).exists()
        )

    def test_criterion_defaults_to_courses_created(self):
        payload = _valid_payload()
        del payload["criterion"]

        response = self.client.post(URL, payload, format="json")

        self.assertEqual(
            response.data["data"]["criterion"], BadgeCriterion.COURSES_CREATED
        )

    def test_auto_award_backfills_creators_who_already_qualify(self):
        qualifies = make_user(role=UserRole.COURSE_CREATOR)
        writer = make_user(role=UserRole.STAFF_WRITER)
        short = make_user(role=UserRole.COURSE_CREATOR)
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        for owner, courses in ((qualifies, 3), (writer, 4), (short, 2), (reviewer, 5)):
            for _ in range(courses):
                make_draft_course(creator=owner)

        response = self.client.post(URL, _valid_payload(auto_award=True), format="json")

        self.assertEqual(response.data["data"]["holder_count"], 2)
        holders = set(
            CreatorBadge.objects.filter(is_deleted=False).values_list(
                "creator_id", flat=True
            )
        )
        self.assertEqual(holders, {qualifies.id, writer.id})
        self.assertTrue(
            Notification.objects.filter(
                receiver=qualifies, title="You earned a badge"
            ).exists()
        )
        self.assertFalse(Notification.objects.filter(receiver=short).exists())

    def test_invalid_payloads_are_400(self):
        for field, value in (
            ("color", "orange"),
            ("required_count", 0),
            ("criterion", "NOPE"),
        ):
            with self.subTest(field=field):
                response = self.client.post(
                    URL, _valid_payload(**{field: value}), format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Badge.objects.exists())

    def test_duplicate_title_ignoring_case_is_409(self):
        make_badge(title="Top", required_count=50)

        response = self.client.post(URL, _valid_payload(title="TOP"), format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_duplicate_criterion_and_count_is_409(self):
        make_badge(title="Other", required_count=3)

        response = self.client.post(URL, _valid_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_a_deleted_badge_frees_its_title_and_rung(self):
        make_badge(title="Top", required_count=3, is_deleted=True)

        response = self.client.post(URL, _valid_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class BadgeDetailTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))

    def test_retrieve(self):
        badge = make_badge(title="Top")

        response = self.client.get(detail_url(badge))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["id"], str(badge.id))

    def test_unknown_and_deleted_badges_are_404(self):
        deleted = make_badge(is_deleted=True)
        for url in (f"{URL}00000000-0000-0000-0000-000000000000/", detail_url(deleted)):
            with self.subTest(url=url):
                self.assertEqual(
                    self.client.get(url).status_code, status.HTTP_404_NOT_FOUND
                )
                self.assertEqual(
                    self.client.patch(url, {"title": "x"}, format="json").status_code,
                    status.HTTP_404_NOT_FOUND,
                )
                self.assertEqual(
                    self.client.delete(url).status_code, status.HTTP_404_NOT_FOUND
                )


class BadgeUpdateTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(self.admin)

    def test_edit_changes_only_what_was_sent(self):
        badge = make_badge(
            title="Top", icon="diamond", color="#F2994A", required_count=100
        )

        response = self.client.patch(
            detail_url(badge),
            {"title": "Top Creator", "color": "#000000"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["title"], "Top Creator")
        self.assertEqual(data["color"], "#000000")
        self.assertEqual(data["icon"], "diamond")
        self.assertEqual(data["required_count"], 100)
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.admin, action="BADGE_UPDATED"
            ).exists()
        )

    def test_empty_body_is_400(self):
        badge = make_badge()

        response = self.client.patch(detail_url(badge), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_criterion_is_not_editable(self):
        badge = make_badge(criterion=BadgeCriterion.COURSES_CREATED)

        self.client.patch(
            detail_url(badge),
            {"criterion": BadgeCriterion.COURSES_PUBLISHED, "title": "Renamed"},
            format="json",
        )

        badge.refresh_from_db()
        self.assertEqual(badge.criterion, BadgeCriterion.COURSES_CREATED)
        self.assertEqual(badge.title, "Renamed")

    def test_configure_onto_another_badges_rung_is_409(self):
        make_badge(required_count=10)
        badge = make_badge(required_count=20)

        response = self.client.patch(
            detail_url(badge), {"required_count": 10}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_lowering_the_requirement_awards_creators_who_now_qualify(self):
        creator = make_user()
        for _ in range(2):
            make_draft_course(creator=creator)
        badge = make_badge(required_count=5, auto_award=True)

        response = self.client.patch(
            detail_url(badge), {"required_count": 2}, format="json"
        )

        self.assertEqual(response.data["data"]["holder_count"], 1)
        self.assertTrue(
            CreatorBadge.objects.filter(badge=badge, creator=creator).exists()
        )

    def test_switching_auto_award_on_awards_creators_who_already_qualify(self):
        creator = make_user()
        make_draft_course(creator=creator)
        badge = make_badge(required_count=1, auto_award=False)

        self.client.patch(detail_url(badge), {"auto_award": True}, format="json")

        self.assertTrue(
            CreatorBadge.objects.filter(badge=badge, creator=creator).exists()
        )

    def test_raising_the_requirement_never_revokes(self):
        creator = make_user()
        badge = make_badge(required_count=1, auto_award=True)
        make_award(badge=badge, creator=creator, source=AwardSource.AUTOMATIC)

        response = self.client.patch(
            detail_url(badge), {"required_count": 50}, format="json"
        )

        self.assertEqual(response.data["data"]["holder_count"], 1)


class BadgeDeleteTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.SUPER_ADMIN)
        self.client.force_authenticate(self.admin)
        self.rising = make_badge(title="Rising", required_count=10)
        self.professional = make_badge(title="Professional", required_count=50)
        self.top = make_badge(title="Top", required_count=100)

    def test_deletion_impact_names_the_previous_badge(self):
        make_award(badge=self.top, creator=make_user())

        response = self.client.get(f"{detail_url(self.top)}deletion-impact/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["holder_count"], 1)
        self.assertEqual(data["previous_badge"]["id"], str(self.professional.id))

    def test_deletion_impact_previous_is_null_on_the_lowest_rung(self):
        response = self.client.get(f"{detail_url(self.rising)}deletion-impact/")

        self.assertIsNone(response.data["data"]["previous_badge"])

    def test_previous_badge_ignores_other_criteria(self):
        make_badge(
            title="Publisher",
            criterion=BadgeCriterion.COURSES_PUBLISHED,
            required_count=5,
        )

        response = self.client.get(f"{detail_url(self.rising)}deletion-impact/")

        self.assertIsNone(response.data["data"]["previous_badge"])

    def test_delete_without_move_removes_the_badge_from_every_holder(self):
        holders = [make_user(), make_user()]
        for holder in holders:
            make_award(badge=self.top, creator=holder)

        response = self.client.delete(detail_url(self.top))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["holders_removed"], 2)
        self.assertEqual(data["holders_moved"], 0)
        self.assertIsNone(data["moved_to_badge"])
        self.top.refresh_from_db()
        self.assertTrue(self.top.is_deleted)
        self.assertFalse(
            CreatorBadge.objects.filter(creator__in=holders, is_deleted=False).exists()
        )
        self.assertEqual(
            UserActivityLog.objects.filter(
                action="BADGE_REVOKED", user__in=holders
            ).count(),
            2,
        )
        self.assertTrue(
            Notification.objects.filter(
                receiver=holders[0], title="A badge was retired"
            ).exists()
        )

    def test_delete_with_move_gives_holders_the_previous_badge_once(self):
        new_holder = make_user()
        already_professional = make_user()
        make_award(badge=self.top, creator=new_holder)
        make_award(badge=self.top, creator=already_professional)
        make_award(badge=self.professional, creator=already_professional)

        response = self.client.delete(f"{detail_url(self.top)}?move_to_previous=true")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["holders_removed"], 2)
        self.assertEqual(data["holders_moved"], 1)
        self.assertEqual(data["moved_to_badge"]["id"], str(self.professional.id))
        self.assertEqual(
            CreatorBadge.objects.filter(
                badge=self.professional, creator=already_professional, is_deleted=False
            ).count(),
            1,
        )
        moved = CreatorBadge.objects.get(badge=self.professional, creator=new_holder)
        self.assertEqual(moved.source, AwardSource.CARRIED_OVER)

    def test_move_with_no_previous_badge_is_400_and_deletes_nothing(self):
        make_award(badge=self.rising, creator=make_user())

        response = self.client.delete(
            f"{detail_url(self.rising)}?move_to_previous=true"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.rising.refresh_from_db()
        self.assertFalse(self.rising.is_deleted)
        self.assertTrue(
            CreatorBadge.objects.filter(badge=self.rising, is_deleted=False).exists()
        )
