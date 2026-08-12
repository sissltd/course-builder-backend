from django.test import TestCase

from api.onboarding.enums import ExpertiseArea, MonthlyCourseCapacity, VideoComfortLevel
from api.onboarding.models import CreatorProfile
from api.onboarding.services import creator_profile_service
from api.onboarding.tests.factories import make_user
from api.platform.services import platform_settings_service
from api.users.models import UserActivityLog


class GetOrCreateProfileTests(TestCase):
    def test_creates_exactly_one_row_and_reuses_it(self):
        user = make_user()

        profile1 = creator_profile_service.get_or_create_profile(user=user)
        profile2 = creator_profile_service.get_or_create_profile(user=user)

        self.assertEqual(profile1.id, profile2.id)
        self.assertEqual(CreatorProfile.objects.filter(user=user).count(), 1)


class UpdateProfileTests(TestCase):
    def test_expertise_area_sets_primary_expertise_area(self):
        user = make_user()

        profile = creator_profile_service.update_profile(
            user=user, expertise_area=ExpertiseArea.WEB_DEVELOPMENT
        )

        self.assertEqual(profile.primary_expertise_area, ExpertiseArea.WEB_DEVELOPMENT)

    def test_only_touches_provided_fields_across_separate_calls(self):
        user = make_user()

        creator_profile_service.update_profile(
            user=user, video_comfort_level=VideoComfortLevel.VERY_COMFORTABLE
        )
        profile = creator_profile_service.update_profile(
            user=user, monthly_course_capacity=MonthlyCourseCapacity.TWO_TO_THREE
        )

        self.assertEqual(
            profile.video_comfort_level, VideoComfortLevel.VERY_COMFORTABLE
        )
        self.assertEqual(
            profile.monthly_course_capacity, MonthlyCourseCapacity.TWO_TO_THREE
        )
        self.assertIsNone(profile.onboarding_completed_at)

    def test_agreement_accepted_sets_both_timestamps_and_logs_activity(self):
        user = make_user()

        profile = creator_profile_service.update_profile(
            user=user, agreement_accepted=True
        )

        self.assertIsNotNone(profile.agreement_accepted_at)
        self.assertIsNotNone(profile.onboarding_completed_at)
        self.assertTrue(profile.has_completed_onboarding)
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=user, action="ONBOARDING_COMPLETED"
            ).exists()
        )

    def test_expertise_update_logs_onboarding_step_updated(self):
        user = make_user()

        creator_profile_service.update_profile(
            user=user, expertise_area=ExpertiseArea.WEB_DEVELOPMENT
        )

        log = UserActivityLog.objects.get(
            user=user, action="ONBOARDING_STEP_UPDATED"
        )
        self.assertEqual(log.category, "ONBOARDING")
        self.assertEqual(log.details["updated_fields"], ["expertise_area"])

    def test_multiple_fields_in_one_call_log_a_single_entry(self):
        user = make_user()

        creator_profile_service.update_profile(
            user=user,
            video_comfort_level=VideoComfortLevel.VERY_COMFORTABLE,
            monthly_course_capacity=MonthlyCourseCapacity.ONE,
        )

        self.assertEqual(
            UserActivityLog.objects.filter(
                user=user, action="ONBOARDING_STEP_UPDATED"
            ).count(),
            1,
        )

    def test_first_time_completion_logs_policy_accepted_and_access_granted_too(self):
        user = make_user()

        profile = creator_profile_service.update_profile(
            user=user, agreement_accepted=True
        )

        self.assertTrue(profile.is_first_completion)
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=user, action="POLICY_ACCEPTED"
            ).exists()
        )
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=user, action="COURSE_BUILDER_ACCESS_GRANTED"
            ).exists()
        )

    def test_agreement_accepted_stamps_current_policy_version(self):
        platform_settings_service.update_settings(creator_agreement_policy_version="2.0")
        user = make_user()

        profile = creator_profile_service.update_profile(
            user=user, agreement_accepted=True
        )

        self.assertEqual(profile.agreement_accepted_version, "2.0")

    def test_reaccepting_after_policy_bump_does_not_reset_completion(self):
        user = make_user()
        first = creator_profile_service.update_profile(
            user=user, agreement_accepted=True
        )
        original_completed_at = first.onboarding_completed_at

        platform_settings_service.update_settings(creator_agreement_policy_version="2.0")
        self.assertTrue(
            CreatorProfile.objects.get(user=user).needs_policy_reacceptance
        )

        second = creator_profile_service.update_profile(
            user=user, agreement_accepted=True
        )

        self.assertFalse(second.is_first_completion)
        self.assertEqual(second.onboarding_completed_at, original_completed_at)
        self.assertEqual(second.agreement_accepted_version, "2.0")
        self.assertFalse(second.needs_policy_reacceptance)
        self.assertEqual(
            UserActivityLog.objects.filter(
                user=user, action="POLICY_ACCEPTED"
            ).count(),
            2,
        )
        self.assertEqual(
            UserActivityLog.objects.filter(
                user=user, action="ONBOARDING_COMPLETED"
            ).count(),
            1,
        )
        self.assertEqual(
            UserActivityLog.objects.filter(
                user=user, action="COURSE_BUILDER_ACCESS_GRANTED"
            ).count(),
            1,
        )

    def test_full_wizard_across_four_calls_ends_completed(self):
        user = make_user()

        creator_profile_service.update_profile(
            user=user, expertise_area=ExpertiseArea.WEB_DEVELOPMENT
        )
        creator_profile_service.update_profile(
            user=user, video_comfort_level=VideoComfortLevel.NEEDS_GUIDANCE
        )
        creator_profile_service.update_profile(
            user=user, monthly_course_capacity=MonthlyCourseCapacity.ONE
        )
        profile = creator_profile_service.update_profile(
            user=user, agreement_accepted=True
        )

        self.assertEqual(profile.primary_expertise_area, ExpertiseArea.WEB_DEVELOPMENT)
        self.assertEqual(profile.video_comfort_level, VideoComfortLevel.NEEDS_GUIDANCE)
        self.assertEqual(profile.monthly_course_capacity, MonthlyCourseCapacity.ONE)
        self.assertTrue(profile.has_completed_onboarding)
