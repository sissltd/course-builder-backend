from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from api.courses.exceptions import ModuleLocked
from api.courses.models import Module
from api.courses.services import module_lock_service
from api.courses.tests.factories import make_draft_course, make_user


def make_module(**kwargs):
    course = kwargs.pop("course", None) or make_draft_course()
    defaults = {"course": course, "title": "Module", "order": 0}
    defaults.update(kwargs)
    return Module.objects.create(**defaults)


class AcquireLockTests(TestCase):
    def test_acquires_lock_when_unlocked(self):
        module = make_module()
        user = make_user()

        result = module_lock_service.acquire_lock(module=module, user=user)

        self.assertEqual(result.locked_by_id, user.id)
        self.assertTrue(result.is_locked)

    def test_raises_when_locked_by_someone_else(self):
        module = make_module()
        holder = make_user()
        other = make_user()
        module_lock_service.acquire_lock(module=module, user=holder)

        with self.assertRaises(ModuleLocked):
            module_lock_service.acquire_lock(module=module, user=other)

    def test_renews_own_lock(self):
        module = make_module()
        user = make_user()
        module_lock_service.acquire_lock(module=module, user=user, ttl_minutes=1)
        first_expiry = module.lock_expires_at

        result = module_lock_service.acquire_lock(module=module, user=user, ttl_minutes=10)

        self.assertGreater(result.lock_expires_at, first_expiry)

    def test_can_acquire_after_expiry(self):
        module = make_module()
        holder = make_user()
        other = make_user()
        module_lock_service.acquire_lock(module=module, user=holder)
        module.lock_expires_at = timezone.now() - timedelta(minutes=1)
        module.save(update_fields=["lock_expires_at"])

        result = module_lock_service.acquire_lock(module=module, user=other)

        self.assertEqual(result.locked_by_id, other.id)


class ReleaseLockTests(TestCase):
    def test_releases_own_lock(self):
        module = make_module()
        user = make_user()
        module_lock_service.acquire_lock(module=module, user=user)

        result = module_lock_service.release_lock(module=module, user=user)

        self.assertIsNone(result.locked_by_id)
        self.assertFalse(result.is_locked)

    def test_raises_when_releasing_someone_elses_lock(self):
        module = make_module()
        holder = make_user()
        other = make_user()
        module_lock_service.acquire_lock(module=module, user=holder)

        with self.assertRaises(PermissionDenied):
            module_lock_service.release_lock(module=module, user=other)

    def test_noop_when_not_locked(self):
        module = make_module()
        user = make_user()

        result = module_lock_service.release_lock(module=module, user=user)

        self.assertIsNone(result.locked_by_id)


class HeartbeatLockTests(TestCase):
    def test_extends_active_lock(self):
        module = make_module()
        user = make_user()
        module_lock_service.acquire_lock(module=module, user=user, ttl_minutes=1)
        first_expiry = module.lock_expires_at

        result = module_lock_service.heartbeat_lock(
            module=module, user=user, ttl_minutes=10
        )

        self.assertGreater(result.lock_expires_at, first_expiry)

    def test_raises_when_not_held_by_caller(self):
        module = make_module()
        holder = make_user()
        other = make_user()
        module_lock_service.acquire_lock(module=module, user=holder)

        with self.assertRaises(ModuleLocked):
            module_lock_service.heartbeat_lock(module=module, user=other)

    def test_raises_when_lock_expired(self):
        module = make_module()
        user = make_user()
        module_lock_service.acquire_lock(module=module, user=user)
        module.lock_expires_at = timezone.now() - timedelta(minutes=1)
        module.save(update_fields=["lock_expires_at"])

        with self.assertRaises(ModuleLocked):
            module_lock_service.heartbeat_lock(module=module, user=user)


class CheckNotLockedTests(TestCase):
    def test_passes_when_unlocked(self):
        module = make_module()
        user = make_user()

        module_lock_service.check_not_locked(module=module, user=user)

    def test_passes_for_lock_holder(self):
        module = make_module()
        user = make_user()
        module_lock_service.acquire_lock(module=module, user=user)

        module_lock_service.check_not_locked(module=module, user=user)

    def test_raises_for_non_holder(self):
        module = make_module()
        holder = make_user()
        other = make_user()
        module_lock_service.acquire_lock(module=module, user=holder)

        with self.assertRaises(ModuleLocked):
            module_lock_service.check_not_locked(module=module, user=other)
