from django.test import TestCase

from api.onboarding.enums import MonthlyCourseCapacity, VideoComfortLevel
from api.onboarding.models import CreatorProfile
from api.onboarding.services import creator_profile_service
from api.onboarding.tests.factories import make_category, make_user
from api.users.models import UserActivityLog


class GetOrCreateProfileTests(TestCase):
    def test_creates_exactly_one_row_and_reuses_it(self):
        user = make_user()

        profile1 = creator_profile_service.get_or_create_profile(user=user)
        profile2 = creator_profile_service.get_or_create_profile(user=user)

        self.assertEqual(profile1.id, profile2.id)
        self.assertEqual(CreatorProfile.objects.filter(user=user).count(), 1)


class UpdateProfileTests(TestCase):
    def test_category_id_sets_primary_expertise_category(self):
        user = make_user()
        category = make_category()

        profile = creator_profile_service.update_profile(user=user, category_id=category)

        self.assertEqual(profile.primary_expertise_category_id, category.id)

    def test_only_touches_provided_fields_across_separate_calls(self):
        user = make_user()

        creator_profile_service.update_profile(
            user=user, video_comfort_level=VideoComfortLevel.VERY_COMFORTABLE
        )
        profile = creator_profile_service.update_profile(
            user=user, monthly_course_capacity=MonthlyCourseCapacity.TWO_TO_THREE
        )

        self.assertEqual(profile.video_comfort_level, VideoComfortLevel.VERY_COMFORTABLE)
        self.assertEqual(profile.monthly_course_capacity, MonthlyCourseCapacity.TWO_TO_THREE)
        self.assertIsNone(profile.onboarding_completed_at)

    def test_agreement_accepted_sets_both_timestamps_and_logs_activity(self):
        user = make_user()

        profile = creator_profile_service.update_profile(user=user, agreement_accepted=True)

        self.assertIsNotNone(profile.agreement_accepted_at)
        self.assertIsNotNone(profile.onboarding_completed_at)
        self.assertTrue(profile.has_completed_onboarding)
        self.assertTrue(
            UserActivityLog.objects.filter(user=user, action="ONBOARDING_COMPLETED").exists()
        )

    def test_full_wizard_across_four_calls_ends_completed(self):
        user = make_user()
        category = make_category()

        creator_profile_service.update_profile(user=user, category_id=category)
        creator_profile_service.update_profile(
            user=user, video_comfort_level=VideoComfortLevel.NEEDS_GUIDANCE
        )
        creator_profile_service.update_profile(
            user=user, monthly_course_capacity=MonthlyCourseCapacity.ONE
        )
        profile = creator_profile_service.update_profile(user=user, agreement_accepted=True)

        self.assertEqual(profile.primary_expertise_category_id, category.id)
        self.assertEqual(profile.video_comfort_level, VideoComfortLevel.NEEDS_GUIDANCE)
        self.assertEqual(profile.monthly_course_capacity, MonthlyCourseCapacity.ONE)
        self.assertTrue(profile.has_completed_onboarding)
