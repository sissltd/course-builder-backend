"""Awarding and revoking a badge by hand, and listing who holds it."""

from rest_framework import status
from rest_framework.test import APITestCase

from api.achievements.enums import AwardSource
from api.achievements.models import CreatorBadge
from api.achievements.tests.factories import make_award, make_badge
from api.courses.tests.factories import make_user
from api.notification.models import Notification
from api.users.enums import UserRole
from api.users.models import UserActivityLog

URL = "/api/v1/admin/achievements/badges/"


def holders_url(badge):
    return f"{URL}{badge.id}/holders/"


class BadgeHolderTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(self.admin)
        self.badge = make_badge(title="Top")

    def test_holder_list_shape(self):
        creator = make_user(first_name="Osaite", last_name="Emmanuel")
        make_award(badge=self.badge, creator=creator, awarded_by=self.admin)
        make_award(badge=self.badge, creator=make_user(), is_deleted=True)

        response = self.client.get(holders_url(self.badge))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["paginator"]["count"], 1)
        row = response.data["data"]["results"][0]
        self.assertEqual(row["creator"]["id"], str(creator.id))
        self.assertEqual(row["creator"]["full_name"], "Osaite Emmanuel")
        self.assertEqual(row["source"], AwardSource.MANUAL)
        self.assertEqual(row["awarded_by_email"], self.admin.email)

    def test_award_by_hand(self):
        creator = make_user(role=UserRole.STAFF_WRITER)

        response = self.client.post(
            holders_url(self.badge), {"creator_id": str(creator.id)}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["source"], AwardSource.MANUAL)
        award = CreatorBadge.objects.get(badge=self.badge, creator=creator)
        self.assertEqual(award.awarded_by, self.admin)
        self.assertTrue(Notification.objects.filter(receiver=creator).exists())
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=creator, actor_user=self.admin, action="BADGE_AWARDED"
            ).exists()
        )

    def test_award_to_a_non_creator_or_unknown_user_is_404(self):
        for creator_id in (
            str(make_user(role=UserRole.CREATOR_REVIEWER).id),
            "00000000-0000-0000-0000-000000000000",
        ):
            with self.subTest(creator_id=creator_id):
                response = self.client.post(
                    holders_url(self.badge), {"creator_id": creator_id}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(CreatorBadge.objects.exists())

    def test_award_twice_is_409(self):
        creator = make_user()
        make_award(badge=self.badge, creator=creator)

        response = self.client.post(
            holders_url(self.badge), {"creator_id": str(creator.id)}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_award_body_must_carry_a_uuid(self):
        response = self.client.post(
            holders_url(self.badge), {"creator_id": "x"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_revoke(self):
        creator = make_user()
        make_award(badge=self.badge, creator=creator)

        response = self.client.delete(f"{holders_url(self.badge)}{creator.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            CreatorBadge.objects.filter(
                badge=self.badge, creator=creator, is_deleted=False
            ).exists()
        )
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=creator, action="BADGE_REVOKED"
            ).exists()
        )

    def test_revoke_a_badge_not_held_is_404(self):
        response = self.client.delete(f"{holders_url(self.badge)}{make_user().id}/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_approver_is_refused(self):
        creator = make_user()
        make_award(badge=self.badge, creator=creator)
        self.client.force_authenticate(make_user(role=UserRole.STAFF_APPROVER))

        self.assertEqual(
            self.client.get(holders_url(self.badge)).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.delete(f"{holders_url(self.badge)}{creator.id}/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
