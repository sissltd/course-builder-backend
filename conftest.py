"""Root pytest fixtures — shared across every app.

New tests can use these instead of writing another `make_user`. The
existing suite (Django `TestCase` / `APITestCase` subclasses, 92 files) and
their per-app `make_user` helper functions (`api/*/tests/factories.py`,
etc.) are untouched — this is a shared starting point for new pytest-style
tests, not a rewrite of the suite.
"""

import pytest
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from api.users.enums import UserRole
from core.testing import transaction_teardown_with_reseed

# TransactionTestCase truncates every table on teardown, including rows
# written by data migrations. Django's runner built the database fresh each
# run, so a class that forgot to restore them only broke whatever ran after
# it; with --reuse-db the wiped state is inherited by the *next* run, where
# every test asserting on seeded rows fails. Patching the base class means a
# new TransactionTestCase cannot reintroduce this by omission - which is how
# api.webhooks.tests.test_youverify silently wiped the database for a while.
if not hasattr(TransactionTestCase, "_fixture_teardown_original"):
    TransactionTestCase._fixture_teardown_original = (
        TransactionTestCase._fixture_teardown
    )
    TransactionTestCase._fixture_teardown = transaction_teardown_with_reseed


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def make_user(db):
    from itertools import count

    from django.contrib.auth import get_user_model

    User = get_user_model()
    counter = count(1)

    def _make_user(role=UserRole.COURSE_CREATOR, **kwargs):
        n = next(counter)
        defaults = {
            "email": f"pytest-user{n}@example.com",
            "first_name": "Test",
            "last_name": "User",
            "role": role,
        }
        defaults.update(kwargs)
        return User.objects.create_user(password="testpass123", **defaults)

    return _make_user
